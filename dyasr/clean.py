"""清理下载的源视频。

转写一旦完成，源视频就只剩占用空间的作用（raw/*.txt 里已有全文，时长也旁挂
在 .dyasr_meta.json 里）。一小时口播就是几个 GB，所以默认清理。

安全边界：
  · 只删 `--src` 目录内的音视频文件，不递归删目录、不碰任何 .txt/.json
  · 只删「已经成功转写出稿件」的那些，失败项原样保留，方便重试
  · 永不删除稿件目录里的任何东西
"""
import os

from . import core


def size_of(paths):
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


def plan(all_stems, done_stems):
    """决定删哪些：只删「已成功转写」的，其余（未选中、失败）一律保留。

    单独抽出来是为了能测 —— 这里逻辑写错的代价是用户丢掉还没转写的视频。
    """
    keep = set(all_stems) - set(done_stems)
    delete = set(done_stems) & set(all_stems)
    return keep, delete


def clean_videos(src_dir, keep_stems=(), dry_run=False):
    """删除 src_dir 下的音视频，保留 keep_stems 指定的那些。

    返回 (删除个数, 释放字节数, 保留个数)。
    """
    keep_stems = set(keep_stems or ())
    media = core.list_media(src_dir)
    targets = [p for p in media
               if os.path.splitext(os.path.basename(p))[0] not in keep_stems]
    freed = size_of(targets)
    if not dry_run:
        for p in targets:
            try:
                os.remove(p)
            except OSError:
                pass
    return len(targets), freed, len(media) - len(targets)


def prune_empty_dirs(src_dir):
    """顺带清掉清空后的空子目录（下载器会建 UIDxxx_昵称_发布作品 那层）。"""
    for root, dirs, files in os.walk(src_dir, topdown=False):
        if os.path.abspath(root) == os.path.abspath(src_dir):
            continue
        if not os.listdir(root):
            try:
                os.rmdir(root)
            except OSError:
                pass
