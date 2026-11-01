#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线冒烟测试：不联网、不下载模型、不碰 GPU。

覆盖最容易回归的纯逻辑：文件名解析、按发布时间排序、术语替换、产物生成、
配置读写、doctor 的 JSON 结构。跑法：

    python tests/smoke.py

任何一条失败都会非零退出，方便挂 CI。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 用临时家目录，避免污染真实的 ~/.dyasr
_TMP_HOME = tempfile.mkdtemp(prefix="dyasr_test_")
os.environ["DYASR_HOME"] = _TMP_HOME

from dyasr import core, paths, terms  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + (f"  {extra}" if extra and not cond else ""))


def test_parse_stem():
    print("parse_stem")
    i = core.parse_stem("2025-07-30 17.22.39-如果你突然捡到1个亿 #经济学")
    check("日期", i["date"] == "2025-07-30", i)
    check("时间", i["time"] == "17:22", i)
    check("标题去掉时间前缀", i["title"].startswith("如果你突然"), i["title"])
    check("可排序 key", i["key"] == ("2025-07-30", 17, 22, 39), i)

    j = core.parse_stem("没有日期的标题")
    check("无日期 → key 为 None", j["key"] is None, j)

    k = core.parse_stem("2026-01-08 中国式婚姻 为何脆弱")
    check("只有日期没时间也能解析", k["date"] == "2026-01-08", k)


def test_sort():
    print("sort_media（必须按发布时间，不是 mtime）")
    names = [
        "2026-10-09 09.40.00-晚的.mp4",
        "2025-08-09 09.00.00-早的.mp4",
        "无日期.mp4",
        "2026-06-12 17.01.41-中间的.mp4",
    ]
    got = [core.parse_stem(Path(p).stem)["title"] for p in core.sort_media(names)]
    check("升序", got == ["早的", "中间的", "晚的", "无日期"], got)


def test_terms():
    print("术语表")
    terms.seed_if_missing(force=True)
    m = terms.load_terms()
    check("默认表非空", len(m) >= 5, len(m))
    check("替换生效", terms.apply_terms("这是结构性发债和婚债市场", m)
          == "这是明股实债和婚恋市场")
    check("长词优先", "结构性发债" in m)
    hits = terms.count_hits("结构性发债结构性发债", m)
    check("命中统计", hits.get("结构性发债") == 2, hits)


def test_outputs():
    print("产物生成")
    out = Path(_TMP_HOME) / "work"
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "2025-01-02 10.00.00-第二篇.txt").write_text("内容二" * 60, encoding="utf-8")
    (raw / "2024-12-31 09.00.00-第一篇.txt").write_text("内容一" * 60, encoding="utf-8")
    (raw / "2025-01-02 10.00.00-第二篇_timeline.json").write_text("{}", encoding="utf-8")

    raws = core.list_raw(str(raw))
    check("忽略 _timeline.json", len(raws) == 2, raws)

    items = []
    for n, p in enumerate(raws, 1):
        body = core.read_done(p, 0)
        items.append({"idx": n, "name": os.path.basename(p),
                      "stem": Path(p).stem, "path": p,
                      "info": core.parse_stem(Path(p).stem), "dur": 100.0,
                      "chars": core.char_count(body), "status": "done"})

    doc, csv_path = core.build_outputs(str(out), items, engine="test",
                                       terms_map=terms.load_terms(), raw_dir=str(raw))
    check("合集已生成", os.path.exists(doc))
    check("索引已生成", os.path.exists(csv_path))
    text = Path(doc).read_text(encoding="utf-8")
    check("合集含标题", "第一篇" in text and "第二篇" in text)
    check("合集按时间排序", text.index("第一篇") < text.index("第二篇"))

    csv_text = Path(csv_path).read_text(encoding="utf-8-sig")
    check("索引有表头", csv_text.splitlines()[0].startswith("序号"))
    check("索引行数正确", len(csv_text.strip().splitlines()) == 3)

    core.save_meta(str(out), items)
    meta = core.load_meta(str(out))
    check("meta 保存时长", meta["2024-12-31 09.00.00-第一篇"]["dur"] == 100.0, meta)

    back = core.load_durations_from_index(str(out))
    check("旧索引回填时长", back["2025-01-02 10.00.00-第二篇"]["dur"] == 100.0, back)


def test_config():
    print("配置")
    cfg = paths.set_config(device="cpu", fmodel="sensevoice")
    check("写入生效", cfg["device"] == "cpu" and cfg["fmodel"] == "sensevoice", cfg)
    cfg2 = paths.load_config()
    check("读回一致", cfg2["device"] == "cpu", cfg2)
    check("默认值仍在", cfg2.get("batch_size_s") == 300, cfg2)


def test_doctor_json():
    print("doctor --json（结构契约）")
    from dyasr import doctor
    r = doctor.collect()
    check("有 checks", isinstance(r.get("checks"), list) and r["checks"])
    check("有 blockers/warnings/actions",
          all(isinstance(r.get(k), list) for k in ("blockers", "warnings", "actions")))
    check("ok 是布尔", isinstance(r.get("ok"), bool), r.get("ok"))
    check("可 JSON 序列化", bool(json.dumps(r, ensure_ascii=False)))


def test_selection():
    print("编号选择")
    items = [{"idx": i, "stem": f"2026-01-0{i} 10.00.00-标题{i}", "dur": 1}
             for i in range(1, 6)]
    pick = lambda s: [i["idx"] for i in core.parse_selection(s, items)]
    check("枚举", pick("1,3") == [1, 3], pick("1,3"))
    check("区间", pick("2-4") == [2, 3, 4], pick("2-4"))
    check("中文逗号", pick("1，3") == [1, 3])
    check("all", pick("all") == [1, 2, 3, 4, 5])
    check("空=全部", pick("") == [1, 2, 3, 4, 5])
    check("标题关键字", pick("标题3") == [3], pick("标题3"))
    try:
        core.parse_selection("9", items)
        check("越界应报错", False)
    except SystemExit:
        check("越界应报错", True)


def test_page_order():
    print("页面顺序（还原置顶）")
    items = [
        {"idx": 1, "stem": "2026-10-21 19.29.25-新作品", "dur": 1},
        {"idx": 2, "stem": "2025-07-30 17.22.39-置顶的旧作品", "dur": 1},
        {"idx": 3, "stem": "2026-01-01 10.00.00-中间的", "dur": 1},
    ]
    out = core.reorder_by_page([dict(i) for i in items], ["置顶的旧作品", "中间的"])
    check("置顶排第一", out[0]["stem"].endswith("置顶的旧作品"), out[0]["stem"])
    check("次级按文件顺序", out[1]["stem"].endswith("中间的"))
    check("未匹配的落末尾", out[-1]["stem"].endswith("新作品"))
    check("编号重排为 1..N", [o["idx"] for o in out] == [1, 2, 3], [o["idx"] for o in out])


def test_cookie():
    print("Cookie 导入（四种格式）")
    from dyasr import cookie

    pw = json.dumps({"cookies": [
        {"name": "sessionid", "value": "a", "domain": ".douyin.com"},
        {"name": "sid_tt", "value": "b", "domain": ".douyin.com"},
        {"name": "sessionid_ss", "value": "c", "domain": ".douyin.com"},
        {"name": "ttwid", "value": "d", "domain": ".douyin.com"},
        {"name": "OTHER", "value": "x", "domain": ".baidu.com"},
    ]})
    pairs = dict((n, v) for n, v, _ in cookie.parse(pw))
    check("Playwright storage state", pairs.get("sessionid") == "a", pairs)
    check("过滤非抖音域", "OTHER" not in pairs, pairs)

    arr = json.dumps([{"name": "ttwid", "value": "z"}])
    check("Cookie 数组", dict((n, v) for n, v, _ in cookie.parse(arr)).get("ttwid") == "z")

    netscape = "# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tn1\n"
    check("Netscape", dict((n, v) for n, v, _ in cookie.parse(netscape)).get("sessionid") == "n1")

    header = "ttwid=h1; sid_tt=h2; sessionid=h3"
    check("原始请求头", dict((n, v) for n, v, _ in cookie.parse(header)).get("sid_tt") == "h2")

    # sid_guard 的值是 URL 编码的，必须解码后才能读出到期日
    sg = "x%7C1%7C5184001%7CThu%2C+31-Dec-2026+17%3A18%3A23+GMT"
    target, _h, _p = cookie.import_text(
        f"ttwid=1; sessionid=a; sessionid_ss=b; sid_tt=c; sid_guard={sg}")
    info = cookie.status()
    check("写入成功", info["present"] and not info["missing"], info)
    check("到期日解码", info["expires_at"] == "2026-12-31", info["expires_at"])
    target.unlink()


def test_clean():
    print("清理源视频")
    from dyasr import clean

    # 回归：只转写了 2 条时，未选中的视频必须活着（曾经 bug：整目录被清空）
    all_stems = {"A", "B", "C"}
    keep, delete = clean.plan(all_stems, {"A", "B"})
    check("只删已转写的", delete == {"A", "B"}, delete)
    check("未选中的保留", keep == {"C"}, keep)
    check("失败项也保留", clean.plan({"A"}, set())[0] == {"A"})

    d = Path(_TMP_HOME) / "media"
    d.mkdir(parents=True, exist_ok=True)
    for n in ("2026-01-01 10.00.00-A", "2026-01-02 10.00.00-B"):
        (d / f"{n}.mp4").write_bytes(b"0" * 1024)
    n, freed, left = clean.clean_videos(str(d), keep_stems={"2026-01-02 10.00.00-B"},
                                        dry_run=True)
    check("dry-run 不删", (d / "2026-01-01 10.00.00-A.mp4").exists())
    check("dry-run 只统计该删的", n == 1 and freed == 1024, (n, freed))
    n, freed, left = clean.clean_videos(str(d), keep_stems={"2026-01-02 10.00.00-B"})
    check("保留指定项", (d / "2026-01-02 10.00.00-B.mp4").exists() and left == 1, (n, left))
    check("删除其余", not (d / "2026-01-01 10.00.00-A.mp4").exists())


def test_fuzzy():
    print("模糊纠错（拼音级）")
    from dyasr import fuzzy
    if not fuzzy.available():
        print("  ⚠ 未安装 pypinyin/rapidfuzz，跳过")
        return
    fc = fuzzy.FuzzyCorrector(
        ["明股实债", "流动性陷阱", "纳什均衡", "彩礼", "生育率", "通胀"], threshold=0.95)
    chk = lambda src, want: check(f"{src} → {want}", fc.apply(src)[0] == want,
                                  fc.apply(src)[0])

    chk("现在是名股实债", "现在是明股实债")
    chk("进入了流动型陷井", "进入了流动性陷阱")
    chk("纳什均横是一种", "纳什均衡是一种")
    chk("材里的问题", "彩礼的问题")

    # 三条防误伤约束，都是实测被误伤逼出来的
    chk("，情绪价值很重要", "，情绪价值很重要")          # 不能吞标点
    chk("情绪、价值", "情绪、价值")                      # 窗口里夹标点不改
    chk("生育对收入有影响", "生育对收入有影响")          # 不能变「生育率」
    chk("系统中间那段", "系统中间那段")                  # 不能变「通胀」
    chk("完全无关的一句话", "完全无关的一句话")

    out, hits = fc.apply("名股实债和流动型陷井")
    check("命中列表长度", len(hits) == 2, hits)
    check("命中分散不重叠", "明股实债和流动性陷阱" == out, out)


def test_human_size():
    print("其他")
    check("字节", core.human_size(900) == "900 B", core.human_size(900))
    check("MB", core.human_size(5 * 1024 ** 2) == "5.0 MB", core.human_size(5 * 1024 ** 2))
    check("按宽度截断", core.truncate("一二三四五六", 6).endswith("…"))


def main():
    print(f"临时家目录：{_TMP_HOME}\n")
    for fn in (test_parse_stem, test_sort, test_terms, test_outputs, test_config,
               test_selection, test_page_order, test_cookie, test_clean, test_fuzzy,
               test_human_size, test_doctor_json):
        try:
            fn()
        except Exception as e:
            FAIL.append(f"{fn.__name__} 抛异常")
            print(f"  ✘ {fn.__name__} 抛异常：{type(e).__name__}: {e}")
        print()

    shutil.rmtree(_TMP_HOME, ignore_errors=True)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  ✘ {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
