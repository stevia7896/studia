"""离线考试检查：目录/题型、用户模型接线、真实浏览器保存与交卷。"""
import json
import tempfile
import threading
import time
import unittest
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from ai.client import AIConfig
from ChaoxingMain.api import ChaoxingApi, ChaoxingError
from ChaoxingMain.exam import (ChaoxingExamRunner, ExamNeedsUser, SUBMIT_PATH, catalog,
                              decode_exam_list, decode_exam_questions, exam_url)
from ChaoxingMain.exercise import encode_answer
from settings import SettingsStore
from web.server import HttpError, Studia, STATIC, INTERRUPTED

COURSE = {"courseId": "111", "clazzId": "222", "cpi": "333", "title": "考试测试课程"}
ENTRY = "/exam-ans/exam/phone/task-exam?taskrefId=900&enc_task=token&courseId=111&classId=222&cpi=333"
MOBILE_ENTRY = "/exam-ans/android/mtaskmsgspecial?taskrefId=900&enc_task=token&courseId=111&clazzId=222"
ITEM = {"id": "exam-900", "exam_id": "900", "name": "期中考试", "_exam_url": "https://mooc1-api.chaoxing.com" + ENTRY}


def exam_list(status="未交", entry=ENTRY):
    return '<ul class="nav"><li data="%s"><p>期中考试</p><span>%s</span><span class="fr">剩余 60 分钟</span></li></ul>' % (escape(entry), status)


def question(qid, kind, title, answer=""):
    options = ""
    if kind in ("0", "1", "3"):
        pairs = [("A", "C"), ("B", "A"), ("C", "B")] if kind != "3" else [("正确", "true"), ("错误", "false")]
        options = "".join('<div class="answerList radioList" name="%s" onclick="pick(this,\'%s\',\'%s\')"><span class="No">%s</span><cc>选项 %s</cc></div>'
                          % (key, qid, kind, label, label) for label, key in pairs)
    if kind == "2":
        controls = "".join('<textarea class="blanktextarea" name="answer%s%d">%s</textarea>' % (qid, i, escape(v))
                           for i, v in enumerate(answer or ["", ""], 1))
    elif kind == "4":
        controls = '<textarea name="answer%s">%s</textarea>' % (qid, escape(answer))
    else:
        controls = '<input id="answer%s" name="answer%s" value="%s">' % (qid, qid, escape(answer))
    return ('<div class="allAnswerList questionWrap singleQuesId ans-cc-exam" data="%s">'
            '<input name="questionId" value="%s"><input name="type%s" value="%s">'
            '<div class="tit"><h3>题型（5.0分）</h3>1.%s</div>%s%s</div>' % (qid, qid, qid, kind, title, controls, options))


class ExamParsingTest(unittest.TestCase):
    def test_catalog_reads_only_the_list_and_preserves_status(self):
        client = Mock()
        client._request.return_value.text = exam_list()
        entries = catalog(client, COURSE)
        self.assertEqual(entries[0][1]["id"], "exam-900")
        self.assertEqual(entries[0][2], 2)
        self.assertNotIn("_unavailable", entries[0][1])
        client._request.assert_called_once()
        self.assertTrue(client._request.call_args.args[1].endswith("/exam/phone/task-list"))
        for status in ("未开始", "已截止", "已完成", "未知状态"):
            item = decode_exam_list(exam_list(status), COURSE)[0][1]
            self.assertIn("_unavailable", item)
            self.assertEqual(item["_completion_status"] == "submitted", status == "已完成")
        self.assertEqual(decode_exam_list("<p>暂无考试</p>", COURSE), [])

    def test_completed_and_closed_exams_need_no_answering_url(self):
        for status in ("已完成", "已提交", "已交卷", "未开始", "已截止", "未知状态"):
            for entry in ("", "javascript:void(0)", MOBILE_ENTRY.replace("&enc_task=token", ""),
                          "/exam-ans/exam/phone/look?taskrefId=900", "https://[invalid"):
                with self.subTest(status=status, entry=entry):
                    item = decode_exam_list(exam_list(status, entry), COURSE)[0][1]
                    self.assertIn("_unavailable", item)
                    self.assertNotIn("_exam_url", item)
                    self.assertEqual(item["_completion_status"] == "submitted", status in ("已完成", "已提交", "已交卷"))
                    if "taskrefId=900" in entry:
                        self.assertEqual(item["id"], "exam-900")

    def test_official_mobile_entry_and_class_identity(self):
        item = decode_exam_list(exam_list(entry=MOBILE_ENTRY), COURSE)[0][1]
        self.assertEqual(item["_exam_url"], "https://mooc1-api.chaoxing.com" + MOBILE_ENTRY)
        self.assertNotIn("_unavailable", item)
        for entry in (MOBILE_ENTRY.replace("clazzId=222", "clazzId=999"),
                      MOBILE_ENTRY + "&classId=999", MOBILE_ENTRY.replace("&enc_task=token", ""),
                      MOBILE_ENTRY.replace("mtaskmsgspecial", "unrecognized")):
            with self.subTest(entry=entry), self.assertRaises(ChaoxingError):
                decode_exam_list(exam_list(entry=entry), COURSE)

    def test_unknown_lists_wrong_courses_and_external_urls_fail_closed(self):
        for page in ("<html>服务异常</html>", exam_list(entry=ENTRY.replace("111", "999")),
                     exam_list(entry="https://evil.example/exam-ans/exam/a"),
                     exam_list().replace("900", "not-a-number")):
            with self.subTest(page=page), self.assertRaises(ChaoxingError):
                decode_exam_list(page, COURSE)
        for url in ("https://mooc1-api.chaoxing.com.evil.example/exam-ans/exam/a", "http://mooc1.chaoxing.com/exam-ans/exam/a",
                    "https://user:pass@mooc1.chaoxing.com/exam-ans/exam/a", "https://mooc1.chaoxing.com:8080/exam-ans/exam/a"):
            with self.assertRaises(ChaoxingError):
                exam_url(url)

    def test_native_option_values_blanks_and_existing_answers(self):
        single, multi, blanks = decode_exam_questions(question("101", "0", "单选") + question("102", "1", "多选") + question("103", "2", "填空", ["旧", ""]))
        self.assertEqual(encode_answer(single, "B"), "A")
        self.assertEqual(encode_answer(multi, ["A", "B"]), "AC")
        self.assertEqual(encode_answer(blanks, ["替换", "新"]), ["旧", "新"])
        self.assertEqual(encode_answer(dict(blanks, existing_answer=["  ", ""]), ["甲", "乙"]), ["甲", "乙"])
        with self.assertRaises(ValueError):
            encode_answer(blanks, ["数量不对"])
        self.assertEqual(single["title"], "单选")
        self.assertEqual(decode_exam_questions(question("104", "9", "未知"))[0]["type"], "unknown")
        with self.assertRaises(ChaoxingError):
            decode_exam_questions(question("101", "0", "题") * 2)
        self.assertIn('<img src="https://p.chaoxing.com/a.png">', decode_exam_questions(
            question("101", "0", '图<img src="https://p.chaoxing.com/a.png">'))[0]["title"])

    def test_verification_and_expiry_prevent_starting_or_writing(self):
        for html in ('<input id="faceRecognitionCompare" value="1">', '<script>var needcode = 1;</script>',
                     '<input id="captchaCheck" value="true">', '<p class="blankTips">考试尚未开始</p>'):
            with self.assertRaises(ExamNeedsUser):
                decode_exam_questions(html)
        runner = ChaoxingExamRunner(Mock(), Mock())
        runner.deadline = time.monotonic() - 1
        with self.assertRaises(ExamNeedsUser):
            runner._check()

    def test_session_cannot_switch_to_another_attempt_of_the_same_exam(self):
        runner = ChaoxingExamRunner(Mock(), Mock())
        runner.course, runner.item = COURSE, ITEM
        runner.deadline = None
        runner.answer_id = None
        runner.page = Mock(url=ITEM["_exam_url"])

        def form(answer_id):
            fields = {"courseId": "111", "classId": "222", "testPaperId": "900",
                      "testUserRelationId": answer_id, "enc": "token", "encRemainTime": "3600",
                      "encLastUpdateTime": "123", "remainTime": "3600"}
            inputs = "".join('<input id="%s" value="%s">' % pair for pair in fields.items())
            return '<form id="submitTest">%s</form>' % inputs

        runner._html = Mock(return_value=form("700"))
        runner._session()
        runner._html.return_value = form("701")
        with self.assertRaisesRegex(ExamNeedsUser, "作答记录发生变化"):
            runner._session()


class ExamWebTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = Studia(SettingsStore(directory.name), oauth=Mock(return_value=None))
        self.platform = self.app.platforms["cx"]
        self.platform.rows, self.platform.client = [COURSE], Mock()
        self.addCleanup(patch.stopall)
        patch("web.server.quiet_hours", return_value={"active": False}).start()

    def test_completed_entries_do_not_hide_available_exams_or_restart(self):
        completed = exam_list("已完成", MOBILE_ENTRY.replace("&enc_task=token", ""))
        pending = exam_list(entry=ENTRY.replace("900", "901"))
        self.platform.client._request.return_value.text = completed.replace("</ul>", "") + pending.replace('<ul class="nav">', "")
        tasks = self.app.tasks("cx", "111-222", exams=True)["tasks"]
        self.assertEqual([t["id"] for t in tasks], ["exam-900", "exam-901"])
        self.assertEqual([t["available"] for t in tasks], [False, True])
        self.assertEqual([t["done"] for t in tasks], [True, False])
        self.assertEqual(tasks[0]["status"], "已提交")
        self.platform.client._request.assert_called_once()
        runner = ChaoxingExamRunner(self.platform.client, Mock())
        with patch.object(runner, "_open") as opened:
            result = runner.run_paper(COURSE, ITEM)
        self.assertEqual(result["status"], "skipped")
        opened.assert_not_called()
        runner.ai_client.answer.assert_not_called()

    def test_lists_are_separate_and_malformed_selections_do_not_start_jobs(self):
        with patch("web.server.cx_exam_catalog", return_value=[(COURSE, ITEM, 2)]) as exams, \
                patch("web.server.cx_catalog", return_value=[(COURSE, {"id": "work-1", "name": "章节作业"}, 1)]) as work:
            self.assertEqual(self.app.tasks("cx", "111-222", exams=True)["tasks"][0]["category"], "考试")
            work.assert_not_called()
            self.app.tasks("cx", "111-222")
            self.app.tasks("cx", "111-222", fresh=True, exams=True)
            self.assertEqual(exams.call_count, 2)
        for tasks in ([], None, "exam-900", ["work-1"], ["exam-900", "missing"], ["exam-900", "exam-900"], [{}]):
            with self.subTest(tasks=tasks), self.assertRaises(HttpError):
                self.app.start_job({"platform": "cx", "course": "111-222", "type": "exam", "tasks": tasks})
        with self.assertRaises(HttpError):
            self.app.start_job({"platform": "cx", "course": "111-222", "type": "exam", "tasks": ["exam-900"], "auto_submit": "false"})
        self.assertFalse(self.app.jobs)

    def test_exam_uses_the_users_active_ai_configuration(self):
        config = AIConfig("https://example.org/v1", "user-model", "user-test-key", api_format="chat_completions")
        self.app.store.activate_ai_profile(self.app.store.save_ai_profile(config))
        ai = Mock(config=config)
        runner = Mock()
        runner.run_paper.return_value = {"status": "saved", "message": "已保存"}
        with patch("web.server.AIClient", return_value=ai) as client, patch("web.server.ChaoxingExamRunner", return_value=runner) as factory:
            result = self.app._answer(self.platform, [(COURSE, ITEM, 2)], False,
                                      {"id": "j", "kind": "exam", "title": "AI 考试", "cancel": threading.Event()})
        self.assertEqual(client.call_args.args[0], config)
        self.assertIs(factory.call_args.args[1], ai)
        self.assertFalse(factory.call_args.kwargs["auto_submit"])
        ai.check_connection.assert_called_once_with()
        runner.run_paper.assert_called_once_with(COURSE, ITEM, 2)
        self.assertEqual(result, "已保存")
        ai.session.close.assert_called_once()

    def test_exam_conflicts_with_answering_and_never_resumes_after_restart(self):
        self.app.jobs["old"] = {"kind": "answer", "platform": "cx", "course": "111-222", "title": "AI 答题"}
        with self.assertRaises(HttpError):
            self.app._check_conflicts("cx", "111-222", "exam")
        self.app.jobs.clear()
        self.app._saved = Mock(return_value={"saved_at": time.time(), "active": [
            {"type": "exam", "platform": "cx", "course": "111-222", "course_name": "课程"}]})
        self.app.start_job = Mock()
        self.app.resume()
        self.app.start_job.assert_not_called()
        self.assertEqual(self.app.history[0]["summary"], INTERRUPTED)


class ExamBrowserTest(unittest.TestCase):
    """真实浏览器访问本机合成平台，绝不访问真实账户、试卷或 AI 服务。"""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import Error, sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Error as exc:
                if "Executable doesn't exist" in str(exc):
                    raise unittest.SkipTest("Chromium is not installed")
                raise
            browser.close()

    def setUp(self):
        self.answers = {"101": "", "102": "", "103": "", "104": ["旧", ""], "105": ""}
        self.kinds = {"101": "0", "102": "1", "103": "3", "104": "2", "105": "4"}
        self.posts, self.starts, self.submitted, self.rejected = [], 0, False, False
        self.sequence = 0
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, body, content="text/html; charset=utf-8", code=200):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url, params = urlsplit(self.path), parse_qs(urlsplit(self.path).query)
                if url.path in ("/", "/studia.js", "/studia.css", "/theme.js", "/mock.js"):
                    path = STATIC / (url.path.lstrip("/") or "index.html")
                    mime = "text/javascript" if path.suffix == ".js" else "text/css" if path.suffix == ".css" else "text/html"
                    self.send(path.read_text(), mime + "; charset=utf-8")
                elif url.path.endswith("/task-list"):
                    entry = MOBILE_ENTRY.replace("&enc_task=token", "") if test.submitted else ENTRY
                    self.send(exam_list("已完成" if test.submitted else "未交", test.base + entry))
                elif url.path.endswith("/task-exam"):
                    self.send('<script>const hiddenTip="人脸识别";</script><button onclick="location.href=\'/exam-ans/exam/phone/start\'">开始考试</button>')
                elif url.path.endswith("/start"):
                    test.starts += 1
                    self.send(test.page("101"))
                elif url.path.endswith("/reVersionTestStartNew"):
                    self.send(test.page(list(test.answers)[int(params["start"][0])]))
                elif url.path.endswith("/loadAnswerStatic"):
                    self.send("<ul>" + "".join('<li data="%d"></li>' % i for i in range(len(test.answers))) + "</ul>")
                elif url.path.endswith("/preview"):
                    self.send(test.page())
                else:
                    self.send("不存在", code=404)

            def do_POST(self):
                data = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode(), keep_blank_values=True)
                test.posts.append(data)
                if test.rejected:
                    self.send(json.dumps({"status": "error", "msg": "拒绝提交"}), "application/json")
                    return
                if data["tempSave"] == ["false"]:
                    test.submitted = True
                else:
                    qid = data["qid"][0]
                    test.answers[qid] = json.loads(data["answer"][0])
                test.sequence += 1
                self.send(json.dumps({"status": "success", "data": "123|3600|enc%d" % test.sequence}), "application/json")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self.base = "http://127.0.0.1:%d" % server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(patch.stopall)
        patch("ChaoxingMain.exam.LIST_URL", self.base + "/exam/phone/task-list").start()
        patch("ChaoxingMain.exam.EXAM_BASE", self.base + "/exam-ans/exam/").start()
        patch("ChaoxingMain.exam.exam_url", side_effect=lambda url: url if url.startswith(self.base) else self.base + url).start()
        patch("base.quiet_hours.quiet_hours", return_value={"active": False}).start()
        self.client = ChaoxingApi(cookies={"UID": "test"})
        self.addCleanup(self.client.close)
        self.ai = Mock()
        answers = {"单选": "B", "多选": ["A", "B"], "判断": True, "填空": ["更改", "新"], "简答": "简答内容"}
        self.ai.answer.side_effect = lambda q, **kwargs: {"answer": answers[q["title"]], "explanation": ""}
        self.runner = ChaoxingExamRunner(self.client, self.ai, auto_submit=True, cover_rate=1)

    def page(self, qid=None):
        titles = {"101": "单选", "102": "多选", "103": "判断", "104": "填空", "105": "简答"}
        fields = {"courseId": "111", "classId": "222", "testPaperId": "900", "testUserRelationId": "700",
                  "enc": "enc%d" % self.sequence, "encRemainTime": "3600", "encLastUpdateTime": "123", "remainTime": "3600"}
        html = '<form id="submitTest">' + "".join('<input id="%s" value="%s">' % (k, v) for k, v in fields.items())
        for i in ([qid] if qid else self.answers):
            html += question(i, self.kinds[i], titles[i], self.answers[i])
        html += '</form><button onclick="save(false)">保存</button><button onclick="if(confirm(\'确认交卷？\'))save(true)">交卷</button>'
        html += """<script>
        function pick(node, id, kind) {
          const input = document.querySelector('#answer'+id), value = node.getAttribute('name');
          input.value = kind === '1' ? (input.value.includes(value) ? input.value.replace(value,'') : [...input.value+value].sort().join('')) : value;
        }
        async function save(final) {
          const qid = document.querySelector('[name=questionId]').value;
          const blanks = [...document.querySelectorAll('textarea.blanktextarea')];
          const answer = blanks.length ? blanks.map(n=>n.value) : document.querySelector('#answer'+qid+',textarea[name=answer'+qid+']').value;
          const data = new URLSearchParams({qid, answer: JSON.stringify(answer), tempSave: String(!final)});
          const r = await fetch('/exam-ans/exam/test/reVersionSubmitTestNew?tempSave='+!final, {method:'POST',body:data});
          const result = await r.json();
          if(result.status === 'success') document.querySelector('#enc').value = result.data.split('|')[2];
        }
        </script>"""
        return html

    def run_exam(self):
        return self.runner.run_paper(COURSE, ITEM)

    def test_real_browser_preserves_existing_answers_saves_and_submits_once(self):
        self.answers["101"] = "C"
        result = self.run_exam()
        self.assertEqual(result["status"], "submitted", result)
        self.assertEqual(self.answers, {"101": "C", "102": "AC", "103": "true", "104": ["旧", "新"], "105": "简答内容"})
        self.assertEqual(sum(p["tempSave"] == ["false"] for p in self.posts), 1)
        self.assertEqual(len(self.posts), 5)
        self.assertEqual(self.ai.answer.call_count, 4)
        self.assertEqual(self.starts, 1)

    def test_each_answer_is_saved_before_the_next_ai_request(self):
        answers = {"单选": "B", "多选": ["A", "B"], "判断": True,
                   "填空": ["更改", "新"], "简答": "简答内容"}
        saved_before_request = []

        def answer(question, **kwargs):
            saved_before_request.append(len(self.posts))
            return {"answer": answers[question["title"]], "explanation": ""}

        self.ai.answer.side_effect = answer
        result = self.run_exam()

        self.assertEqual(result["status"], "submitted", result)
        self.assertEqual(saved_before_request, [0, 1, 2, 3, 4])
        self.assertEqual(sum(p["tempSave"] == ["true"] for p in self.posts), 5)
        self.assertEqual(sum(p["tempSave"] == ["false"] for p in self.posts), 1)

    def test_save_only_and_partial_papers_never_submit(self):
        self.runner.auto_submit = False
        self.assertEqual(self.run_exam()["status"], "saved")
        self.assertFalse(self.submitted)
        self.assertTrue(all(p["tempSave"] == ["true"] for p in self.posts))
        self.kinds["105"], self.answers["105"] = "9", ""
        self.runner.auto_submit = True
        result = self.run_exam()
        self.assertEqual(result["status"], "saved")
        self.assertIn("4/5", result["message"])
        self.assertIn("第 5 题", result["message"])
        self.assertFalse(self.submitted)

    def test_rejected_save_is_not_retried(self):
        self.rejected = True
        with self.assertRaisesRegex(ExamNeedsUser, "平台拒绝保存"):
            self.run_exam()
        self.assertEqual(len(self.posts), 1)
        self.assertFalse(self.submitted)

    def test_expiry_during_ai_stops_before_any_save(self):
        def expire(q, **kw):
            self.runner.deadline = time.monotonic() - 1
            return {"answer": "B", "explanation": ""}
        self.ai.answer.side_effect = expire
        with self.assertRaises(ExamNeedsUser):
            self.run_exam()
        self.assertEqual(self.posts, [])

    def test_exam_tab_selection_confirmation_and_mobile_layout(self):
        from playwright.sync_api import expect, sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            mock = (STATIC / "mock.js").read_text().replace(
                'if (path.startsWith("/api/platforms/cx/exams"))',
                'if (path.startsWith("/api/platforms/cx/exams") && globalThis.failExamRefresh) throw new Error("模拟刷新失败");\n'
                '  if (path.startsWith("/api/platforms/cx/exams"))')
            page.route("**/mock.js", lambda route: route.fulfill(body=mock, content_type="text/javascript"))
            page.goto(self.base + "/?mock#/course/cx/cx-1/exam")
            expect(page.get_by_role("tab", name="考试", exact=True)).to_have_attribute("aria-selected", "true")
            expect(page.locator("[data-paper]")).to_have_count(3)
            expect(page.locator("[data-paper].on")).to_have_count(0)
            expect(page.locator("[data-pick-all]")).to_have_count(0)
            expect(page.locator("[data-submit]")).to_be_disabled()
            page.locator('[data-paper="exam-2"]').click(force=True)
            expect(page.locator("[data-submit]")).to_be_disabled()
            page.locator('[data-paper="exam-1"]').click()
            expect(page.locator("[data-save]")).to_be_enabled()
            page.get_by_role("tab", name="答题", exact=True).click()
            expect(page.locator('[data-paper="work-a"]')).to_be_visible()
            page.locator('[data-paper="work-a"]').click()
            page.get_by_role("tab", name="考试", exact=True).click()
            expect(page.locator("[data-paper].on")).to_have_count(1)
            expect(page.locator(".dock .info")).to_contain_text("已选 1 场")
            page.evaluate("globalThis.failExamRefresh = true")
            page.get_by_role("button", name="刷新考试列表").click()
            expect(page.get_by_text("模拟刷新失败", exact=False)).to_be_visible()
            expect(page.locator("[data-paper]")).to_have_count(0)
            page.evaluate("globalThis.failExamRefresh = false")
            page.locator("[data-papers-retry]").click()
            expect(page.locator("[data-paper].on")).to_have_count(1)
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("[data-submit]")).to_be_visible()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            page.locator("[data-save]").click()
            expect(page.locator("[data-save]")).to_have_class("btn armed")
            expect(page.locator("[data-save]")).to_have_text("确认开始考试并保存答案")
            page.locator("[data-save]").click()
            expect(page.get_by_text("正在考试", exact=False).first).to_be_visible()
            self.assertFalse(errors, errors)
            browser.close()


if __name__ == "__main__":
    unittest.main()
