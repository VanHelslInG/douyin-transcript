#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""dyasr 一键安装器（纯标准库，可在任何 Python 3.9+ 上直接跑）。

它做的事，按顺序：
  1. 自检系统依赖（Python / ffmpeg / 磁盘）
  2. 选定算力：CPU 还是 CUDA（可用 --device 指定，也可自动探测）
  3. 在 ~/.dyasr/venv 建独立虚拟环境（不动系统 Python）
  4. 装对应版本的 torch —— 这是唯一需要区分的步骤，装错就得整包重来
  5. 装 funasr / modelscope 与本项目本体
  6. 写默认配置、播种术语表、生成 dyasr 启动器
  7. 打印环境自检结果，并明确告知「还差什么模型、多大、要不要下载」

用法：
  python install.py                   # 自动判断 GPU
  python install.py --device cuda     # 强制 GPU（无 CUDA 会报错并给出修复办法）
  python install.py --device cpu      # 强制 CPU
  python install.py --recreate        # 删掉旧 venv 重装（换 CPU/GPU 版本时用）

退出码：0 成功 / 1 失败
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
HOME = Path(os.environ.get("DYASR_HOME") or (Path.home() / ".dyasr"))
VENV = HOME / "venv"
MIN_PY = (3, 9)

TORCH_INDEX = {
    "cuda": "https://download.pytorch.org/whl/cu126",
    "cpu": "https://download.pytorch.org/whl/cpu",
}

C_OK, C_WARN, C_ERR, C_DIM, C_END = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    # 老 conhost 不支持 ANSI，退化为纯文本
    C_OK = C_WARN = C_ERR = C_DIM = C_END = ""


def say(msg, color=""):
    print(f"{color}{msg}{C_END}" if color else msg, flush=True)


def step(n, total, msg):
    say(f"\n[{n}/{total}] {msg}", C_DIM)


def run(cmd, **kw):
    return subprocess.run(cmd, **kw)


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def detect_gpu():
    """返回 (是否有可用 NVIDIA GPU, 描述)。只用 nvidia-smi，不依赖 torch。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, "未找到 nvidia-smi"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        if out:
            return True, out.splitlines()[0]
    except Exception:
        pass
    return False, "nvidia-smi 执行失败"


def pip(args, index=None):
    cmd = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check"]
    if index:
        cmd += ["--index-url", index]
    cmd += args
    say("  $ " + " ".join(cmd), C_DIM)
    return run(cmd).returncode


def write_launcher():
    """生成启动器。同时提供 dyasr（短）与 douyin-transcript（与仓库同名，便于记忆）。"""
    bindir = HOME / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    py = venv_python()
    made = []
    for name in ("dyasr", "douyin-transcript"):
        if os.name == "nt":
            p = bindir / f"{name}.cmd"
            p.write_text(f'@echo off\r\n"{py}" -m dyasr %*\r\n', encoding="utf-8")
        else:
            p = bindir / name
            p.write_text(f'#!/bin/sh\nexec "{py}" -m dyasr "$@"\n', encoding="utf-8")
            p.chmod(0o755)
        made.append(p)
    return made


def main():
    ap = argparse.ArgumentParser(description="安装 dyasr（离线 ASR 口播转写流水线）")
    ap.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto",
                    help="算力版本：cuda=显卡加速，cpu=纯 CPU，auto=自动探测（默认）")
    ap.add_argument("--fmodel", choices=["paraformer", "sensevoice"], default="paraformer",
                    help="默认 ASR 模型档位")
    ap.add_argument("--recreate", action="store_true", help="删除已有 venv 重装")
    ap.add_argument("--python", default="", help="指定建 venv 用的 Python 解释器")
    ap.add_argument("--skip-asr", action="store_true", help="只装 CLI 核心，不装 ASR 依赖")
    args = ap.parse_args()

    total = 7
    say("dyasr 安装器", C_OK)
    say(f"  仓库：{REPO}\n  目标：{HOME}")

    # ── 1. 系统依赖 ──
    step(1, total, "检查系统依赖")
    if sys.version_info < MIN_PY:
        say(f"✘ 需要 Python {MIN_PY[0]}.{MIN_PY[1]}+，当前 {sys.version.split()[0]}", C_ERR)
        return 1
    say(f"  ✔ Python {sys.version.split()[0]} ({platform.system()})")

    if not shutil.which("ffmpeg"):
        say("  ✘ 未找到 ffmpeg —— 转写前必须装好，否则无法抽音轨", C_ERR)
        say("    Windows: winget install Gyan.FFmpeg\n"
            "    macOS  : brew install ffmpeg\n"
            "    Linux  : sudo apt install ffmpeg", C_DIM)
        say("    （可先继续安装，装好 ffmpeg 后重开终端即可）", C_WARN)
    else:
        say("  ✔ ffmpeg 已就绪")

    # ── 2. 算力版本 ──
    step(2, total, "确定算力版本")
    has_gpu, gpu_desc = detect_gpu()
    device = args.device
    if device == "auto":
        device = "cuda" if has_gpu else "cpu"
        say(f"  自动探测：{'发现 NVIDIA GPU → 走 CUDA' if has_gpu else '未发现 NVIDIA GPU → 走 CPU'}")
        if has_gpu:
            say(f"  {gpu_desc}（只想用 CPU 可加 --device cpu 重装）", C_DIM)
    elif device == "cuda" and not has_gpu:
        say("  ✘ 指定了 --device cuda，但没检测到可用的 NVIDIA GPU", C_ERR)
        say("    确认显卡驱动已装（命令行能运行 nvidia-smi）。若只想用 CPU："
            "python install.py --device cpu", C_DIM)
        return 1
    say(f"  → 将安装 {'CUDA (cu126)' if device == 'cuda' else 'CPU'} 版 torch")

    # ── 3. venv ──
    step(3, total, "准备虚拟环境")
    HOME.mkdir(parents=True, exist_ok=True)
    if args.recreate and VENV.exists():
        say("  删除旧环境…", C_DIM)
        shutil.rmtree(VENV, ignore_errors=True)
    if not venv_python().exists():
        py = args.python or sys.executable
        say(f"  创建 {VENV}", C_DIM)
        if run([py, "-m", "venv", str(VENV)]).returncode != 0:
            say("  ✘ 创建虚拟环境失败（部分 Linux 发行版需先装 python3-venv）", C_ERR)
            return 1
        pip(["--upgrade", "pip", "setuptools", "wheel"])
    else:
        say(f"  复用已存在的环境 {VENV}（换 CPU/GPU 版请加 --recreate）")

    if args.skip_asr:
        say("  跳过 ASR 依赖（--skip-asr）", C_WARN)
    else:
        # ── 4. torch（顺序关键：必须先按 index-url 装，否则会从 PyPI 拉到 CPU 版）──
        step(4, total, f"安装 torch（{device}）")
        index = TORCH_INDEX["cuda" if device == "cuda" else "cpu"]
        if pip(["torch", "torchaudio"], index=index) != 0:
            say("  ✘ torch 安装失败。常见原因：网络无法访问 download.pytorch.org", C_ERR)
            say("    国内可换镜像重试，例如（清华源，注意不保证含 cu126 构建）：\n"
                "    pip install torch torchaudio -i https://pypi.tuna.tsinghua.edu.cn/simple", C_DIM)
            return 1

        # 立刻验证 CUDA 是否真的可用 —— 这一步能提前发现驱动不匹配
        check = subprocess.run(
            [str(venv_python()), "-c",
             "import torch;print(torch.__version__, torch.cuda.is_available())"],
            capture_output=True, text=True)
        ver_cuda = (check.stdout or "").strip()
        say(f"  torch {ver_cuda}")
        if device == "cuda" and ver_cuda.endswith("False"):
            say("  ⚠ torch 装好了但 torch.cuda.is_available()=False —— "
                "多半是显卡驱动过旧。会先按 CPU 配置，修好驱动后执行：\n"
                "    dyasr config --device cuda", C_WARN)
            device = "cpu"

        # ── 5. ASR 依赖 ──
        step(5, total, "安装 funasr / modelscope")
        if pip(["-e", f"{REPO}[asr]"]) != 0:
            say("  ✘ ASR 依赖安装失败（funasr 依赖较多，网络不稳时重试一次即可）", C_ERR)
            return 1

    if args.skip_asr:
        step(5, total, "安装 dyasr 本体")
        if pip(["-e", str(REPO)]) != 0:
            say("  ✘ dyasr 本体安装失败", C_ERR)
            return 1

    # ── 6. 配置与启动器 ──
    step(6, total, "写入配置与启动器")
    cfg_path = HOME / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg.update({"device": device, "fmodel": args.fmodel})
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    launchers = write_launcher()
    say(f"  ✔ 配置 {cfg_path}")
    say(f"  ✔ 启动器 {', '.join(p.name for p in launchers)}")

    # ── 7. 自检 ──
    step(7, total, "环境自检")
    rc = run([str(venv_python()), "-m", "dyasr", "doctor"]).returncode

    say("\n" + "─" * 64)
    say("安装完成。", C_OK)
    say(f"  直接调用（无需激活环境）：\n    \"{venv_python()}\" -m dyasr doctor")
    if os.name == "nt":
        say(f"  或把 {HOME / 'bin'} 加入 PATH 后使用：dyasr doctor")
    else:
        say(f"  或加入 PATH：export PATH=\"{HOME / 'bin'}:$PATH\"")
    say("\n  下一步：\n"
        "    1) 把抖音 Cookie 存到 " + str(HOME / "cookie.txt") + "\n"
        "    2) dyasr fetch --url <博主主页>\n"
        "    3) dyasr transcribe --src <视频目录> --out ./文稿")
    return 0 if rc == 0 else 0   # 自检有问题不判安装失败，交由 doctor 的提示引导


if __name__ == "__main__":
    sys.exit(main())
