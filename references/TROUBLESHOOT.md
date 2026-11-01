# 排障

先跑 `dyasr doctor`（Agent 用 `dyasr doctor --json`），九成问题它能直接指出来。

## 采集阶段

### 采集器卡住不动 / 反复问要 Cookie

上游 DouK-Downloader 是交互式 TUI。`dyasr` 已用三处硬编码绕开交互：

1. `run_command = "5 1 1"` —— 主菜单第 5 项才是「终端交互模式」，写 `1 1 1` 会进
   「手动输入 Cookie」分支并死等输入。
2. `config_data.Disclaimer = 1` 写进 `Volume/DouK-Downloader.db` —— 跳过首启免责声明。
3. 子进程 `stdin=DEVNULL` —— 任何漏网的提示都会立即 EOF 而不是挂住。

如果仍然卡住，多半是上游改了菜单结构。排查：

```bash
cd ~/.dyasr/vendor/TikTokDownloader
uv run main.py          # 手动进去看真实菜单
```

### `✘ 采集完成但目录内没有视频文件`

按概率排序：

1. **Cookie 失效**（最常见）。重导 Cookie，`dyasr doctor` 应能看到新的到期日。
2. **链接不对**：必须是 `https://www.douyin.com/user/<sec_uid>`，不是分享短链、
   不是单条作品链接。短链要展开：
   ```bash
   curl -sIL -A "Mozilla/5.0 ... Chrome/120" "https://v.douyin.com/xxxxx/" | grep -i location
   ```
3. **账号作品非公开**（私密/仅好友可见）。
4. 平台风控：换网络、隔几十分钟再试。

### Cookie 到期时间怎么看

`cookie.txt` 里的 `sid_guard` 形如 `xxxx|<uid>|<ttl>|<到期日 GMT>`，**值是 URL 编码的**
（`%7C` 就是 `|`），所以必须解码后才能读。`dyasr cookie status` 已经处理，会算出「还剩 N 天」，
≤7 天时提示提前更换。

### 导入后提示「缺关键键 sessionid / sid_tt」

说明导出的是 **JS 可见的 Cookie**（`document.cookie`），而 `sessionid`、`sid_tt` 标了
HttpOnly，JS 永远读不到。换浏览器上下文的接口重导：

```python
# Playwright
context.storage_state(path="dy.json")     # 推荐，含 HttpOnly
# 或
await context.cookies()                    # 自己 json.dump
```

```js
// Puppeteer
const cookies = await page.cookies();      // 含 HttpOnly
```

浏览器插件导出 `cookies.txt`（Netscape 格式）也可以，dyasr 会自动识别。

### 导入成功但采集仍说没登录

- 确认导出时**已经登录**（页面上能看到自己的头像），Cookie 里应有 `sessionid`、`sid_tt`、
  `ttwid`、`odin_tt`。
- 确认域名属于 `douyin.com`（dyasr 会过滤掉其它域的 Cookie）。
- 少数情况下需要重新打开一次 douyin.com 让服务端下发新的 `ttwid`，再导出。


## 转写阶段

### `✘ 你要求 GPU，但 torch 看不到可用显卡`

```bash
<DYASR_PY> -c "import torch;print(torch.__version__, torch.cuda.is_available())"
```

- 打印 `+cpu` → 装的是 CPU 版 torch，用 `python install.py --device cuda --recreate` 重装。
- 打印 `+cu126 False` → torch 对但驱动不认，升级显卡驱动。
- `nvidia-smi` 本身报错 → 驱动问题，与 Python 无关。

### 显存不足（OOM）

长音频会一次性送进模型。调小单批长度：

```bash
dyasr config --batch-size-s 120
dyasr transcribe --src ... --out ...
```

3090 级别 24 GB 用默认 300 完全够；6–8 GB 的卡建议 60–120。

### 转写速度异常慢

- 检查 `dyasr doctor` 的「算力」一行实际走的是什么。配置 `auto` 在没有 CUDA 时静默走 CPU。
- CPU 基线约 7 倍实时（10 分钟视频约 1.5 分钟）；GPU 约 60 倍实时（约 10 秒）。
- 模型加载是一次性的 20–40 秒，不算在单条耗时里；每条都重新加载说明你在用上游技能脚本，
  不是 `dyasr transcribe`（后者把模型提到循环外，这是本项目存在的核心理由）。

### `内容过短（0 字），已丢弃`

纯音乐、纯静音、或音频轨损坏。用 ffmpeg 确认：

```bash
ffprobe -v error -show_streams "video.mp4" | grep -E "codec_type|channels|sample_rate"
```

### 进度页打不开

`进度.html` 用 `file://` 直接打开即可，不需要起服务。页面 5 秒自刷新，
关掉页面不影响后台转写。

## 产物阶段

### 视频被删了，想重新转写某一条

转写成功后源视频默认删除（`--keep-videos` 可保留）。要重转某条：
重新 `fetch` 一次（已下载的会跳过、已删的会重下），然后
`transcribe --select <编号>`。稿件本身也在 `raw/` 里，只是纯文本，能看不能改音频。

### 编号不是我以为的顺序（置顶那条不在 01）

下载器不返回「置顶」标记，所以仅凭文件名还原不了 App 顺序。两个办法：

1. 把作品页**可见顺序**的标题逐行写进 `<媒体目录>/页面顺序.txt`，`list` 和 `transcribe`
   会以它为准（01 = 第一行 = 置顶）。
2. 用 `--select` 直接指定编号或标题关键字，绕开顺序问题。

### 序号和真实发布时间对不上

老版本按文件 mtime 排序，而 mtime 是下载时间。当前版本已改为解析文件名里的
`YYYY-MM-DD HH.MM.SS` 前缀。若你的文件名不带日期，会排到末尾并在 rebuild 时告警——
此时只能靠重命名或人工排序。

### 时长列全是 0

说明既没有 `.dyasr_meta.json`，也没有可回填的旧 `索引.csv`。时长只能从源视频
`ffprobe` 得到，视频删了就只能显示 0 —— 这也是为什么 `transcribe` 会把时长旁挂到
`.dyasr_meta.json`，**不要删这个文件**。

### 术语替换不生效

1. `dyasr terms --show` 确认规则写对：必须是 `错词<TAB>正字`，用 Tab 不是空格。
2. 替换只作用于**产物**（合集/索引），`raw/*.txt` 保持 ASR 原文，这是刻意设计。
3. 改完要跑 `dyasr rebuild --out ./文稿`。

### rebuild 报「找不到稿件目录」

`--out` 要指向含 `raw/` 的那层，即 `transcribe` 时的 `--out`，不是 `raw` 本身。

## 其他

### 公司网络装不上 torch

`download.pytorch.org` 可能被墙/被代理拦。换镜像：

```bash
~/.dyasr/venv/bin/pip install torch torchaudio \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

注意镜像不一定提供 cu126 构建，装完务必用上面的 `torch.cuda.is_available()` 验证。

### Windows 上中文路径报错

已在 `subprocess` 调用中全程使用 list 参数（不经过 shell 拼接），中文与空格路径均安全。
若第三方组件仍报错，把工作目录换成纯英文路径重试，用于定位问题。
