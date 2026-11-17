"""学习通考试：只读目录，复用登录 Cookie，由官方页面保存和交卷。

页面结构与只读接口核对来源见 docs/EXAM_PROTOCOL.md。考试的动态签名由页面
原有点击处理器生成；不把章节作业表单发到考试接口，也不修改平台计时或验证参数。
"""
import copy
import re
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from base.auth import AuthenticationExpired, PlatformUnavailable
from base.quiet_hours import checkpoint
from ChaoxingMain.api import ChaoxingError, MOBILE_BASE, MOBILE_HEADERS, WorkUnavailable
from ChaoxingMain.decode import QUESTION_TYPES, _all, _document, _one, _title_text
from ChaoxingMain.exercise import ChaoxingExerciseRunner, prompt_title
from ChaoxingMain.font import FontDecodeError, FontDecoder, encrypted
from task_capabilities import task_availability

LIST_URL = MOBILE_BASE + "/exam/phone/task-list"
EXAM_BASE = MOBILE_BASE + "/exam-ans/exam/"
QUESTION = "div.questionWrap.singleQuesId.ans-cc-exam"
SUBMIT_PATH = "/exam-ans/exam/test/reVersionSubmitTestNew"


class ExamNeedsUser(ChaoxingError):
    """需在学习通完成验证、开考或处理未知页面。"""


def exam_url(value):
    """只接受官方考试页，避免把无域 Cookie 发到目录里的任意地址。"""
    try:
        if not isinstance(value, str) or not value:
            raise ValueError
        url = urljoin(EXAM_BASE, value)
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in ("mooc1-api.chaoxing.com", "mooc1.chaoxing.com")
                or parts.port not in (None, 443) or parts.username or parts.password
                or not parts.path.startswith("/exam-ans/exam/")):
            raise ValueError
    except ValueError:
        raise ChaoxingError("考试地址无法识别，请在学习通核对") from None
    return url


def _guard(text):
    root = _document(text)
    if root is None:
        raise ChaoxingError("考试页面为空")
    if root.xpath('.//input[@type="password"]'):
        raise AuthenticationExpired("学习通登录已过期，请重新登录")
    for name in ("faceRecognitionCompare", "captchaCheck"):
        values = root.xpath('.//input[@id=$name]/@value', name=name)
        if values and values[0].strip().lower() not in ("", "0", "false"):
            raise ExamNeedsUser("考试要求身份或验证码验证，请先到学习通完成验证")
    if re.search(r"\bneedcode\s*=\s*['\"]?[1-9]", text):
        raise ExamNeedsUser("考试要求考试码，请先到学习通输入考试码并开考")
    tips = _all(root, "p", "blankTips") + _all(root, "li", "msg")
    if tips:
        raise ExamNeedsUser(tips[0].text_content().strip() or "请到学习通查看考试要求")
    return root


def decode_exam_list(text, course):
    root = _guard(text)
    lists = _all(root, "ul", "nav")
    if not lists:
        if any(word in root.text_content() for word in ("暂无考试", "没有考试", "暂无数据")):
            return []
        raise ChaoxingError("考试目录格式无法识别，请刷新或到学习通核对")
    entries, seen = [], set()
    for node in lists[0].xpath('./li'):
        url = exam_url(node.get("data") or "")
        params = parse_qs(urlsplit(url).query)
        eid = params.get("taskrefId", [""])[0]
        if (not eid.isdigit() or len(params.get("taskrefId", [])) != 1
                or len(params.get("enc_task", [])) != 1 or eid in seen):
            raise ChaoxingError("考试目录缺少唯一标识或校验参数")
        for key, expected in (("courseId", course["courseId"]), ("classId", course["clazzId"]), ("cpi", course["cpi"])):
            if key in params and params[key] != [str(expected)]:
                raise ChaoxingError("考试目录与当前课程不符")
        names = node.xpath('.//p')
        states = node.xpath('.//span[not(contains(concat(" ", normalize-space(@class), " "), " fr "))]')
        if not names or not states:
            raise ChaoxingError("考试目录缺少名称或状态")
        name, status = names[0].text_content().strip(), states[0].text_content().strip()
        if not name:
            raise ChaoxingError("考试目录缺少名称")
        done = status in ("已完成", "已提交", "已交卷")
        item = {"id": "exam-" + eid, "exam_id": eid, "name": name, "_exam_url": url,
                "_completion_status": "submitted" if done else "not_started",
                "_hint": " · ".join(n.text_content().strip() for n in _all(node, "span", "fr"))}
        if done or status not in ("未交", "待做", "待考试", "可以参加", "正在作答", "考试中", "进行中"):
            item["_unavailable"] = ("已提交" if done else status or "状态未知", "考试%s，请在学习通核对。" % (status or "状态未知"))
        entries.append((course, item, 2))
        seen.add(eid)
    return entries


def catalog(client, course):
    response = client._request("get", LIST_URL, headers=MOBILE_HEADERS, params={
        "courseId": course["courseId"], "classId": course["clazzId"], "cpi": course["cpi"]})
    return decode_exam_list(response.text, course)


def decode_exam_questions(text):
    root = _guard(text)
    decoder = FontDecoder(text) if encrypted(text) else None
    questions, seen = [], set()
    for node in _all(root, "div", "singleQuesId"):
        ids = node.xpath('.//input[@name="questionId"]/@value')
        if len(ids) != 1 or not ids[0].isdigit() or ids[0] in seen:
            raise ChaoxingError("考试题目编号缺失或重复，未保存")
        qid = ids[0]
        types = node.xpath('.//input[@name=$name]/@value', name="type" + qid)
        title = _one(node, "div", "tit")
        if len(types) != 1 or title is None:
            raise ChaoxingError("考试题目结构无法识别，未保存")
        title = copy.deepcopy(title)
        for meta in title.xpath('.//h3') + _all(title, "span", "grayTit"):
            meta.drop_tree()
        kind = QUESTION_TYPES.get(types[0], "unknown")
        options = []
        if kind in ("single", "multiple"):
            for option in _all(node, "div", "radioList"):
                key = option.get("name", "")
                content = option.xpath('.//cc')
                label = _one(option, "span", "No")
                label = label.text_content().strip().rstrip(".、．") if label is not None else ""
                if not re.fullmatch("[A-Z]", key) or not re.fullmatch("[A-Z]", label) or len(content) != 1:
                    raise ChaoxingError("考试选项结构无法识别，未保存")
                options.append({"label": label, "value": key, "text": _title_text(content[0], decoder)})
            if len({o["value"] for o in options}) != len(options):
                raise ChaoxingError("考试选项提交值重复，未保存")
        controls = node.xpath('.//input[@id=$name] | .//textarea[@name=$name]', name="answer" + qid)
        existing = None
        if len(controls) == 1:
            existing = controls[0].text_content() if controls[0].tag == "textarea" else controls[0].get("value", "")
        question = {"id": qid, "type": kind, "type_code": types[0], "title": prompt_title(_title_text(title, decoder)),
                    "options": options, "existing_answer": existing}
        if kind == "completion":
            blanks = _all(node, "textarea", "blanktextarea")
            if blanks:
                question.update(blank_count=len(blanks), existing_answer=[n.text_content() for n in blanks])
        if question["existing_answer"] is None:
            # 保留未知题型用于覆盖率；未理解的已有答案绝不能写回。
            question["type"] = "unknown"
            question["existing_answer"] = ""
        if kind in ("single", "multiple") and existing and any(v not in {o["value"] for o in options} for v in existing):
            raise ChaoxingError("考试已有答案无法识别，未保存")
        if kind == "judgement" and existing not in (None, "", "true", "false"):
            raise ChaoxingError("考试已有判断答案无法识别，未保存")
        questions.append(question)
        seen.add(qid)
    if not questions:
        raise ChaoxingError("没有读取到考试题目，请先在学习通进入考试")
    return questions


class ChaoxingExamRunner(ChaoxingExerciseRunner):
    """一场考试一个浏览器；答案复用现有 AI 流程，提交不自动重试。"""

    def run_paper(self, course, item, category=2):
        from playwright.sync_api import Error

        name = item["name"]
        self._pw = self._browser = self.page = None
        self.deadline = None
        self.course, self.item = course, item
        self.writing = False
        try:
            fresh = next((entry[1] for entry in catalog(self.client, course) if entry[1]["id"] == item["id"]), None)
            if fresh is None:
                raise WorkUnavailable("考试已不在目录中，请刷新列表")
            availability = task_availability(fresh, 2)
            if not availability.available:
                raise WorkUnavailable(availability.reason)
            self._open(fresh["_exam_url"])
            questions = self._questions()
            values, problems = self._answer_all(name, questions, check=self._check)
            saved = 0
            for index, question in enumerate(questions):
                self._check()
                value = values.get(question["id"])
                if value is None:
                    continue
                if value != question["existing_answer"]:
                    self._goto_question(index)
                    (current,) = decode_exam_questions(self._html())
                    if current != question:
                        raise ExamNeedsUser("作答过程中题目或已有答案发生变化，请在学习通核对")
                    self._fill(question, value)
                    self._submit(False)
                saved += 1
                self.reporter.detail("已保存 %d/%d 题" % (saved, len(questions)))
            # 已填答案也要回读；不根据 HTTP 200 或页面按钮点击推断成功。
            verified = self._questions()
            if [q["id"] for q in verified] != [q["id"] for q in questions] or any(
                    q["existing_answer"] != values[q["id"]] for q in verified if q["id"] in values):
                raise ExamNeedsUser("平台保存的答案与本次作答不一致，未交卷，请到学习通核对")
            if self.auto_submit and saved == len(questions):
                self._submit(True)
                # 交卷 POST 只发一次；之后只读目录确认结果。
                completed = next((e[1] for e in catalog(self.client, course) if e[1]["id"] == item["id"]), {})
                if completed.get("_completion_status") != "submitted":
                    return self._result(name, "submission_unknown", "平台尚未确认已交卷，请在学习通核对；不会自动重交")
                self.writing = False
                return self._result(name, "submitted", "考试已交卷 · %d/%d 题" % (saved, len(questions)))
            reasons = "；".join(dict.fromkeys(problems.values()))
            return self._result(name, "saved" if saved else "error", "已保存 %d/%d 题，未交卷%s" % (
                saved, len(questions), "；" + reasons if reasons else ""))
        except (ExamNeedsUser, AuthenticationExpired):
            raise
        except WorkUnavailable as exc:
            return self._result(name, "skipped", str(exc))
        except (Error, ChaoxingError, PlatformUnavailable, FontDecodeError) as exc:
            status = "submission_unknown" if self.writing else "error"
            message = "保存或交卷结果未知，请到学习通核对，不会自动重试" if self.writing else str(exc)
            if isinstance(exc, Error) and not self.writing:
                message = "考试页面未能加载或控件已变化，请到学习通核对；本机需安装 Chromium（uv run playwright install --only-shell chromium）"
            return self._result(name, status, message)
        finally:
            for close in (getattr(self._browser, "close", None), getattr(self._pw, "stop", None)):
                if close:
                    try:
                        close()
                    except Exception:
                        pass

    def _check(self):
        checkpoint()
        if self.cancel_event.is_set():
            raise WorkUnavailable("已停止，未继续保存或交卷；已保存的答案保留在学习通")
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ExamNeedsUser("考试剩余时间已用完，已停止保存和交卷，请在学习通查看结果")

    def _open(self, url):
        from playwright.sync_api import sync_playwright

        self._check()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        context = self._browser.new_context(user_agent=MOBILE_HEADERS["User-Agent"])
        cookies = []
        for c in self.client.session.cookies:
            domain = c.domain or ".chaoxing.com"
            if domain.lstrip(".") == "chaoxing.com" or domain.endswith(".chaoxing.com"):
                cookies.append({"name": c.name, "value": c.value, "domain": domain, "path": c.path or "/", "secure": True})
        context.add_cookies(cookies)
        self.page = context.new_page()
        self.page.set_default_timeout(15000)
        self.page.goto(exam_url(url), wait_until="domcontentloaded")
        root = _guard(self.page.content())
        if not root.xpath('.//form[@id="submitTest"]'):
            if any(word in self.page.locator("body").inner_text() for word in ("承诺书", "诚信承诺", "人脸识别", "考试客户端", "指定的IP")):
                raise ExamNeedsUser("请先在学习通完成开考要求，再回来刷新考试列表")
            self._click(("开始考试", "进入考试", "继续考试", "开始答题"))
            self.page.locator("form#submitTest").wait_for()
        fields = self._session()
        remaining = int(fields["remainTime"])
        self.reporter.detail("已进入考试 · 剩余 %d 分 %d 秒" % divmod(remaining, 60))

    def _session(self):
        self._check()
        if urlsplit(self.page.url).hostname == "passport2.chaoxing.com":
            raise AuthenticationExpired("学习通登录已过期，请重新登录")
        exam_url(self.page.url)
        root = _guard(self._html())
        forms = root.xpath('.//form[@id="submitTest"]')
        if len(forms) != 1:
            raise ExamNeedsUser("未进入可作答试卷，请先在学习通开考")
        fields = {n.get("id") or n.get("name"): n.get("value", "") for n in forms[0].xpath('.//input')}
        for key, expected in (("courseId", self.course["courseId"]), ("classId", self.course["clazzId"]),
                              ("testPaperId", self.item["exam_id"])):
            if fields.get(key) != str(expected):
                raise ChaoxingError("试卷身份与所选考试不符，未保存")
        if any(not fields.get(k) for k in ("testUserRelationId", "enc", "encRemainTime", "encLastUpdateTime")):
            raise ChaoxingError("考试会话参数缺失，未保存")
        remaining = fields.get("remainTime", "")
        if not remaining.isdigit():
            raise ChaoxingError("无法读取考试剩余时间，未保存")
        deadline = time.monotonic() + int(remaining)
        self.deadline = min(self.deadline, deadline) if self.deadline is not None else deadline
        self._check()
        return fields

    def _html(self):
        # input.value / textarea.value 不会同步回 HTML 属性；只在副本中序列化当前值。
        return self.page.evaluate("""() => {
            const copy = document.documentElement.cloneNode(true);
            const live = document.querySelectorAll('input, textarea');
            copy.querySelectorAll('input, textarea').forEach((node, i) => {
                if (node.tagName === 'TEXTAREA') node.textContent = live[i].value;
                else node.setAttribute('value', live[i].value);
            });
            return copy.outerHTML;
        }""")

    def _params(self):
        fields = self._session()
        return {"courseId": self.course["courseId"], "classId": self.course["clazzId"], "cpi": self.course["cpi"],
                "examRelationId": self.item["exam_id"], "examRelationAnswerId": fields["testUserRelationId"],
                "enc": fields["enc"], "remainTimeParam": fields["encRemainTime"],
                "relationAnswerLastUpdateTime": fields["encLastUpdateTime"], "source": "0", "start": "0",
                "imei": fields.get("imei", "")}

    def _read(self, path):
        response = self.page.context.request.get(EXAM_BASE + path, params=self._params(), timeout=30000)
        if response.status != 200:
            raise ChaoxingError("考试读取失败（HTTP %s）" % response.status)
        return response.text()

    def _questions(self):
        card = _guard(self._read("phone/loadAnswerStatic"))
        indices = card.xpath('.//ul//li/@data')
        if not indices or any(not i.isdigit() for i in indices) or sorted(map(int, indices)) != list(range(len(indices))):
            raise ChaoxingError("无法确认答题卡总题数，未交卷")
        questions = decode_exam_questions(self._read("phone/preview"))
        if len(questions) != len(indices):
            raise ChaoxingError("试卷题数与答题卡不一致，未交卷")
        return questions

    def _goto_question(self, index):
        params = self._params()
        params.update(tId=params.pop("examRelationId"), id=params.pop("examRelationAnswerId"),
                      start=str(index), tag="0", isphone="true", p="1")
        self.page.goto(EXAM_BASE + "test/reVersionTestStartNew?" + urlencode(params), wait_until="domcontentloaded")
        self._session()

    def _fill(self, question, value):
        self._check()
        node = self.page.locator(QUESTION).filter(has=self.page.locator('input[name="questionId"][value="%s"]' % question["id"]))
        if node.count() != 1:
            raise ChaoxingError("无法唯一定位考试题目，未保存")
        kind = question["type"]
        if kind in ("single", "multiple", "judgement"):
            current = question["existing_answer"]
            keys = ([o["value"] for o in question["options"] if (o["value"] in value) != (o["value"] in current)]
                    if kind == "multiple" else [value])
            for key in keys:
                node.locator('div.answerList[name="%s"]' % key).click()
        elif kind == "completion":
            blanks = node.locator("textarea.blanktextarea")
            for i, part in enumerate(value):
                blanks.nth(i).fill(part)
        else:
            node.locator('textarea[name="answer%s"]' % question["id"]).fill(value)
        (actual,) = decode_exam_questions(self._html())
        if actual["existing_answer"] != value:
            raise ChaoxingError("考试页面填写结果与答案不符，未保存")

    def _click(self, labels):
        for label in labels:
            matches = self.page.get_by_text(label, exact=True)
            visible = [matches.nth(i) for i in range(matches.count()) if matches.nth(i).is_visible()]
            if not visible:
                matches = self.page.get_by_role("button", name=label, exact=True)
                visible = [matches.nth(i) for i in range(matches.count()) if matches.nth(i).is_visible()]
            if len(visible) == 1:
                self._check()
                visible[0].click()
                return
        raise ExamNeedsUser("考试页面没有唯一的「%s」按钮，请在学习通操作" % labels[0])

    def _submit(self, final):
        self._check()
        self.writing = True
        # 只有用户选择交卷时才接受原生交卷确认。其它验证由用户在平台处理。
        def dialog(d):
            if final and d.type == "confirm" and any(t in d.message for t in ("交卷", "提交试卷")):
                d.accept()
            else:
                d.dismiss()
        self.page.on("dialog", dialog)
        try:
            with self.page.expect_response(lambda r: urlsplit(r.url).path == SUBMIT_PATH and r.request.method == "POST", timeout=30000) as reply:
                self._click(("交卷", "提交试卷") if final else ("保存", "保存答案", "暂存", "下一题"))
                if final:
                    dialogs = self.page.locator('[role="dialog"], .layui-layer, .popDiv').filter(has_text=re.compile("交卷|提交试卷"))
                    for i in range(dialogs.count()):
                        box = dialogs.nth(i)
                        if box.is_visible():
                            button = box.get_by_text(re.compile(r"^(确认交卷|确定交卷|确认提交|确定|确认)$"))
                            if button.count() == 1:
                                self._check()
                                button.click()
            response = reply.value
            mode = parse_qs(urlsplit(response.url).query).get("tempSave") or parse_qs(response.request.post_data or "").get("tempSave")
            if mode != ["false" if final else "true"]:
                raise ChaoxingError("平台返回了非预期的保存/交卷操作")
            try:
                result = response.json()
            except ValueError:
                raise ChaoxingError("平台返回的保存/交卷结果无法解析") from None
            if isinstance(result, dict) and result.get("status") in ("error", "fail", False):
                self.writing = False
                raise ExamNeedsUser("平台拒绝%s：%s" % ("交卷" if final else "保存", result.get("msg") or "请在学习通核对"))
            if response.status != 200 or not isinstance(result, dict) or result.get("status") != "success":
                raise ChaoxingError("平台未确认%s，请在学习通核对" % ("交卷" if final else "保存"))
            if not final:
                updated = str(result.get("data", "")).split("|")
                if len(updated) != 3 or not updated[2]:
                    raise ChaoxingError("保存后考试会话参数缺失")
                self.page.wait_for_function("enc => document.querySelector('#submitTest #enc')?.value === enc", arg=updated[2])
            self.writing = final  # 交卷还需目录回读确认；保存则已得到确认。
        finally:
            self.page.remove_listener("dialog", dialog)
