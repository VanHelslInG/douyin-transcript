---
name: dyasr
description: 把某个抖音博主的全部作品批量下载并离线转写成口播文字稿，输出逐条 txt + 合集 md + 索引 csv，支持按编号挑选要转写的作品、自动获取登录 Cookie、转写后自动清理视频。当用户提出「下载某抖音博主的视频 / 把抖音作品转成文字 / 提取口播文案 / 批量转写抖音 / 做博主内容拆解素材 / 对标账号内容分析」时使用。全程本地 ASR，视频与文字不出本机。
license: MIT
---

# dyasr — 抖音口播稿流水线（Agent 执行手册）

你要做的是**替用户把这套本地流水线装起来并跑完**，不是解释它。
用户只需要提供**一个博主主页链接**；Cookie 由你用浏览器自己拿，不用用户去 F12 里翻。

```
取 Cookie(你操浏览器) → 下载(默认只下用户给的那条) → 列清单(按编号挑) → 转写 → 自动删视频
```

## 铁律

1. **先问算力，再装。** 绝不自作主张选 CPU 或 GPU，这一步决定装哪个 torch，装错要整包重来。
2. **Cookie 由你用浏览器导出，不要让用户手工找。** 唯一需要用户动手的是**扫码/输验证码登录**。
3. **导出必须用浏览器上下文的 cookie 接口**（`context.cookies()` / `page.cookies()` / CDP），
   **不能用 `document.cookie`** —— `sessionid`、`sid_tt` 是 HttpOnly，JS 读不到，导出来是废的。
4. **模型下载前必须告知体积**（约 1.9 GB，一次性；大头是标点模型 1.1 GB）并取得同意。
5. **编号先给用户看，再动手转。** 转写前必须让用户确认「全选还是哪几条」。
6. 不要用 `--no-resume`，除非用户明确要求全部重跑（它是覆盖式的）。
7. 退出码即契约：`0` 成功 / `1` 有失败项或环境阻断 / `2` 缺输入。别去正则解析文案。

## 第 0 步：定位仓库 + 判断是否已安装

仓库固定在 `~/.dyasr/repo`，这样启动器、配置、稿件路径才稳定：

```bash
git clone https://github.com/VanHelslInG/douyin-transcript "$HOME/.dyasr/repo"   # 还没有才执行

# Windows (Git Bash)
"$HOME/.dyasr/venv/Scripts/python.exe" -m dyasr doctor --json
# macOS / Linux
"$HOME/.dyasr/venv/bin/python" -m dyasr doctor --json
```

能输出 JSON → 已安装，跳到第 2 步；报错 → 走第 1 步。

> 记住解释器路径，后面都用 `<DYASR_PY> -m dyasr <子命令>`。

## 第 1 步：安装（必须先问 CPU/GPU）

用类似这样的话问用户：

> 转写有两种算力版本：
> - **GPU**：需要 NVIDIA 显卡，约 60 倍实时（10 分钟视频约 10 秒），推荐。
> - **CPU**：任何电脑都行，约 7 倍实时（10 分钟视频约 1.5 分钟）。
> 你有 NVIDIA 显卡吗？

```bash
cd "$HOME/.dyasr/repo"
python install.py --device cuda     # 或 --device cpu
```

用**系统 Python** 跑，不要用 `<DYASR_PY>`（安装器自己建 venv）。
装完 `doctor` 的「算力」一行应显示实际设备，且 `cuda.is_available()` 已验证过。

## 第 2 步：用浏览器拿 Cookie（用户无需手工操作）

**目标：拿到包含 HttpOnly 在内的完整抖音 Cookie，写进 `~/.dyasr/cookie.txt`。**
优先复用你已有的浏览器会话，没有登录态再请用户扫码。

```text
1) 打开 https://www.douyin.com/
2) 检查是否已登录（页面上能看到头像/自己的昵称即已登录）
3) 未登录 → 请用户扫码登录（这是唯一需要用户动手的一步），登录后再继续
4) 用浏览器上下文的 cookie 接口导出到临时文件：
     Playwright : context.storage_state(path="/tmp/dy.json")
     Puppeteer  : JSON.stringify(await page.cookies())  → /tmp/dy.json
     其它工具    : 导出该站点的 cookie 列表为 JSON 数组
5) 导入：
     <DYASR_PY> -m dyasr cookie import --file /tmp/dy.json
```

成功的标志：命令输出 `✓ 已写入 …（N 个键）` 且状态为「可用，YYYY-MM-DD 到期」。

### 没有现成浏览器工具时（playwright-cli 配方，已实测）

**关键坑：浏览器进程会随你的 shell 命令一起结束**，所以「打开」和「等登录 + 导出」
必须写在**同一条命令**里，否则窗口会闪退、cookie 拿不到。

```bash
npm install -g @playwright/cli@latest          # 首次
playwright-cli -s=dy open "https://www.douyin.com/" \
  --browser=chrome --persistent --headed --idle-timeout 900000

# 在同一条命令里轮询登录态（Playwright 的 cookie API 能读到 HttpOnly）
for i in $(seq 1 90); do
  OUT=$(playwright-cli -s=dy cookie-get sessionid 2>&1)
  echo "$OUT" | grep -q "not found" || { echo 已登录; break; }
  sleep 10
done
playwright-cli -s=dy state-save ./auth.json     # 这就是 storage_state
<DYASR_PY> -m dyasr cookie import --file ./auth.json
```

- `--headed` 必须有，否则用户看不到窗口、没法扫码。
- `--persistent` 让登录态留在 profile 里，下次不用再扫（默认 profile 在
  `~/AppData/Local/ms-playwright/daemon/*/ud-*`）。
- 抖音新 profile 常先给一个「验证码中间页」，让用户在窗口里点掉即可。
- 导出后**删掉 auth.json**（里面有完整登录态）。


- 若提示「缺关键键 sessionid / sid_tt」→ 说明你用了 `document.cookie`，换 cookie API 重导。
- Cookie 等同账号密码：**不要打印全文、不要写进日志、不要提交到任何仓库**。导入完删掉临时文件。
- 半年左右会过期；`doctor` / `cookie status` 会提示剩余天数。

## 第 3 步：下载（默认只下用户给的那一条）

**先判断链接类型**：

| 用户给的链接 | 走法 | 要问的问题 |
|---|---|---|
| 单条作品（含 `modal_id=` 或 `/video/<id>`） | `--video` | 不用问，就下这一条 |
| 博主主页（`/user/<sec_uid>`） | `--url` | **必须先问**：「只转最近几条，还是整个账号全量？」 |

```bash
# 单条作品：用户给了 /jingxuan?modal_id=xxx 或 /video/xxx 都用这条
<DYASR_PY> -m dyasr fetch --video "https://www.douyin.com/jingxuan?modal_id=xxx" --out ./videos

# 整个账号（必须先征求同意；几十条 = 几个 GB）
<DYASR_PY> -m dyasr fetch --url "https://www.douyin.com/user/<sec_uid>" --out ./videos
```

- `--url` 模式下的 `--max-pages` **上游并不严格生效**，别用它当"只下前 N 条"用；
  要限量就按主页链接只取单条，或下载后用 `--select` 控制转写范围。
- **默认把视频转成音轨并删除视频**：转写只需要声音，15 分钟作品从约 100 MB 降到约 10 MB。
  注意这**只省磁盘不省流量**（抖音只提供 mp4，没法只下音频流）。要留原片加 `--keep-video`。
- 首次运行会自动 clone 采集器源码（约 1 分钟）。
- 结束会打印**真实媒体目录**，下一步用它。

## 第 4 步：列清单，让用户按编号挑

```bash
<DYASR_PY> -m dyasr list --src "<媒体目录>" --out ./文稿
```

编号口径：**01 = 用户打开抖音看到的第一条**。为此需要一份页面顺序：

- 你用浏览器访问该博主作品页，把**可见顺序**的标题逐行写入 `<媒体目录>/页面顺序.txt`
  （置顶作品就是第一行）。抓不到就算了——脚本会退回「发布时间倒序」，并在输出里明确告知。
- 有这份文件时，`list` 会自动按它排序，Agent 不用自己排序。

**然后必须把表贴给用户确认**，例如：

> 共 60 条作品，01–60 如下（01 是置顶那条）……
> 你要转写**全部**，还是只转**某几条**（例如 1,3,5-8）？

`--json` 输出结构化清单，便于你自己解析后再向用户转述。

## 第 5 步：转写（按选择）

```bash
<DYASR_PY> -m dyasr transcribe --src "<媒体目录>" --out ./文稿 --select "1,3,5-8"
<DYASR_PY> -m dyasr transcribe --src "<媒体目录>" --out ./文稿 --select all
```

- `--select` 支持 `all` / `1,3,5-8` / 标题关键字。
- 只下了一条时直接 `--select all`，不必让用户挑。
- 首次会下载 ASR 模型（约 1.9 GB，其中标点模型 1.1 GB）——**先问用户是否现在下载**。
- 断点续跑默认开：中断后重跑同一条命令即可。
- 进度页：`./文稿/进度.html`（浏览器打开，5 秒自刷新）。
- **转写成功后会自动删除对应的源视频**并打印释放了多少空间（用户要求的行为）。
  想保留就加 `--keep-videos`。转写失败的条目视频会保留，方便重试。

## 第 6 步：纠错与重排（建议做一次）

```bash
<DYASR_PY> -m dyasr terms --show     # 按博主补充「错词→正字」
<DYASR_PY> -m dyasr rebuild --out ./文稿
```

`rebuild` 不碰模型，几秒完成：套术语表、按发布时间重排、重生成合集与索引。
改错字**不需要**重新转写。

## 交付给用户什么

| 文件 | 说明 |
|---|---|
| `文稿/口播稿件合集.md` | 全文汇总，可直接阅读 |
| `文稿/索引.csv` | 序号/日期/时长/字数/状态，适合丢进表格分析 |
| `文稿/raw/*.txt` | 逐条纯文本，供下游做结构拆解 |
| `文稿/进度.html` | 转写进度可视化 |

## 合规边界（要主动告知用户）

- 下载与转写仅限**个人学习研究**；原作者文案改写后二次发布构成侵权。
- Cookie 是用户本人登录凭证，只存本机。

## 参考文档

- `references/SETUP.md` —— 手工安装、换算力、离线部署
- `references/TROUBLESHOOT.md` —— Cookie 导出失败、采集失败、OOM、时长缺失
- `references/OUTPUT.md` —— 产物结构、配置项、术语表格式、代码结构
