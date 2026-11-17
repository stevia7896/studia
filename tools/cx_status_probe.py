#!/usr/bin/env python3
"""超星章节作业完成状态探测：只读，不打开作业页（打开会建草稿）。

用法（先在网页版登录过超星）：
    uv run python tools/cx_status_probe.py          # 列出课程
    uv run python tools/cx_status_probe.py 3        # 探测第 3 门课

输出每份章节作业卡片的原始字段（enc/token 等签名已隐藏），以及手机端已完成 / 未完成
任务点列表里能不能找到这些作业，用来判断作业的完成状态该从哪里读。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ChaoxingMain.api import ChaoxingApi  # noqa: E402
from ChaoxingMain.decode import _MARG  # noqa: E402
from settings import SettingsStore  # noqa: E402

SECRET = re.compile(r"enc|token|sign|key|uuid", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "…" if SECRET.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def main():
    saved = SettingsStore().session("cx")
    if not saved or "cookies" not in saved:
        sys.exit("没有找到已保存的超星登录，请先在网页版登录超星。")
    client = ChaoxingApi(cookies=saved["cookies"])
    courses = client.course_list()
    if len(sys.argv) < 2:
        for index, course in enumerate(courses, 1):
            print(index, course.get("title") or course.get("courseName"))
        print("\n再运行一次，后面加上课程编号，例如：uv run python tools/cx_status_probe.py 1")
        return
    course = courses[int(sys.argv[1]) - 1]
    print("课程：", course.get("title") or course.get("courseName"))

    pages, request = [], client._request

    def spy(method, url, **kwargs):  # job_list 只读卡片页；把原始页面留下来看字段
        response = request(method, url, **kwargs)
        if "knowledge/cards" in url:
            pages.append(response.text)
        return response

    client._request = spy
    works = {}
    for point in client.course_points(course)["points"]:
        pages.clear()
        client.job_list(course, point, include_finished=True)
        for page in pages:
            found = _MARG.search(page)
            data = json.loads(found.group(1)) if found else {}
            for card in data.get("attachments") or []:
                prop = card.get("property") or {}
                if "workid" in (str(card.get("type")), str(prop.get("type"))) or str(card.get("jobid", "")).startswith("work-"):
                    works[card.get("jobid")] = (point.get("title"), card)

    nodes = client.course_nodes(course)
    print("\n== 每章完成计数（%d 章；finish 已完成 / unfinish 未完成；章节页标志 has_finished）==" % len(nodes))
    for point in client.course_points(course)["points"]:
        counts = nodes.get(str(point.get("id"))) or {}
        print("%s  finish=%s unfinish=%s has_finished=%s" % (
            point.get("title"), counts.get("finish", "?"), counts.get("unfinish", "?"), point.get("has_finished")))

    print("\n== 章节作业卡片（%d 份）==" % len(works))
    for jobid, (chapter, card) in works.items():
        print("\n[%s] %s" % (chapter, (card.get("property") or {}).get("title")))
        print(json.dumps(redact(card), ensure_ascii=False, sort_keys=True))

    finished, unfinished = client.finished_job_ids(course), client.unfinished_job_ids(course)
    bare = {str(j).replace("work-", "", 1) for j in works} | {str(j) for j in works}
    print("\n== 手机端任务点列表 ==")
    print("已完成 %d 个，示例：%s" % (len(finished), sorted(finished)[:5]))
    print("未完成 %d 个，示例：%s" % (len(unfinished), sorted(unfinished)[:5]))
    print("作业出现在已完成里：%s" % sorted(bare & finished))
    print("作业出现在未完成里：%s" % sorted(bare & unfinished))


if __name__ == "__main__":
    main()
