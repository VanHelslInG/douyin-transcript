"""只重新生成产物，不碰模型、不碰显卡。

用途：手改完 raw/*.txt、或更新了 ~/.dyasr/terms.tsv 之后，几秒内重建
「合集.md」和「索引.csv」。这是整个流水线里唯一可以随时廉价重跑的环节，
所以纠错表、排序口径、文档模板都收敛到这里，不要重新转写。
"""
import os

from . import core, paths, terms as terms_mod


def rebuild(out_dir, order="asc", device_note="", doc_name="口播稿件合集.md",
            show_hits=True, fuzzy=True):
    raw_dir = os.path.join(out_dir, "raw")
    if not os.path.isdir(raw_dir):
        raise SystemExit(f"✗ 找不到稿件目录：{raw_dir}")

    raws = core.list_raw(raw_dir)
    if not raws:
        raise SystemExit(f"✗ {raw_dir} 里没有 .txt 稿件")

    meta = core.load_meta(out_dir) or core.load_durations_from_index(out_dir)
    items = []
    for i, p in enumerate(raws, 1):
        stem = os.path.splitext(os.path.basename(p))[0]
        body = core.read_done(p, 0) or ""
        items.append({
            "idx": i, "name": os.path.basename(p), "stem": stem, "path": p,
            "info": core.parse_stem(stem),
            "dur": float((meta.get(stem) or {}).get("dur") or 0),
            "chars": core.char_count(body), "status": "done" if body else "fail",
        })

    # 按文件名里的发布日期排（而不是 mtime），这才是博主真实的时间线
    dated = [i for i in items if i["info"]["key"]]
    undated = [i for i in items if not i["info"]["key"]]
    dated.sort(key=lambda i: (i["info"]["key"], i["stem"]))
    if order in ("desc", "newest", "latest"):
        dated.reverse()
    items = dated + undated
    for n, it in enumerate(items, 1):
        it["idx"] = n

    terms_map = terms_mod.load_terms()
    fuzzy_engine = None
    if fuzzy:
        targets = terms_mod.fuzzy_targets(terms_map)
        if targets:
            from . import fuzzy as fuzzy_mod
            if fuzzy_mod.available():
                thr = float(paths.load_config().get("fuzzy_threshold", 0.85))
                fuzzy_engine = fuzzy_mod.FuzzyCorrector(targets, threshold=thr)
            else:
                print("⚠ 未安装 pypinyin / rapidfuzz，跳过模糊纠错"
                      "（pip install pypinyin rapidfuzz）")

    if show_hits and terms_map:
        hits = {}
        for it in items:
            body = core.read_done(it["path"], 0) or ""
            for w, c in terms_mod.count_hits(body, terms_map).items():
                hits[w] = hits.get(w, 0) + c
        if hits:
            print("术语纠错命中：")
            for w, c in sorted(hits.items(), key=lambda kv: -kv[1]):
                print(f"  {w} → {terms_map[w]}　×{c}")

    stats = {}
    doc, csv_path = core.build_outputs(
        out_dir, items, engine=device_note or "既有稿件（未重新转写）",
        doc_name=doc_name, terms_map=terms_map, raw_dir=raw_dir,
        fuzzy=fuzzy_engine, stats=stats)
    core.save_meta(out_dir, items)      # 把补回来的时长固化，免得下次又要从旧索引里捞

    fhits = stats.get("fuzzy") or []
    if show_hits and fhits:
        print(f"模糊纠错命中 {len(fhits)} 处：")
        for stem, seg, target, score in fhits[:12]:
            print(f"  {seg} → {target}　[{score}]　{stem[:28]}")
        if len(fhits) > 12:
            print(f"  …另有 {len(fhits) - 12} 处")

    total = sum(i["chars"] for i in items)
    print(f"✓ 重建完成：{len(items)} 条 / {total:,} 字")
    print(f"  合集：{doc}\n  索引：{csv_path}")
    if undated:
        print(f"  ⚠ {len(undated)} 个文件名里没有日期，已排到末尾：")
        for it in undated[:5]:
            print(f"    · {it['stem']}")
    return 0
