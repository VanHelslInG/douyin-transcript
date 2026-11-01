"""统一的家目录与配置读写。

所有可写状态都在 DYASR_HOME（默认 ~/.dyasr）下，仓库目录保持只读，
这样 git clone 出来的目录可以被任意多个项目共享，也可以随时删除重装。
"""
import json
import os
from pathlib import Path

# 家目录：允许用环境变量重定向（便携/多实例场景）
HOME = Path(os.environ.get("DYASR_HOME") or (Path.home() / ".dyasr"))

CONFIG_FILE = HOME / "config.json"
VENV_DIR = HOME / "venv"
VENDOR_DIR = HOME / "vendor"
LOG_DIR = HOME / "logs"
CACHE_DIR = HOME / "cache"
SEED_TERMS = HOME / "terms.tsv"

DEFAULTS = {
    # auto = 有可用 CUDA 就走 GPU，否则 CPU；也可硬指定 cuda / cpu
    "device": "auto",
    # funasr 模型档位：paraformer（默认，快且准）/ sensevoice（更快，标点依赖 ct-punc）
    "fmodel": "paraformer",
    # 逗号或空格分隔的热词，可留空
    "hotword": "",
    # 下载器源码仓库与锁定的 commit
    "douk_repo": "https://github.com/JoeanAmier/TikTokDownloader.git",
    "douk_commit": "e327b9549d284a8b1613566f949fe2dbb8a6c2a1",
    # 小于该字数的转写结果视为无效（防中断半成品）
    "min_chars": 100,
    # 单批送模型的音频长度上限（秒），显存紧张时调小
    "batch_size_s": 300,
}


def ensure_home() -> Path:
    for d in (HOME, VENDOR_DIR, LOG_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return HOME


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f) or {})
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> Path:
    ensure_home()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return CONFIG_FILE


def set_config(**kwargs) -> dict:
    cfg = load_config()
    cfg.update({k: v for k, v in kwargs.items() if v is not None})
    save_config(cfg)
    return cfg


def vendor_repo_dir() -> Path:
    """下载器源码所在目录（git clone 目标）。

    允许用 DYASR_VENDOR 指到一个已经存在的上游目录 —— 内网/无法访问 GitHub 时，
    或者想复用别处已经装好的副本（含 .venv）时很有用。
    """
    override = os.environ.get("DYASR_VENDOR")
    if override:
        return Path(override)
    return VENDOR_DIR / "TikTokDownloader"


def cookie_path() -> Path:
    return HOME / "cookie.txt"


def venv_python() -> Path:
    """当前 venv 的解释器路径（跨平台）。"""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"
