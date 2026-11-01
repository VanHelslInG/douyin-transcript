# 安装与部署

## 前置依赖

| 依赖 | 要求 | 为什么需要 |
|---|---|---|
| Python | 3.9+（推荐 3.10–3.12） | `install.py` 与 CLI 本体 |
| ffmpeg + ffprobe | 任意新版本，需在 PATH | 抽 16k 单声道音轨、读时长 |
| git | 任意版本 | 首次采集时拉取下载器源码 |
| NVIDIA 驱动 | 仅 GPU 版需要，能跑 `nvidia-smi` | CUDA 加速 |
| 磁盘 | ≥ 10 GB 空闲 | 模型约 1.2 GB + 视频体积 |
| uv | 可选 | 有则采集层自动用它建隔离环境，没有就退化成 venv+pip |

装 ffmpeg：

```bash
# Windows
winget install Gyan.FFmpeg
# macOS
brew install ffmpeg
# Ubuntu / Debian
sudo apt install ffmpeg
```

## 标准安装（推荐，交给 Agent 也行）

```bash
git clone <REPO_URL> ~/.dyasr/repo
cd ~/.dyasr/repo
python install.py --device cuda      # 或 --device cpu
```

安装器会依次：建 venv → 按算力装对应 torch → 装 funasr/modelscope → 装本体 →
写配置与启动器 → 跑一次 `dyasr doctor`。

装完的目录：

```
~/.dyasr/
├─ repo/          # 本仓库（可 git pull 升级）
├─ venv/          # 独立虚拟环境
├─ bin/dyasr      # 启动器（Windows 为 dyasr.cmd）
├─ vendor/        # 采集器源码（首次 fetch 时自动 clone）
├─ config.json    # 配置
├─ terms.tsv      # 术语纠错表
└─ cookie.txt     # 抖音 Cookie（需你自己放）
```

## 切换 CPU ⇄ GPU

torch 的构建版本由 `--index-url` 决定，**不能靠 pip 原地改**，必须重建环境：

```bash
cd ~/.dyasr/repo
python install.py --device cuda --recreate
```

`--recreate` 会删掉 `~/.dyasr/venv` 重来。`config.json`、术语表、稿件都不受影响。

若只是临时想换个算法跑一次，不必重装：

```bash
dyasr transcribe --src ./videos/xxx --out ./文稿 --device cpu
```

> 注意：CPU 版 torch 上强制 `--device cuda` 会直接报错退出，而不是悄悄降级 —— 这是刻意的，
> 避免你以为在跑 GPU 实际上在跑 CPU。

## 离线 / 内网安装

`install.py` 依赖 pip 网络源，内网环境请手工装。思路是「联网机器下 wheel，离线机器装 wheel」。

```bash
# ① 在联网机器上（注意版本要与目标机器 Python 一致，跨平台 wheel 不通用）
pip download -d ./wheels torch torchaudio \
  --index-url https://download.pytorch.org/whl/cpu
pip download -d ./wheels funasr modelscope

# ② 拷 wheels 到目标机器后
python -m venv ~/.dyasr/venv
~/.dyasr/venv/bin/pip install --no-index --find-links ./wheels torch torchaudio funasr modelscope
cd ~/.dyasr/repo && ~/.dyasr/venv/bin/pip install --no-index --no-build-isolation -e .
~/.dyasr/venv/bin/python -m dyasr config --device cpu
```

模型同样可离线：在任意联网机器跑一次转写，把 `~/.cache/modelscope/models` 整个拷到目标机器
同一位置即可。也可单独下：

```bash
modelscope download --model iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
modelscope download --model iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
modelscope download --model iic/punc_ct-transformer_cn-en-common-vocab471067-large
```

**采集层需要 git 能访问 GitHub**。完全断网的机器上，把 `~/.dyasr/vendor/TikTokDownloader`
从别处整个拷过来即可，`fetch` 检测到 `main.py` 存在就会跳过 clone。

## 卸载

```bash
rm -rf ~/.dyasr                 # 全部（含稿件！先自己备份 文稿/）
rm -rf ~/.dyasr/venv            # 只删环境，保留配置与稿件
```

模型缓存在 `~/.cache/modelscope`，占约 1.2 GB，如要腾空间可单独删。

## 升级

```bash
cd ~/.dyasr/repo
git pull
~/.dyasr/venv/bin/pip install -e .     # Windows: %USERPROFILE%\.dyasr\venv\Scripts\pip.exe
```

采集器源码固定在 `config.json` 的 `douk_commit`。上游更新后想跟随，把该字段清空即可
（下次 fetch 用默认分支，但行为可能与文档不符）。
