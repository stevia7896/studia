"""超星章节作业批量作答：课程 → 章节 → 作业。

这是本次迁移的入口，对应 chaoxing 项目 main.py 里 workid 任务的分发；视频、文档、
阅读、直播任务不在范围内。进度沿用 studia 的报告约定：每门课一个 result，每份作业
一个 unit，界面如何显示由调用方决定。
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from ai.client import AIServiceError
from base.auth import AuthenticationExpired, PlatformUnavailable
from ChaoxingMain.api import ChaoxingApi, ChaoxingError
from ChaoxingMain.exercise import ChaoxingExerciseRunner
from MoocMain.log import Logger
from reporting import Reporter

logger = Logger(__name__).get_log()

DONE = ("submitted", "saved")
UNIT_STATE = {"skipped": "skipped"}
# ponytail: 章节卡片逐章串行要几十秒；并行读，但超星 WAF 对突发请求敏感，限制为 6 路。被 403 时调小。
CATALOG_WORKERS = 6


def _chapter_jobs(client, course, include_finished=False):
    """每个章节和它的 (jobs, job_info)，按章节顺序返回；章节之间并行读取（纯读取）。"""
    points = client.course_points(course)["points"]
    with ThreadPoolExecutor(CATALOG_WORKERS) as pool:
        return list(zip(points, pool.map(
            lambda point: client.job_list(course, point, include_finished=include_finished), points)))


def _status(client, course, name):
    """平台状态接口是锦上添花：方法缺失或读取失败都返回 None，不拖垮列表。"""
    method = getattr(client, name, None)
    if not callable(method):
        return None
    try:
        return method(course)
    except Exception:
        logger.debug("读取课程状态失败：%s", name, exc_info=True)
        return None


def _chapter_done(point, nodes):
    """整章任务点都完成：优先 gas/clazz 计数，取不到再看章节页的完成标志。"""
    counts = nodes.get(str(point.get("id"))) if isinstance(nodes, dict) else None
    return counts.get("unfinish", 0) == 0 and counts.get("finish", 0) > 0 if counts else bool(point.get("has_finished"))


def catalog(client, course, submitted=()):
    """这门课所有章节里的作业，给终端 / 网页的任务列表用。

    每条是 ``(course, item, category)``；item 除了任务本身，还带上作答要用的
    ``_point`` 和 ``_job_info``，这样选中的作业可以单独交给 runner.run()。

    打开作业页会建草稿，不能拿来查状态；workid 卡片上也没有完成字段。完成状态按顺序取：
    studia 自己交卷成功的记录（``submitted``，jobid 集合）→ 章节已完成（章节页标志或
    gas/clazz 计数，整章任务点都完成，作业自然也交了）→ 手机端已完成 / 未完成任务点
    列表 → 都没有就如实标成未知。真实账号上手机端列表和 gas/clazz 都可能是空的，
    章节页标志是唯一一定有的来源。
    """
    with ThreadPoolExecutor(2) as pool:
        nodes, job_status = pool.submit(_status, client, course, "course_nodes"), pool.submit(_status, client, course, "job_status_ids")
        walked = _chapter_jobs(client, course)
        nodes, (finished, unfinished) = nodes.result() or {}, job_status.result() or (set(), set())
    entries = []
    for point, (jobs, job_info) in walked:
        for job in jobs:
            if job["type"] != "workid":
                continue
            name = job.get("name") or point.get("title") or "章节作业"
            # 手机端列表里的 id 可能带也可能不带 work- 前缀，两种都认。
            ids = {job["jobid"], job["jobid"].replace("work-", "", 1)}
            status = ("submitted" if job["jobid"] in submitted or _chapter_done(point, nodes) or ids & finished
                      else "draft" if ids & unfinished else "unknown")
            entries.append((course, dict(
                job, id=job["jobid"], name=name, _point=point, _job_info=job_info, _completion_status=status,
                _hint="平台没有返回这份作业的完成状态；已完成的作业请不要再勾选" if status == "unknown" else ""), 1))
    return entries


def course_units(client, course):
    """章节和其中的任务点，给网页课程页的单元格子用（只读）。

    已完成的卡片也会带回来，所以整章学完时显示的是 n/n 而不是 0/0；完成状态优先
    相信平台的两份状态（gas/clazz 的节点计数、手机端 finished/unfinished 列表），
    状态接口不可用时采用章节完成标志，再用卡片 isPassed 兜底——文档/PPT 的
    卡片标志经常不更新，即使章节页已经确认完成。
    """
    with ThreadPoolExecutor(2) as pool:  # 两份状态和章节卡片同时读
        nodes, job_status = pool.submit(_status, client, course, "course_nodes"), pool.submit(_status, client, course, "job_status_ids")
        walked = _chapter_jobs(client, course, include_finished=True)
        nodes, job_status = nodes.result() or {}, job_status.result()
    finished, unfinished = job_status if job_status else (set(), set())
    chapters = []
    for point, (jobs, _) in walked:
        all_done = _chapter_done(point, nodes)
        units = []
        for job in jobs:
            if job["type"] == "workid":
                continue  # 作业在「AI 答题」里做，学习不碰它，放进格子会一直显示待学
            done = bool(job.get("is_passed")) or job["jobid"] in finished
            if job["jobid"] in unfinished:
                done = False
            if all_done:
                done = True
            units.append({"id": job["jobid"], "name": job.get("name") or job["type"],
                          "kind": job["type"], "state": "done" if done else "queued"})
        if units:
            chapters.append({"name": point.get("title") or "未命名章节", "units": units})
    return chapters


class ChaoxingHandler:
    def __init__(self, username="", password="", *, client=None, ai_client=None, courses=None,
                 course_ids=None, skip=None, auto_submit=False, cover_rate=0.8, delay=0.0,
                 on_progress=None, cancel_event=None, sleep=None):
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self.client = client if client is not None else ChaoxingApi(username, password)
        self.ai_client = ai_client
        self.courses = courses
        self.course_ids = {str(value) for value in course_ids or ()}
        self.skip = [word for word in skip or () if word]
        self.progress = Reporter(on_progress)
        self.runner = ChaoxingExerciseRunner(
            self.client, ai_client, auto_submit=auto_submit, cover_rate=cover_rate, delay=delay,
            on_progress=on_progress, sleep=sleep, cancel_event=self.cancel_event)
        self.counts = {"submitted": 0, "saved": 0, "skipped": 0, "error": 0,
                       "cancelled": 0, "submission_unknown": 0}
        try:
            self.start_courses()
        finally:
            if client is None:
                self.client.close()

    def start_courses(self):
        try:
            rows = self.courses if self.courses is not None else self.client.course_list()
        except (AuthenticationExpired, PlatformUnavailable, ChaoxingError) as error:
            logger.error("读取超星课程失败：%s", error)
            self.progress.result("读取超星课程", "error")
            raise
        rows = [row for row in rows if self._wanted(row)]
        if not rows:
            logger.warning("没有可处理的超星课程")
            self.progress.result("读取超星课程", "skipped")
            return
        self.progress.begin("超星 · 章节作业", len(rows), "门课程")
        for course in rows:
            if self.cancel_event.is_set():
                break
            self.progress.detail(course.get("title", "课程"))
            self.progress.result(course.get("title", "课程"), self.process_course(course))

    def _wanted(self, course):
        if self.course_ids and str(course.get("courseId")) not in self.course_ids:
            return False
        title = course.get("title") or ""
        return not any(word in title for word in self.skip)

    def process_course(self, course):
        title = course.get("title", "课程")
        try:
            point_list = self.client.course_points(course)
        except (AuthenticationExpired, PlatformUnavailable):
            raise
        except ChaoxingError as error:
            logger.error("%s：读取章节失败：%s", title, error)
            return "error"
        if point_list["hasLocked"]:
            logger.info("%s：有需要完成任务点才能解锁的章节", title)
        failed = processed = False
        for point in point_list["points"]:
            if self.cancel_event.is_set():
                return "cancelled"
            try:
                jobs, job_info = self.client.job_list(course, point)
            except (AuthenticationExpired, PlatformUnavailable):
                raise
            except ChaoxingError as error:
                logger.error("%s / %s：读取任务点失败：%s", title, point.get("title", ""), error)
                failed = True
                continue
            for job in jobs:
                if job["type"] != "workid" or self.cancel_event.is_set():
                    continue
                status = self.process_work(course, point, job, job_info)
                failed |= status in ("error", "submission_unknown")
                processed |= status in DONE
        if failed:
            return "error"
        return "success" if processed else "skipped"

    def process_work(self, course, point, job, job_info):
        name = job.get("name") or point.get("title") or "章节作业"
        self.progress.unit(job["jobid"], name, "running")
        try:
            result = self.runner.run(course, point, job, job_info)
        except (AuthenticationExpired, PlatformUnavailable):
            self.progress.unit(job["jobid"], name, "failed")
            raise
        except AIServiceError:
            self.progress.unit(job["jobid"], name, "failed")
            raise
        except Exception as error:
            logger.exception("%s：作答异常", name)
            result = {"status": "error", "message": str(error)}
        status = result["status"]
        self.counts[status] = self.counts.get(status, 0) + 1
        state = "done" if status in DONE else UNIT_STATE.get(status, "failed")
        self.progress.unit(job["jobid"], name, state)
        logger.info("%s / %s：%s", course.get("title", "课程"), name, result["message"])
        return status
