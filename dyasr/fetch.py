"""采集层：封装 DouK-Downloader（JoeanAmier/TikTokDownloader）。

为什么需要这一层：上游是个交互式 TUI 程序，直接跑会卡在菜单等着人按键。
这里把三处交互全部绕开——
  1) 主菜单序号：5=终端交互模式（1~4 都是 Cookie 相关，写错会死循环等输入）
  2) 免责声明确认：存在 Volume/DouK-Downloader.db 的 config_data.Disclaimer
  3) 任何剩余提示：子进程 stdin 直接设为 DEVNULL

上游程序不会被复制进本仓库（许可证与体积都不合适），首次使用时 clone 到你本机。
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

from . import core, paths

# 主菜单：5 = 终端交互模式
#   1 = 批量下载账号作品(抖音) → 1 = 使用 accounts_urls 配置
#   2 = 批量下载链接作品(抖音) → 2 = 从文本文档读取链接（会让你输入文档路径）
RUN_COMMAND_ACCOUNT = "5 1 1"
RUN_COMMAND_DETAIL = "5 2 2"

_VIDEO_ID_RE = re.compile(r"(?:modal_id=|/video/|/note/)(\d{8,})")


def normalize_video_url(url):
    """把各种形态的作品链接归一成 /video/<id>，并取出 id。

    抖音分享出来的链接常见是 `.../jingxuan?modal_id=123` 或 `.../video/123`，
    上游的链接解析对形态比较挑，统一成 /video/<id> 最稳。
    """
    m = _VIDEO_ID_RE.search(url or "")
    if not m:
        return None, None
    vid = m.group(1)
    return f"https://www.douyin.com/video/{vid}", vid


def looks_like_video(url):
    return _VIDEO_ID_RE.search(url or "") is not None


class FetchError(RuntimeError):
    pass


def _run(cmd, cwd=None, check=True, stdin_data=None):
    """stdin_data 为 None 时断开 stdin（防交互卡死）；给了就喂进去回答必填提问。"""
    kwargs = {"cwd": cwd}
    if stdin_data is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        # input= 会自动接管 stdin，不能再显式指定 stdin=PIPE（会 ValueError）
        kwargs.update(input=stdin_data, text=True, encoding="utf-8", errors="ignore")
    p = subprocess.run(cmd, **kwargs)
    if check and p.returncode != 0:
        raise FetchError(f"命令失败（{p.returncode}）：{' '.join(map(str, cmd))}")
    return p.returncode


def ensure_vendor(cfg, quiet=False):
    """确保下载器源码存在且是锁定的版本。返回其目录。"""
    repo = paths.vendor_repo_dir()
    if (repo / "main.py").exists():
        return repo

    if not shutil.which("git"):
        raise FetchError("找不到 git。请先安装 git（https://git-scm.com/downloads）后重试。")

    paths.ensure_home()
    url = cfg["douk_repo"]
    if not quiet:
        print(f"→ 首次使用：正在下载采集器源码\n  {url}\n  → {repo}")
    code = _run(["git", "clone", "--quiet", url, str(repo)], check=False)
    if code != 0:
        raise FetchError(
            "下载采集器源码失败（git clone）。通常是网络到 GitHub 不通（代理/防火墙）。\n"
            "  三个办法任选：\n"
            f"    1) 配好代理后重试；\n"
            f"    2) 手动把 upstream 仓库放到：{repo}\n"
            "    3) 设环境变量 DYASR_VENDOR 指向已有的上游目录，再重跑")

    commit = (cfg.get("douk_commit") or "").strip()
    if commit:
        code = _run(["git", "-C", str(repo), "checkout", "--quiet", commit], check=False)
        if code != 0:
            # 锁定版本拿不到就退回默认分支，但要明确告诉用户口径已经漂移
            print(f"⚠ 无法切换到锁定版本 {commit[:8]}，将使用默认分支（行为可能与文档有差异）")
    return repo


def resolve_runner(cfg, repo):
    """返回启动下载器的命令前缀。

    优先级：已有可用 .venv（最快也最确定，不需要联网）→ uv（上游推荐，会同步依赖）
    → 现建 venv + pip 装依赖。
    """
    venv_py = repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv_py.exists():
        return [str(venv_py), "main.py"]

    if shutil.which("uv"):
        return ["uv", "run", "main.py"]

    print("→ 未检测到 uv 与现成环境，正在为采集器创建虚拟环境并安装依赖（首次较慢）…")
    _run([sys.executable, "-m", "venv", str(repo / ".venv")])
    req = repo / "requirements.txt"
    if not req.exists():
        raise FetchError("采集器缺少 requirements.txt，无法自动安装依赖")
    _run([str(venv_py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    _run([str(venv_py), "-m", "pip", "install", "-q", "-r", str(req)])
    return [str(venv_py), "main.py"]


def patch_settings(repo, url, cookie, out_dir, max_pages, mode="account"):
    volume = repo / "Volume"
    volume.mkdir(parents=True, exist_ok=True)     # 全新 clone 里没有这个目录
    settings = volume / "settings.json"

    # 全新 clone 里也没有 settings.json —— 上游是在首次运行时才生成它，
    # 而我们必须在它启动之前把 cookie / 输出目录写进去，否则会退化成交互式询问。
    # 这里只写我们关心的键，其余键由上游的 __check() 在读取时自动补齐并回写。
    if settings.exists():
        # 上游文件带 BOM，必须用 utf-8-sig 读，否则 json 解析失败
        with open(settings, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    if mode == "detail":
        cfg["run_command"] = RUN_COMMAND_DETAIL
        cfg["accounts_urls"] = []
    else:
        cfg["run_command"] = RUN_COMMAND_ACCOUNT
        cfg["accounts_urls"] = [{
            "mark": "",
            "url": url.strip(),
            "tab": "post",        # 只要「作品」，不要喜欢/收藏
            "earliest": "",
            "latest": "",
            "enable": True,
        }]
    cfg["cookie"] = cookie
    cfg["root"] = os.path.abspath(out_dir)
    cfg["folder_name"] = "Download"
    cfg["folder_mode"] = False      # 上游仍会强制建 UID_昵称_发布作品 子目录
    cfg["name_format"] = "create_time desc"   # 文件名带发布时间，供后续按时间排序
    cfg["desc_length"] = 80                  # 标题最多保留 80 字，太短会被截成「...」
    cfg["download"] = True
    cfg["music"] = False
    cfg["dynamic_cover"] = False
    cfg["static_cover"] = False
    cfg["max_pages"] = int(max_pages)
    cfg["douyin_platform"] = True

    with open(settings, "w", encoding="utf-8-sig") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


def accept_disclaimer(repo):
    """把免责声明与语言写进 SQLite，跳过首次启动的交互问答。

    全新 clone 里连数据库都没有，而这两个交互（语言菜单 + 免责声明 YES/NO）
    在 `stdin=DEVNULL` 下会直接拿到 EOF —— 所以必须先把库和默认行建好。
    表结构与上游 src/manager/database.py 保持一致，否则它的
    CREATE TABLE IF NOT EXISTS 不会纠正已经建错的表。
    """
    volume = repo / "Volume"
    volume.mkdir(parents=True, exist_ok=True)
    db = volume / "DouK-Downloader.db"

    con = sqlite3.connect(db)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS config_data (
            NAME TEXT PRIMARY KEY,
            VALUE INTEGER NOT NULL CHECK(VALUE IN (0, 1)));""")
        con.execute("CREATE TABLE IF NOT EXISTS download_data (ID TEXT PRIMARY KEY);")
        con.execute("""CREATE TABLE IF NOT EXISTS mapping_data (
            ID TEXT PRIMARY KEY,
            NAME TEXT NOT NULL,
            MARK TEXT NOT NULL);""")
        con.execute("""CREATE TABLE IF NOT EXISTS option_data (
            NAME TEXT PRIMARY KEY,
            VALUE TEXT NOT NULL);""")
        con.execute("INSERT OR IGNORE INTO config_data (NAME, VALUE) VALUES "
                    "('Record', 1), ('Logger', 0), ('Disclaimer', 1)")
        con.execute("REPLACE INTO config_data (NAME, VALUE) VALUES ('Disclaimer', 1)")
        con.execute("INSERT OR IGNORE INTO option_data (NAME, VALUE) "
                    "VALUES ('Language', 'zh_CN')")
        con.execute("REPLACE INTO option_data (NAME, VALUE) VALUES ('Language', 'zh_CN')")
        con.commit()
    except sqlite3.Error:
        pass
    finally:
        con.close()


def discover_media_dir(out_dir):
    """下载器会多建一层子目录，这里递归找真正装了视频的那层。"""
    best, best_n = out_dir, 0
    for root, _dirs, files in os.walk(out_dir):
        n = sum(1 for f in files if os.path.splitext(f)[1].lower() in
                {".mp4", ".mov", ".m4a", ".webm"})
        if n > best_n:
            best, best_n = root, n
    return best, best_n


def fetch(target, out_dir, cookie, max_pages=0, cfg=None):
    """采集。target 是账号主页或单个作品链接，自动判定走哪条上游流程。

    单作品走「批量下载链接作品 → 从文本文档读取」：把链接写进一个临时 txt，
    再把该路径喂给上游的 `请输入文本文档路径：` 提问（它没有配置文件入口）。
    """
    cfg = cfg or paths.load_config()
    is_video = looks_like_video(target)
    mode = "detail" if is_video else "account"

    repo = ensure_vendor(cfg)
    os.makedirs(out_dir, exist_ok=True)
    patch_settings(repo, target, cookie, out_dir, max_pages, mode=mode)
    accept_disclaimer(repo)

    stdin_data = None
    if is_video:
        video_url, vid = normalize_video_url(target)
        if not video_url:
            raise FetchError(f"无法从链接里解析出作品 ID：{target}")
        paths.ensure_home()
        link_file = paths.CACHE_DIR / "links.txt"
        link_file.write_text(video_url + "\n", encoding="utf-8")
        stdin_data = str(link_file) + "\n"
        print(f"→ 目标作品：{video_url}")
    else:
        print(f"→ 博主主页：{target}")
    print(f"→ 保存目录：{os.path.abspath(out_dir)}")
    print("→ 开始下载（Ctrl+C 可中止）…\n")

    runner = resolve_runner(cfg, repo)
    code = _run(runner, cwd=str(repo), check=False, stdin_data=stdin_data)
    if code != 0:
        raise FetchError(
            f"采集器退出码 {code}。常见原因：Cookie 失效 / 平台风控 / 链接不是作品页。\n"
            "  自查：重导 Cookie（dyasr cookie status 看是否过期）；"
            "或确认链接形如 https://www.douyin.com/video/<id>")

    media_dir, n = discover_media_dir(out_dir)
    if n == 0:
        raise FetchError("采集完成但目录内没有视频文件，请检查 Cookie 是否有效、该作品是否可见")
    print(f"\n✓ 采集完成，共 {n} 个文件：{os.path.abspath(media_dir)}")
    return media_dir, n


def to_audio(media_dir, delete_source=True):
    """把视频里的音轨抽成 m4a，再删视频。

    转写只需要音轨，而抖音作品是视频封装（1080p 十几分钟 ≈ 100 MB），
    音轨 copy 出来通常只有 1/10。注意这**只省磁盘，不省下载流量** ——
    上游只提供 mp4，没法只下音频流。
    """
    media = core.list_media(media_dir)
    targets = [p for p in media if os.path.splitext(p)[1].lower()
               in {".mp4", ".mov", ".mkv", ".webm", ".avi"}]
    if not targets:
        return 0, 0, 0
    before = sum(os.path.getsize(p) for p in targets if os.path.exists(p))
    done = 0
    for src in targets:
        dst = os.path.splitext(src)[0] + ".m4a"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", src, "-vn", "-c:a", "copy", dst,
                 "-loglevel", "error"], check=True, capture_output=True)
            if delete_source and os.path.getsize(dst) > 0:
                os.remove(src)
            done += 1
        except Exception:
            continue
    after = sum(os.path.getsize(os.path.splitext(p)[0] + ".m4a")
                for p in targets if os.path.exists(os.path.splitext(p)[0] + ".m4a"))
    return done, before, after
