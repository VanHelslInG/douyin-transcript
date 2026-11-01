"""流水线公用逻辑：探测、清洗、排序、进度页、汇总产物。

刻意不依赖 funasr / torch，这个模块在只有 Python 标准库的环境里也能跑，
所以 `dyasr rebuild` 可以在没装模型、没显卡的机器上重新生成全部产物。
"""
import csv
import html
import os
import re
import subprocess
import time

from . import terms as terms_mod

MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi",
             ".m4a", ".wav", ".mp3", ".flac", ".aac", ".ogg"}

# 下载器产物命名：2025-07-30 17.22.39-标题.mp4
_STEM_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ _T]"
    r"(?P<h>\d{1,2})[.\-:](?P<m>\d{2})(?:[.\-:](?P<s>\d{2}))?"
    r"\s*[-_ ]\s*(?P<title>.*)$"
)
_DATE_ONLY_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})")


# ──────────────────────────── 探测 ────────────────────────────

def probe_duration(path):
    """用 ffprobe 读时长（秒，float）。失败返回 0.0，不抛异常。"""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return round(float(p.stdout.strip()), 1)
    except Exception:
        return 0.0


def parse_stem(stem: str) -> dict:
    """从文件名解析发布时间与标题。

    返回 {"date": "YYYY-MM-DD", "time": "HH:MM", "title": str, "key": 可排序元组}。
    解析不出日期时 key 为 None，排序时落到底部（不假装知道时间）。
    """
    m = _STEM_RE.match(stem)
    if m:
        hh, mm = int(m.group("h")), int(m.group("m"))
        ss = int(m.group("s") or 0)
        return {
            "date": m.group("date"),
            "time": f"{hh:02d}:{mm:02d}",
            "title": (m.group("title") or "").strip(),
            "key": (m.group("date"), hh, mm, ss),
        }
    m = _DATE_ONLY_RE.match(stem)
    if m:
        return {"date": m.group("date"), "time": "", "title": stem, "key": (m.group("date"), 0, 0, 0)}
    return {"date": "", "time": "", "title": stem, "key": None}


def sort_media(paths):
    """按文件名里的发布日期排序（升序＝由旧到新）。无日期的排末尾，按名字兜底。

    ⚠️ 这正是不用 mtime 的原因：mtime 反映的是下载批次而不是发布顺序，
    按它排序会让稿件序号与博主真实时间线错位，做趋势分析会直接得错结论。
    """
    dated, undated = [], []
    for p in paths:
        info = parse_stem(os.path.splitext(os.path.basename(p))[0])
        (dated if info["key"] else undated).append((info, p))
    dated.sort(key=lambda t: (t[0]["key"], os.path.basename(t[1])))
    undated.sort(key=lambda t: os.path.basename(t[1]))
    return [p for _, p in dated] + [p for _, p in undated]


def list_media(src):
    """递归收集音视频文件（下载器会强制多建一层 UID_昵称_发布作品 子目录）。"""
    found = []
    for root, _dirs, files in os.walk(src):
        for f in files:
            if os.path.splitext(f)[1].lower() in MEDIA_EXT:
                found.append(os.path.join(root, f))
    return sort_media(found)


def list_raw(raw_dir):
    """只认 .txt，忽略伴生的 _timeline.json 等文件。"""
    if not os.path.isdir(raw_dir):
        return []
    return sorted(os.path.join(raw_dir, f) for f in os.listdir(raw_dir)
                  if f.lower().endswith(".txt"))


# ──────────────────────────── 文本 ────────────────────────────

def clean_text(t: str, min_chars: int = 0) -> str:
    t = re.sub(r"\[说话人\d+\]\s*", "", t or "")
    t = t.replace("[低置信]", "").replace("（听不清）", "")
    t = re.sub(r"[ \t]+", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def read_done(txt_path, min_chars: int = 100):
    """读已完成的稿件；不存在、读失败或字数不足都返回 None（视为未完成）。"""
    if not os.path.exists(txt_path):
        return None
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            t = clean_text(f.read())
        return t if len(t) >= min_chars else None
    except Exception:
        return None


def char_count(t: str) -> int:
    return len(re.sub(r"\s", "", t or ""))


# ──────────────────────── 终端对齐（CJK 占两格）────────────────────────

def width(s: str) -> int:
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, n: int, align="left") -> str:
    s = str(s)
    gap = " " * max(0, n - width(s))
    return gap + s if align == "right" else s + gap


def truncate(s: str, n: int) -> str:
    """按显示宽度截断，超出补省略号。"""
    if width(s) <= n:
        return s
    out, w = "", 0
    for ch in s:
        cw = 2 if __import__("unicodedata").east_asian_width(ch) in "WF" else 1
        if w + cw > n - 1:
            break
        out, w = out + ch, w + cw
    return out + "…"


# ──────────────────────── 选择与排序 ────────────────────────

PAGE_ORDER_FILE = "页面顺序.txt"


def parse_selection(spec, items):
    """把 "1,3,5-8" / "all" / 空 解析成 items 子集。

    也接受文件名关键字（含中文标题的一段即可），方便 Agent 用标题指定。
    """
    if not spec or str(spec).strip().lower() in ("all", "*", "全部"):
        return list(items)

    want_idx, want_kw = set(), []
    for raw in str(spec).replace("，", ",").split(","):
        token = raw.strip()
        if not token:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
        if m:
            a, b = sorted((int(m.group(1)), int(m.group(2))))
            want_idx.update(range(a, b + 1))
        elif token.isdigit():
            want_idx.add(int(token))
        else:
            want_kw.append(token)

    picked, seen = [], set()
    for it in items:
        if it["idx"] in want_idx or any(k in it["stem"] for k in want_kw):
            if it["idx"] not in seen:
                picked.append(it)
                seen.add(it["idx"])

    missing = sorted(want_idx - seen)
    if missing:
        raise SystemExit(f"✗ 编号超出范围或不存在：{', '.join(map(str, missing))}"
                         f"（当前共 {len(items)} 条，编号 1–{len(items)}）")
    if want_kw and not picked:
        raise SystemExit(f"✗ 没有文件名匹配：{'、'.join(want_kw)}")
    return picked


def load_page_order(src_dir):
    """读取可选的「页面顺序.txt」：每行一个标题关键字，顺序即抖音作品页顺序。

    存在的意义：抖音「置顶」作品在时间线上可能是很旧的，下载器又不返回置顶标记，
    所以仅靠文件名无法还原 App 里看到的顺序。Agent 若能用浏览器读到作品页顺序，
    就写进这个文件，`list` / `transcribe` 会以它为准（01 = 页面第一条 = 置顶）。
    """
    path = os.path.join(src_dir, PAGE_ORDER_FILE)
    if not os.path.exists(path):
        return []
    lines = []
    for raw in open(path, "r", encoding="utf-8-sig").read().splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            lines.append(s)
    return lines


def reorder_by_page(items, page_lines):
    """按页面顺序重排 items，并把 idx 重编为 1..N。未匹配的接在后面。"""
    if not page_lines:
        return items
    used, ordered = set(), []
    for line in page_lines:
        for it in items:
            if it["stem"] in used:
                continue
            if line in it["stem"] or it["stem"] in line:
                ordered.append(it)
                used.add(it["stem"])
                break
    ordered += [it for it in items if it["stem"] not in used]
    for n, it in enumerate(ordered, 1):
        it["idx"] = n
    return ordered


def human_size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_sec(s) -> str:
    s = int(max(0, s or 0))
    if s < 60:
        return f"{s} 秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {s} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


# ──────────────────────────── 进度页 ────────────────────────────

PROGRESS_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>dyasr 转写进度</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;padding:28px 32px;background:#1c1c1a;color:#f1efe8;
font:14px/1.6 system-ui,"Segoe UI","Microsoft YaHei",sans-serif}}
h1{{font-size:17px;font-weight:500;margin:0 0 4px}}
.muted{{color:#b4b2a9;font-size:13px}}
.bar{{height:14px;border-radius:7px;background:#3a3a37;overflow:hidden;margin:18px 0 10px}}
.fill{{height:100%;background:#1d9e75;border-radius:7px;transition:width .4s}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 26px}}
.card{{background:#2c2c2a;border:1px solid #444441;border-radius:10px;padding:12px 14px}}
.card .k{{font-size:12px;color:#b4b2a9}}
.card .v{{font-size:20px;font-weight:500;margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:#b4b2a9;font-weight:400;padding:7px 8px;border-bottom:1px solid #444441}}
td{{padding:7px 8px;border-bottom:1px solid #2f2f2c}}
.done{{color:#5dcaa5}} .run{{color:#85b7eb}} .wait{{color:#888780}} .fail{{color:#f09595}}
.dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;vertical-align:middle}}
.d1{{background:#1d9e75}} .d2{{background:#378add}} .d3{{background:#5f5e5a}} .d4{{background:#e24b4a}}
.now{{background:#0c447c}}
</style></head><body>
<h1>dyasr 转写进度</h1>
<div class="muted">{updated}</div>
<div class="bar"><div class="fill" style="width:{pct}%"></div></div>
<div class="muted">{pct}%　·　{bartext}</div>
<div class="stats">
<div class="card"><div class="k">已完成</div><div class="v">{done}</div></div>
<div class="card"><div class="k">待处理</div><div class="v">{wait}</div></div>
<div class="card"><div class="k">失败</div><div class="v">{fail}</div></div>
<div class="card"><div class="k">预计剩余</div><div class="v">{eta}</div></div>
</div>
<table><thead><tr><th style="width:52px">#</th><th>文件</th><th style="width:86px">时长</th>
<th style="width:86px">字数</th><th style="width:110px">状态</th></tr></thead><tbody>
{rows}
</tbody></table>
<div class="muted" style="margin-top:18px">每 5 秒自动刷新；关闭页面不影响后台转写。</div>
</body></html>
"""

_STATUS = {
    "done": ("已完成", "done", "d1"),
    "run": ("转写中", "run", "d2"),
    "wait": ("等待", "wait", "d3"),
    "fail": ("失败", "fail", "d4"),
}


def write_progress(out_dir, items, started_at, processed_audio, processed_wall, total_audio):
    done_n = sum(1 for i in items if i["status"] == "done")
    fail_n = sum(1 for i in items if i["status"] == "fail")
    total = len(items)
    pct = round(done_n / total * 100, 1) if total else 0

    # 用「已处理音频秒数 / 已耗墙钟」反推剩余，比按文件数平均更贴近长视频场景
    speed = (processed_wall / processed_audio) if processed_audio > 0 else 0
    remain = total_audio - processed_audio
    eta = fmt_sec(remain * speed) if speed > 0 and remain > 0 else "—"

    rows = []
    for i in items:
        st, cls, dot = _STATUS[i["status"]]
        name = html.escape(i["name"])
        if len(name) > 62:
            name = name[:60] + "…"
        rows.append(
            f'<tr class="{"now" if i["status"] == "run" else ""}"><td>{i["idx"]}</td>'
            f'<td class="{"run" if i["status"] == "run" else ""}">{name}</td>'
            f'<td>{int(i["dur"] // 60)}:{int(i["dur"] % 60):02d}</td>'
            f'<td>{i["chars"] or "—"}</td>'
            f'<td class="{cls}"><span class="dot {dot}"></span>{st}</td></tr>'
        )

    page = PROGRESS_HTML.format(
        updated=time.strftime("%Y-%m-%d %H:%M:%S"),
        pct=pct,
        bartext=f"{done_n} / {total} 条　·　已用时 {fmt_sec(time.time() - started_at)}",
        done=done_n, wait=total - done_n - fail_n, fail=fail_n, eta=eta,
        rows="\n".join(rows),
    )
    path = os.path.join(out_dir, "进度.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


# ──────────────────────────── 汇总产物 ────────────────────────────

def build_outputs(out_dir, items, engine="funasr/paraformer", doc_name="口播稿件合集.md",
                  terms_map=None, raw_dir=None):
    """从 items 生成「合集.md」+「索引.csv」。

    items 里 status=done 的条目会去 raw_dir/<stem>.txt 读正文；缺失则跳过。
    terms_map 只作用于输出，不写回 raw。
    """
    raw_dir = raw_dir or os.path.join(out_dir, "raw")
    terms_map = terms_map or {}
    ok = [i for i in items if i["status"] == "done"]

    total_sec = sum(i["dur"] for i in items)
    total_char = sum(i["chars"] for i in items)

    md = [
        f"# 口播稿件合集（{len(ok)}/{len(items)} 条）", "",
        f"转写引擎：{engine}　·　总时长 {fmt_sec(total_sec)}　·　总字数 {total_char:,}",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}", "",
        "| # | 发布 | 标题 | 时长 | 字数 |", "|---|---|---|---|---|",
    ]
    for it in items:
        info = it.get("info") or parse_stem(it["stem"])
        md.append(f"| {it['idx']} | {info['date']} {info['time']} | "
                  f"{info['title']} | {int(it['dur'])}s | {it['chars']} |")
    md.append("")

    for it in items:
        if it["status"] != "done":
            continue
        body = read_done(os.path.join(raw_dir, it["stem"] + ".txt"))
        if not body:
            continue
        info = it.get("info") or parse_stem(it["stem"])
        body = terms_mod.apply_terms(body, terms_map)
        md += ["---", "",
               f"## {it['idx']:02d}. {info['title'] or it['stem']}", "",
               f"> 发布 {info['date']} {info['time']}　·　时长 {fmt_sec(it['dur'])}　·　{it['chars']} 字",
               "", body, ""]

    doc_path = os.path.join(out_dir, doc_name)
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    csv_path = os.path.join(out_dir, "索引.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["序号", "发布日期", "发布时间", "文件名", "时长秒", "字数", "状态"])
        for it in items:
            info = it.get("info") or parse_stem(it["stem"])
            w.writerow([it["idx"], info["date"], info["time"], it["name"],
                        int(it["dur"]), it["chars"], it["status"]])

    return doc_path, csv_path


def make_items(paths, min_chars=100, raw_dir=None):
    """把媒体路径列表整理成 items，并预扫描断点（已有合格稿件的直接标 done）。"""
    items = []
    for i, p in enumerate(paths, 1):
        stem = os.path.splitext(os.path.basename(p))[0]
        info = parse_stem(stem)
        it = {"idx": i, "name": os.path.basename(p), "stem": stem, "path": p,
              "info": info, "dur": probe_duration(p), "chars": 0, "status": "wait"}
        if raw_dir:
            t = read_done(os.path.join(raw_dir, stem + ".txt"), min_chars)
            if t:
                it["status"], it["chars"] = "done", char_count(t)
        items.append(it)
    return items


# ──────────────────────────── 元数据 ────────────────────────────

META_NAME = ".dyasr_meta.json"


def save_meta(out_dir, items):
    """把「文件名 → 时长」存下来。

    raw/*.txt 里只有正文，没有时长。而 mtime 不可靠、原始视频又被删掉时，
    rebuild 就需要这份旁挂元数据才能继续显示时长，否则时长列永远是 0。
    """
    import json
    data = {i["stem"]: {"dur": i.get("dur", 0), "name": i.get("name", "")}
            for i in items}
    path = os.path.join(out_dir, META_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


def load_meta(out_dir) -> dict:
    import json
    path = os.path.join(out_dir, META_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def load_durations_from_index(out_dir) -> dict:
    """兼容旧产物：没有 meta 时，从上一版「索引.csv」里捞回时长。

    存在的意义是让「先有稿件、后接管工具」的人不必丢掉时长列，
    也不会因为要补时长而重新转写一遍。
    """
    path = os.path.join(out_dir, "索引.csv")
    if not os.path.exists(path):
        return {}
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                name = row.get("文件名") or ""
                dur = row.get("时长秒") or "0"
                if not name:
                    continue
                stem = os.path.splitext(os.path.basename(name))[0]
                try:
                    d = float(dur)
                except ValueError:
                    d = 0.0
                if d:
                    out[stem] = {"dur": d, "name": name}
    except Exception:
        return {}
    return out
