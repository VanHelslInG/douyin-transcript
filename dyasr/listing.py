"""把采集到的作品列成一张可勾选的表，让用户按编号决定转写哪些。

编号口径（重要）：
  · 有 `<src>/页面顺序.txt` 时 → 按该文件的顺序，01 就是用户打开抖音看到的第一条
    （通常正是置顶作品）。这份文件由 Agent 在浏览器里读取作品页顺序后写入。
  · 没有时 → 按发布时间倒序（最新在前）。
下载器本身不返回「置顶」标记，所以第一种方式是目前唯一能对齐 App 视觉顺序的办法。
"""
import json
import os

from . import core, paths


def build(src, out_dir=None, page_order=None):
    media = core.list_media(src)
    if not media:
        raise SystemExit(f"✗ 目录内没有音视频文件：{src}")
    items = core.make_items(media, raw_dir=None)
    if out_dir:
        raw_dir = os.path.join(out_dir, "raw")
        min_chars = int(paths.load_config().get("min_chars", 100))
        for it in items:
            body = core.read_done(os.path.join(raw_dir, it["stem"] + ".txt"), min_chars)
            if body:
                it["status"], it["chars"] = "done", core.char_count(body)

    lines = page_order if page_order is not None else core.load_page_order(src)
    if lines:
        items = core.reorder_by_page(items, lines)
    else:
        items.sort(key=lambda i: (i["info"]["key"] is not None,
                                  i["info"]["key"] or (), i["stem"]), reverse=True)
        for n, it in enumerate(items, 1):
            it["idx"] = n
    return items, bool(lines)


def show(src, out_dir=None, as_json=False):
    items, by_page = build(src, out_dir)

    total_dur = sum(i["dur"] for i in items)
    pending = sum(1 for i in items if i["status"] != "done")

    if as_json:
        print(json.dumps({
            "src": os.path.abspath(src),
            "ordered_by": "page" if by_page else "time_desc",
            "count": len(items),
            "total_seconds": round(total_dur, 1),
            "pending": pending,
            "items": [{
                "idx": i["idx"],
                "date": i["info"]["date"],
                "time": i["info"]["time"],
                "title": i["info"]["title"] or i["stem"],
                "duration": i["dur"],
                "chars": i["chars"],
                "status": i["status"],
                "file": i["name"],
            } for i in items],
        }, ensure_ascii=False, indent=2))
        return 0

    print(f"\n共 {len(items)} 条作品　·　总时长 {core.fmt_sec(total_dur)}　·　"
          f"待转写 {pending} 条")
    print(f"编号口径：{'页面顺序（01 = 打开抖音看到的第一条，通常是置顶）' if by_page else '发布时间倒序（最新在前）'}")
    if not by_page:
        print(f"  ⚠ 未找到 {core.PAGE_ORDER_FILE}，无法还原置顶顺序；"
              "需要严格对齐 App 顺序见 SKILL.md「作品清单」一节")
    print()
    print("  " + core.pad("#", 4) + core.pad("发布", 12) + core.pad("时长", 7)
          + core.pad("状态", 8) + "标题")
    print("  " + "─" * 74)
    for i in items:
        flag = "已转写" if i["status"] == "done" else "待转写"
        date = i["info"]["date"] or "未知"
        dur = f"{int(i['dur'] // 60)}:{int(i['dur'] % 60):02d}" if i["dur"] else "—"
        print("  " + core.pad(f"{i['idx']:02d}", 4) + core.pad(date, 12)
              + core.pad(dur, 7) + core.pad(flag, 8)
              + core.truncate(i["info"]["title"] or i["stem"], 46))
    print()
    print("转写选中项：dyasr transcribe --src \"<目录>\" --out ./文稿 --select 1,3,5-8")
    print("转写全部　：dyasr transcribe --src \"<目录>\" --out ./文稿 --select all")
    return 0
