# douyin-transcript · 抖音口播稿

[![smoke](https://github.com/VanHelslInG/douyin-transcript/actions/workflows/smoke.yml/badge.svg)](https://github.com/VanHelslInG/douyin-transcript/actions/workflows/smoke.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

<img src="docs/demo.png" alt="dyasr 实际运行：环境自检 → 单条下载(72.6MB→14.8MB) → 作品清单 → GPU 转写(66x 实时) → 自动清理" width="100%">

<sub>↑ 真实运行输出：自检 → 下一条 → 转音轨 → 转写 → 自动删源（RTX 3090，16 分钟音频 15 秒）</sub>

把**抖音博主**的作品批量下载到本地，离线转写成可检索、可分析的口播文字稿。
下载 → 抽音轨 → 本地 ASR → 逐条 txt + 合集 md + 索引 csv。

适合：拆解对标账号的内容结构、做文案学习素材、把口播视频变成可搜索的文本库。

```
抖音链接 ──→ 采集 ──→ 转写 ──→ 产物
            DouK-Downloader / FunASR(paraformer+VAD+标点) / raw/*.txt + 合集.md + 索引.csv
```

- **全程本地**：视频和文字都不出本机，不经过任何第三方 API。
- **GPU 优先**：RTX 3090 上约 **66 倍实时**（16 分钟音频 15 秒转完），CPU 约 7 倍。
- **默认只下你要的那一条**，不会偷偷把整个账号几十 GB 拉下来。
- **断点续跑**：中断后重跑不重复劳动。
- **转完自动删源**：只留文字，磁盘占用降到 1/10。
- **两层纠错**：`terms.tsv` 精确替换已知错法，`hotwords.txt` 用拼音相似度抓同音错字
  （`名股实债`、`婚链市场`、`边际效勇` 这类不用穷举也能改对）。

> 命令有两个等价名字：`douyin-transcript`（全名）和 `dyasr`（短名，DouYin ASR）。
> 下面统一用 `dyasr`。

## 给 AI Agent 一句话

把下面这句丢给你的 Agent（Claude Code / WorkBuddy / Cursor / 任何能读 `SKILL.md` 的）：

```
从 https://github.com/VanHelslInG/douyin-transcript 安装 douyin-transcript，
然后把这个视频转成文字稿：https://www.douyin.com/jingxuan?modal_id=xxxx
```

它会：装依赖 → **自己开浏览器**（你只需扫码登录一次）→ 取 Cookie →
只下这一条 → 转音轨 → 转写 → 自动删源文件。

仓库根的 [`SKILL.md`](SKILL.md) 就是给 Agent 看的执行手册，包含全部踩过的坑
（浏览器会话会随命令退出、必须用 cookie API 而不是 `document.cookie`、采集器是交互式 TUI 等）。

## 人工安装

```bash
git clone https://github.com/VanHelslInG/douyin-transcript ~/.dyasr/repo
cd ~/.dyasr/repo
python install.py --device cuda        # 没有 N 卡就 --device cpu
```

装完（Windows 下标 `<VENV>` 为 `%USERPROFILE%\.dyasr\venv\Scripts\python.exe`）：

```bash
<VENV> -m dyasr doctor                          # 环境自检，缺什么会直说
<VENV> -m dyasr cookie import --file cookies.json   # 浏览器导出的登录态（见下）
<VENV> -m dyasr fetch --video "https://www.douyin.com/video/<id>" --out ./videos   # 单条
<VENV> -m dyasr fetch --url "https://www.douyin.com/user/<sec_uid>" --out ./videos  # 整号
<VENV> -m dyasr list --src "./videos/Download/UIDxxx_昵称_发布作品"
<VENV> -m dyasr transcribe --src "<同上>" --out ./文稿 --select "1,3,5-8"
<VENV> -m dyasr rebuild --out ./文稿             # 可选：纠错 + 按发布时间重排
```

前置：Python 3.9+、ffmpeg、git。GPU 版另需 NVIDIA 驱动。磁盘留 10 GB。

## 关于 Cookie

登录态是采集的唯一门槛，dyasr 支持四种导出格式，自动识别：

| 来源 | 怎么拿 |
|---|---|
| Playwright | `context.storage_state(path="dy.json")` |
| Puppeteer / 浏览器插件 | `page.cookies()` 存成 JSON 数组 |
| Netscape cookies.txt | 各类 Cookie 导出扩展 |
| 请求头字符串 | F12 → Network → 复制 `Cookie:` 后面那一段 |

```bash
<VENV> -m dyasr cookie import --file dy.json
<VENV> -m dyasr cookie status
```

⚠️ 必须用浏览器上下文的 cookie 接口导出，**不能读 `document.cookie`** ——
`sessionid`、`sid_tt` 是 HttpOnly，JS 拿不到，导出来是废的（`import` 会直接提示缺键）。

## 挑着转，不转全部

```bash
<VENV> -m dyasr list --src ./videos/Download/UIDxxx_昵称_发布作品
```

```
  #   发布        时长    状态    标题
  01  2026-10-21  15:47   待转写  诺奖经济学如何解释彩礼
  02  2026-10-09  13:12   待转写  谁在婚恋中明股实债
  ...
```

`--select "1,3,5-8"` 或 `--select all`。编号默认按发布时间倒序；
把抖音作品页的可见顺序写进 `<媒体目录>/页面顺序.txt`，`01` 就变成**置顶那条**，
与手机上看到的顺序一致（下载器本身不返回置顶标记，这是唯一的还原方式）。

**转写成功后源视频会自动删除**并报告释放空间——稿件和时长都已落盘，视频只剩占地方。
失败项的视频会保留以便重试。想全部留着加 `--keep-videos`。

## 产物长什么样

```
文稿/
├─ raw/2025-07-30 17.22.39-标题.txt      # 一条一文件，ASR 原文
├─ 口播稿件合集.md                        # 全文汇总，按发布时间排序
├─ 索引.csv                              # 序号/日期/时长/字数/状态
├─ 进度.html                             # 转写进度（5 秒自刷新）
└─ .dyasr_meta.json                      # 时长旁挂，视频删了也不丢时长
```

## 命令

| 命令 | 作用 |
|---|---|
| `dyasr doctor [--json]` | 环境自检；`--json` 供 Agent 解析 |
| `dyasr cookie import --file <f>` | 导入浏览器导出的登录态（自动识别格式） |
| `dyasr cookie status` / `clear` | 查看有效期 / 清除 |
| `dyasr fetch --video <作品链接>` | 下载单条作品（默认；自动转音轨删视频） |
| `dyasr fetch --url <主页>` | 下载整个账号（几十条＝几个 GB，先问用户） |
| `dyasr list --src <目录> [--json]` | 列作品清单，按编号挑 |
| `dyasr transcribe --src <目录> --out <目录> [--select …] [--fmodel seaco]` | 按选择转写，默认删源视频 |
| `dyasr clean --src <目录> [--dry-run]` | 手动清理视频释放空间 |
| `dyasr rebuild --out <目录> [--order desc]` | 只重建产物，不跑模型 |
| `dyasr config [--device cpu\|cuda\|auto]` | 查看/修改配置 |
| `dyasr terms [--show\|--hotwords\|--edit]` | 术语表（精确）与模糊热词表 |

退出码：`0` 成功 / `1` 有失败项或环境阻断 / `2` 缺输入。

## 为什么不直接用一个脚本

因为踩过的坑都在细节里，而细节决定它是「能用」还是「放着不动」：

| 坑 | 现象 | 这里的处理 |
|---|---|---|
| Cookie 要人肉去 F12 翻 | 用户第一步就卡住 | 支持四种导出格式自动识别；Agent 用浏览器接口导出（含 HttpOnly） |
| 采集器是交互式 TUI | 无人值守时永远卡在等输入 | 锁死菜单号 `5 1 1`、写库跳过免责声明、`stdin=DEVNULL` |
| 逐文件重新加载模型 | 每条白扔 40 秒，GPU 只跑 17% | 模型提到循环外，一次加载全批复用，**快 10 倍** |
| 按文件 mtime 排序 | 稿件序号＝下载批次，不是发布时间 | 解析文件名日期前缀；支持按作品页顺序还原置顶 |
| 源视频吃掉几十 GB | 转完就再也不会看 | 转写成功后自动删除并报告释放空间 |
| ASR 系统性错字 | 行业术语、人名必错 | 可维护的 TSV 纠错表，`rebuild` 套用，不动原文 |
| 装错 torch 版本 | 装了 CPU 版却以为在跑 GPU | 安装器显式询问 + 装完立刻验证 `cuda.is_available()` |

更多细节见 `references/TROUBLESHOOT.md`。

## 文档

- `SKILL.md` —— Agent 执行手册（装、问、跑、排障）
- `references/SETUP.md` —— 安装、换算力、离线部署、卸载升级
- `references/TROUBLESHOOT.md` —— 采集失败、OOM、慢、乱码、时长缺失
- `references/OUTPUT.md` —— 产物结构、配置项、术语表格式、代码结构

## 合规

- 下载与转写仅供**个人学习研究**。
- 原作者文案属其核心资产，**改写后二次发布构成侵权**，平台亦判搬运。建议定位为
  「拆结构」的学习素材，不要做内容搬运。
- Cookie 是你本人的登录凭证，只存本机，本项目不写入任何日志、不上传。

## 致谢与许可

采集层使用 [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)
（运行时不复制进本仓库，首次使用时 clone 到本地并锁定 commit）。
转写依赖 [FunASR](https://github.com/modelscope/FunASR) 与
[ModelScope](https://modelscope.cn) 的开源模型。

本项目代码以 MIT 许可发布，见 `LICENSE`。

---

## English TL;DR

**douyin-transcript** turns Douyin (Chinese TikTok) videos into a local, searchable
text library — download → strip audio → offline ASR → per-video `.txt` + a single
markdown corpus + a CSV index.

- 100% local inference (FunASR / Paraformer + VAD + punctuation). No cloud API, no data leaves your machine.
- ~66× realtime on an RTX 3090 (16-min clip in 15 s); ~7× on CPU.
- Single-video mode by default — it will not silently pull a creator's whole account.
- Ships with an agent-facing `SKILL.md`, so an AI assistant can install and run it for you.
- Requires Python 3.9+, ffmpeg, git. Douyin login cookie obtained via your own browser.

```bash
git clone https://github.com/VanHelslInG/douyin-transcript ~/.dyasr/repo
cd ~/.dyasr/repo && python install.py --device cuda   # or --device cpu
dyasr doctor
dyasr fetch --video "https://www.douyin.com/video/<id>" --out ./videos
dyasr list  --src ./videos/Download
dyasr transcribe --src ./videos/Download --out ./文稿 --select all
```

Intended for personal study and content-structure analysis. The original creator
owns the script — republishing rewritten versions is copyright infringement.
