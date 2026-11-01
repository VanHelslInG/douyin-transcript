"""命令行入口。所有子命令都是幂等的，重复执行只会补齐缺失部分。

退出码约定（供 Agent 判断，不必解析文案）：
  0 成功 / 1 有失败项或环境阻断 / 2 缺输入（例如缺 Cookie、缺链接）
"""
import argparse
import json
import os
import subprocess
import sys

from . import __version__, doctor as doctor_mod, fetch as fetch_mod
from . import cookie as cookie_mod, clean as clean_mod, core, listing as listing_mod
from . import paths, rebuild as rebuild_mod, terms as terms_mod, transcribe as transcribe_mod


def _cookie_from_args(args):
    if getattr(args, "cookie", ""):
        return args.cookie.strip()
    env = os.environ.get("DYASR_COOKIE", "").strip()
    if env:
        return env
    path = getattr(args, "cookie_file", "") or str(paths.cookie_path())
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            c = f.read().strip()
        if c:
            return c
    return ""


def cmd_doctor(args):
    return doctor_mod.doctor(as_json=args.json, fmodel=args.fmodel)


def cmd_fetch(args):
    cookie = _cookie_from_args(args)
    if not cookie:
        print("✘ 缺少抖音登录 Cookie。让 Agent 用浏览器打开 douyin.com 登录后导出，然后：\n"
              "    dyasr cookie import --file <导出的 cookie 文件>\n"
              "  （也支持手工：F12 → Network → 复制请求头整段 Cookie 存成 "
              f"{paths.cookie_path()}）", file=sys.stderr)
        return 2

    target = args.video or args.url
    if not target:
        print("✘ 需要 --url（博主主页）或 --video（单条作品链接）之一", file=sys.stderr)
        return 2

    try:
        media_dir, n = fetch_mod.fetch(target, args.out, cookie, args.max_pages)
    except fetch_mod.FetchError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1

    # 默认把视频转成音轨：转写只要声音，而视频封装通常是音轨的 8–10 倍
    if not args.keep_video:
        done, before, after = fetch_mod.to_audio(media_dir)
        if done:
            saved = max(0, before - after)
            print(f"✓ 已转为音频 {done} 个：{core.human_size(before)} → "
                  f"{core.human_size(after)}（省 {core.human_size(saved)}）")
            print("  （保留原始视频：加 --keep-video）")

    print(f"\n下一步：dyasr list --src \"{media_dir}\"")
    return 0


def cmd_list(args):
    return listing_mod.show(args.src, out_dir=args.out, as_json=args.json)


def cmd_clean(args):
    keep = set()
    if args.out:
        raw_dir = os.path.join(args.out, "raw")
        for p in core.list_raw(raw_dir):
            keep.add(os.path.splitext(os.path.basename(p))[0])
    n, freed, left = clean_mod.clean_videos(args.src, keep_stems=keep, dry_run=args.dry_run)
    verb = "将删除" if args.dry_run else "已删除"
    print(f"{verb} {n} 个视频，{'可释放' if args.dry_run else '释放'} "
          f"{core.human_size(freed)}" + (f"；保留 {left} 个（已有稿件）" if left else ""))
    if not args.dry_run and n:
        clean_mod.prune_empty_dirs(args.src)
    return 0


def cmd_cookie(args):
    if args.action == "status":
        info = cookie_mod.status()
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"Cookie：{cookie_mod.describe(info)}")
            print(f"  文件：{info['path']}")
            if info["present"]:
                print(f"  键数：{len(info['keys'])}　未解析到："
                      f"{'、'.join(info['missing']) or '无'}")
        return 0 if info["present"] and not info["missing"] else 1

    if args.action == "clear":
        print("✓ 已删除 Cookie" if cookie_mod.clear() else "（本来就没有 Cookie）")
        return 0

    # import
    if args.file:
        target, header, pairs = cookie_mod.import_file(args.file, args.path or None)
    elif not sys.stdin.isatty():
        target, header, pairs = cookie_mod.import_text(sys.stdin.read(), args.path or None)
    else:
        print("✘ 请用 --file 指定导出的 cookie 文件，或把内容通过管道传入。", file=sys.stderr)
        return 2
    info = cookie_mod.status()
    print(f"✓ 已写入 {target}（{len(pairs)} 个键，{len(header)} 字节）")
    print(f"  状态：{cookie_mod.describe(info)}")
    if info["missing"]:
        print(f"  ⚠ 缺关键键：{'、'.join(info['missing'])}。"
              "多半是只导出了 document.cookie（HttpOnly 读不到），"
              "请改用浏览器上下文的 cookies() 接口导出。")
        return 1
    return 0


def cmd_transcribe(args):
    return transcribe_mod.transcribe(
        src=args.src, out_dir=args.out, device=args.device, fmodel=args.fmodel,
        hotword=args.hotword if args.hotword is not None else "",
        no_resume=args.no_resume, doc_name=args.doc_name,
        batch_size_s=args.batch_size_s, select=args.select,
        keep_videos=args.keep_videos)


def cmd_rebuild(args):
    return rebuild_mod.rebuild(args.out, order=args.order, doc_name=args.doc_name,
                               show_hits=not args.quiet)


def cmd_config(args):
    cfg = paths.load_config()
    changed = {}
    for key in ("device", "fmodel", "hotword", "batch_size_s", "min_chars"):
        val = getattr(args, key, None)
        if val is not None:
            changed[key] = val
    if changed:
        cfg = paths.set_config(**changed)
        print(f"✓ 已写入 {paths.CONFIG_FILE}")
    print("当前配置：")
    for k in sorted(cfg):
        print(f"  {k:<14} {cfg[k]}")
    return 0


def cmd_terms(args):
    p = terms_mod.seed_if_missing()
    if args.show or not args.edit:
        print(f"术语表：{p}\n" + "─" * 50)
        print(p.read_text(encoding="utf-8"))
        print("─" * 50)
        print("改完执行 dyasr rebuild --out <稿件目录> 立即生效，无需重新转写。")
        return 0
    opener = "notepad" if os.name == "nt" else (
        "open" if sys.platform == "darwin" else "xdg-open")
    try:
        subprocess.Popen([opener, str(p)])
    except Exception:
        print(f"请手动编辑：{p}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="dyasr",
        description="把抖音博主主页的全部作品转成可检索的本地口播文字稿（离线 ASR）。",
        epilog="典型流程：dyasr doctor → dyasr fetch → dyasr transcribe → dyasr rebuild",
    )
    ap.add_argument("-V", "--version", action="version", version=f"dyasr {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境自检（缺什么、要不要下载模型）")
    d.add_argument("--json", action="store_true", help="输出机器可读 JSON，供 Agent 解析")
    d.add_argument("--fmodel", default="", help="检查哪个模型档位的缓存")
    d.set_defaults(func=cmd_doctor)

    f = sub.add_parser("fetch", help="下载作品（单条链接或整个账号）")
    f.add_argument("--url", default="", help="博主主页 https://www.douyin.com/user/<sec_uid>（全量采集）")
    f.add_argument("--video", default="", help="单条作品链接（含 modal_id / /video/<id>），只下这一条")
    f.add_argument("--out", default="", help="保存目录（默认 ./videos）")
    f.add_argument("--cookie", default="", help="Cookie 字符串（默认读 ~/.dyasr/cookie.txt）")
    f.add_argument("--cookie-file", default="", help="Cookie 文件路径")
    f.add_argument("--max-pages", type=int, default=0, help="账号模式翻页上限，0=全部")
    f.add_argument("--keep-video", action="store_true",
                   help="保留原始视频（默认转成音轨并删视频，体积约 1/10）")
    f.set_defaults(func=cmd_fetch)

    l = sub.add_parser("list", help="列出作品，按编号选择要转写哪些")
    l.add_argument("--src", required=True, help="采集产物目录")
    l.add_argument("--out", default="", help="稿件目录（用于标记哪些已转写）")
    l.add_argument("--json", action="store_true", help="输出 JSON 供 Agent 使用")
    l.set_defaults(func=cmd_list)

    k = sub.add_parser("cookie", help="导入/查看/清除抖音 Cookie")
    k.add_argument("action", choices=["import", "status", "clear"], nargs="?", default="status")
    k.add_argument("--file", default="", help="浏览器导出的 cookie 文件（json/txt 均可）")
    k.add_argument("--path", default="", help="写入位置（默认 ~/.dyasr/cookie.txt）")
    k.add_argument("--json", action="store_true", help="status 输出 JSON")
    k.set_defaults(func=cmd_cookie)

    n = sub.add_parser("clean", help="删除下载的源视频释放空间")
    n.add_argument("--src", required=True, help="采集产物目录")
    n.add_argument("--out", default="", help="稿件目录；有稿件的视频会被保留")
    n.add_argument("--dry-run", action="store_true", help="只看会删什么，不动手")
    n.set_defaults(func=cmd_clean)

    t = sub.add_parser("transcribe", help="批量转写（常驻模型，GPU 优先）")
    t.add_argument("--src", required=True, help="视频目录（采集产物目录）")
    t.add_argument("--out", required=True, help="稿件输出目录")
    t.add_argument("--select", default="all",
                   help="要转写哪些：all / 1,3,5-8 / 标题关键字（默认 all）")
    t.add_argument("--device", default="", help="auto / cuda / cpu，留空用配置")
    t.add_argument("--fmodel", default="", help="paraformer（默认）/ sensevoice")
    t.add_argument("--hotword", default=None, help="热词，空格分隔")
    t.add_argument("--no-resume", action="store_true", help="忽略已有稿件，全部重跑")
    t.add_argument("--keep-videos", action="store_true",
                   help="转写后保留源视频（默认删除已成功转写的视频以释放空间）")
    t.add_argument("--doc-name", default="口播稿件合集.md")
    t.add_argument("--batch-size-s", type=int, default=None, help="单批音频秒数，显存紧张时调小")
    t.set_defaults(func=cmd_transcribe)

    r = sub.add_parser("rebuild", help="仅重建合集与索引（不跑模型，秒级）")
    r.add_argument("--out", required=True, help="稿件目录（含 raw/）")
    r.add_argument("--order", default="asc", choices=["asc", "desc"], help="时间正序/倒序")
    r.add_argument("--doc-name", default="口播稿件合集.md")
    r.add_argument("--quiet", action="store_true", help="不打印术语命中统计")
    r.set_defaults(func=cmd_rebuild)

    c = sub.add_parser("config", help="查看/修改配置")
    c.add_argument("--device", choices=["auto", "cuda", "cpu"])
    c.add_argument("--fmodel", choices=["paraformer", "sensevoice"])
    c.add_argument("--hotword")
    c.add_argument("--batch-size-s", type=int)
    c.add_argument("--min-chars", type=int)
    c.set_defaults(func=cmd_config)

    m = sub.add_parser("terms", help="查看/编辑术语纠错表")
    m.add_argument("--show", action="store_true", help="只打印，不打开编辑器")
    m.add_argument("--edit", action="store_true", help="用系统默认编辑器打开")
    m.set_defaults(func=cmd_terms)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd == "fetch" and not args.out:
        args.out = os.path.abspath("videos")
    paths.ensure_home()
    try:
        return args.func(args) or 0
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n已中断。已完成的稿件会保留，重跑同一命令即可续跑。", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"✘ {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
