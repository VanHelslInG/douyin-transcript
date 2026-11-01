"""环境自检：把「能不能跑」和「还差什么」一次性说清楚。

设计给两类读者：
  · 人类：dyasr doctor 直接看对齐的勾叉列表
  · Agent：dyasr doctor --json 拿结构化结果（blockers / warnings / actions），
    据此决定该问用户什么，而不是靠猜。

约定：blockers 会让流水线必然失败；warnings 能干但会踩坑；
actions 是需要用户点头的动作（主要是首次下载模型）。
"""
import json
import os
import shutil
import subprocess
import sys

from . import cookie as cookie_mod
from . import paths, terms as terms_mod, transcribe


def _sh(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() or p.stderr.strip()
    except Exception:
        return ""


def _width(s: str) -> int:
    """按终端显示宽度算（CJK 占两格），否则表格永远对不齐。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _width(s))


def cookie_info():
    return cookie_mod.status()


def collect(fmodel=None):
    cfg = paths.load_config()
    fmodel = fmodel or cfg.get("fmodel", "paraformer")
    checks, blockers, warnings, actions = [], [], [], []

    def add(name, ok, detail, level="block", kind="check"):
        """level: block=阻断 / warn=提醒（ok 为 False 时生效）；kind=info 表示纯信息项。"""
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "kind": kind})
        if not ok and kind != "info":
            (blockers if level == "block" else warnings).append(f"{name}：{detail}")

    # Python
    py_ok = sys.version_info >= (3, 9)
    add("Python", py_ok, f"{sys.version.split()[0]} ({sys.executable})")

    # torch / GPU
    gpu = {"installed": False, "version": "", "cuda": False, "device": "", "vram_gb": 0}
    try:
        import torch
        gpu["installed"] = True
        gpu["version"] = torch.__version__
        gpu["cuda"] = bool(torch.cuda.is_available())
        if gpu["cuda"]:
            gpu["device"] = torch.cuda.get_device_name(0)
            gpu["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, 1)
    except Exception:
        pass
    cfg_device = cfg.get("device", "auto")
    if cfg_device in ("cuda", "gpu") and not gpu["cuda"]:
        add("设备配置", False, f"配置要求 GPU，但当前 torch 看不到可用显卡（实际会失败）")
    else:
        eff = gpu["cuda"] if cfg_device == "auto" else (cfg_device == "cuda")
        label = f"{gpu['device']} {gpu['vram_gb']}GB" if eff else "CPU"
        add("算力", True,
            f"配置={cfg_device}｜实际={label}｜torch {gpu['version'] or '未安装'}")

    # ffmpeg
    ff = shutil.which("ffmpeg")
    add("ffmpeg", bool(ff), _sh(["ffmpeg", "-version"]).splitlines()[0] if ff else
        "未找到。Windows: winget install Gyan.FFmpeg；macOS: brew install ffmpeg；Linux: apt install ffmpeg")
    fp = shutil.which("ffprobe")
    add("ffprobe", bool(fp), "已就绪" if fp else "未找到，通常与 ffmpeg 同包安装")

    # funasr
    funasr_ver = ""
    try:
        import funasr
        funasr_ver = getattr(funasr, "__version__", "?")
    except Exception:
        pass
    add("funasr", bool(funasr_ver), f"版本 {funasr_ver}" if funasr_ver else
        "未安装。请重跑安装脚本：python install.py --device cpu|cuda")

    # 模型缓存
    ms = transcribe.model_status(fmodel)
    cached = [(m, mb) for m, ok, mb in ms if ok]
    miss = transcribe.missing_models(fmodel)
    if miss:
        mb = sum(m for _m, m in miss)
        add("ASR 模型", False, f"缺 {len(miss)}/{len(ms)} 个（约 {mb} MB 需下载）", level="warn")
        actions.append({"type": "download_models", "fmodel": fmodel,
                        "size_mb": mb, "models": [m for m, _ in miss]})
    else:
        add("ASR 模型", True, f"{fmodel} 全部 {len(ms)} 个已在本地缓存")

    # 采集器
    repo = paths.vendor_repo_dir()
    have_repo = (repo / "main.py").exists()
    add("采集器源码", True, str(repo) if have_repo
        else f"尚未下载，首次执行 dyasr fetch 时自动 clone 到 {paths.VENDOR_DIR}",
        kind="info")
    add("git", bool(shutil.which("git")), "已就绪" if shutil.which("git") else
        "未找到，采集层需要 git 来获取下载器源码")
    add("uv", bool(shutil.which("uv")), "已就绪" if shutil.which("uv") else
        "未安装（可选）。没有 uv 时会退化为独立 venv + pip，速度慢一些", level="warn")

    # Cookie
    ck = cookie_info()
    if not ck["present"]:
        add("Cookie", False, "未配置。让 Agent 用浏览器导出后执行 dyasr cookie import --file …",
            level="warn")
    elif ck["missing"]:
        add("Cookie", False,
            "缺关键键 " + "、".join(ck["missing"]) +
            "（多半只导出了 document.cookie，HttpOnly 的 sessionid 读不到）", level="warn")
    elif ck["days_left"] is not None and ck["days_left"] < 0:
        add("Cookie", False, f"已过期（{ck['expires_at']}），需重新导出", level="warn")
    elif ck["days_left"] is not None and ck["days_left"] < 7:
        add("Cookie", True,
            f"{ck['expires_at']} 到期，仅剩 {ck['days_left']} 天，建议提前更换", level="warn")
    else:
        add("Cookie", True, f"{len(ck['keys'])} 个键" +
            (f"，{ck['expires_at']} 到期" if ck["expires_at"] else ""))

    # 家目录与磁盘
    paths.ensure_home()
    try:
        du = shutil.disk_usage(str(paths.HOME))
        free_gb = round(du.free / 1024 ** 3, 1)
        add("磁盘", free_gb > 5, f"{paths.HOME} 所在分区剩余 {free_gb} GB" +
            ("" if free_gb > 5 else "（建议 ≥10 GB：模型 + 视频）"), level="warn")
    except Exception:
        pass

    terms_mod.seed_if_missing()
    add("术语表", True, f"{paths.SEED_TERMS}（可自行编辑）")

    return {
        "ok": not blockers,
        "python": sys.version.split()[0],
        "home": str(paths.HOME),
        "config": cfg,
        "gpu": gpu,
        "cookie": ck,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "actions": actions,
    }


def doctor(as_json=False, fmodel=None):
    r = collect(fmodel)
    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1

    mark = {True: "✔", False: "✘"}
    print(f"\ndyasr 环境自检　·　家目录 {r['home']}\n" + "─" * 64)
    for c in r["checks"]:
        m = "·" if c.get("kind") == "info" else mark[c["ok"]]
        print(f"  {m} {_pad(c['name'], 14)} {c['detail']}")
    print("─" * 64)

    if r["actions"]:
        for a in r["actions"]:
            if a["type"] == "download_models":
                print(f"→ 需要下载 {len(a['models'])} 个 ASR 模型（约 {a['size_mb']} MB），"
                      "首次转写时自动完成。")
    if r["warnings"]:
        print("\n提醒：")
        for w in r["warnings"]:
            print(f"  · {w}")
    if r["blockers"]:
        print("\n阻断项（必须先解决）：")
        for b in r["blockers"]:
            print(f"  ✘ {b}")
        print("\n修复后重跑：dyasr doctor")
        return 1

    print("\n✔ 环境就绪，可以开始：dyasr fetch --url <博主主页>")
    return 0
