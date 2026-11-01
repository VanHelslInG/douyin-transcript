"""转写层：常驻模型批量转写。

关键设计（性能差 10 倍就是差在这里）：
funasr 官方示例/多数封装都是「一个文件一个进程」，每处理一条都要重新加载
paraformer + VAD + 标点三个模型（约 40 秒）。本模块把模型加载提到循环外，
一次加载、全部文件复用，实测从 6.6x 实时提升到 60x+ 实时、GPU 占用 17% → 99%。

音频统一先经 ffmpeg 抽成 16k 单声道 wav 再送模型，避免长视频里容器格式、
可变帧率、多音轨带来的偶发解码失败。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from . import clean as clean_mod
from . import core, paths, terms as terms_mod

MODEL_ALIAS = {
    "paraformer": "paraformer-zh",
    "sensevoice": "iic/SenseVoiceSmall",
}

# funasr 用到的模型 ID，用于预检缓存是否齐全（对应 modelscope 缓存目录名）
REQUIRED_MODELS = {
    "paraformer": [
        ("iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
         "iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", 848),
        ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
         "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch", 4),
        ("iic/punc_ct-transformer_cn-en-common-vocab471067-large",
         "iic--punc_ct-transformer_cn-en-common-vocab471067-large", 1132),
    ],
    "sensevoice": [
        ("iic/SenseVoiceSmall", "iic--SenseVoiceSmall", 897),
        ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
         "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch", 4),
        ("iic/punc_ct-transformer_cn-en-common-vocab471067-large",
         "iic--punc_ct-transformer_cn-en-common-vocab471067-large", 1132),
    ],
}


def modelscope_cache_dir():
    env = os.environ.get("MODELSCOPE_CACHE")
    if env:
        return os.path.join(env, "models")
    return os.path.join(os.path.expanduser("~"), ".cache", "modelscope", "models")


def model_status(fmodel="paraformer"):
    """返回 [(模型ID, 是否已缓存, 约多少 MB)]。只依据本地缓存目录判断，不发网络请求。"""
    cache = modelscope_cache_dir()
    out = []
    for model_id, dirname, mb in REQUIRED_MODELS.get(fmodel, REQUIRED_MODELS["paraformer"]):
        d = os.path.join(cache, dirname)
        snap = os.path.join(d, "snapshots")
        # modelscope 缓存结构：<owner>--<name>/snapshots/<rev>/...；空 snapshots 视为未就绪
        ok = os.path.isdir(d) and (any(os.scandir(snap)) if os.path.isdir(snap) else True)
        out.append((model_id, bool(ok), mb))
    return out


def missing_models(fmodel="paraformer"):
    return [(m, mb) for m, ok, mb in model_status(fmodel) if not ok]


def resolve_device(requested="auto"):
    """返回实际使用的 device 字符串；GPU 请求不可用时报错而不是静默降级。"""
    try:
        import torch
    except ImportError:
        if requested in ("cuda", "gpu"):
            raise SystemExit("✗ 你要求 GPU，但当前环境没装 torch。请重跑安装：python install.py --device cuda")
        return "cpu"

    if requested in ("cuda", "gpu"):
        if not torch.cuda.is_available():
            raise SystemExit(
                "✗ 你要求 GPU，但 torch 看不到可用显卡。\n"
                "  可能原因：装的是 CPU 版 torch / 驱动过旧 / 显卡被其他进程占满。\n"
                "  自查：python -c \"import torch;print(torch.__version__, torch.cuda.is_available())\"\n"
                "  改回 CPU：dyasr config --device cpu")
        return "cuda"
    if requested == "cpu":
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _extract_wav(video, work_dir):
    wav = os.path.join(work_dir, "cur.wav")
    if os.path.exists(wav):
        os.remove(wav)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-f", "wav", wav, "-loglevel", "error"],
        check=True, capture_output=True,
    )
    return wav


def transcribe(src, out_dir, device="", fmodel="", hotword="", no_resume=False,
               doc_name="口播稿件合集.md", batch_size_s=None, select="",
               keep_videos=False):
    cfg = paths.load_config()
    fmodel = fmodel or cfg.get("fmodel", "paraformer")
    hotword = hotword if hotword != "" else cfg.get("hotword", "")
    need = batch_size_s or int(cfg.get("batch_size_s", 300))
    min_chars = int(cfg.get("min_chars", 100))

    raw_dir = os.path.join(out_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    terms_mod.seed_if_missing()

    media = core.list_media(src)
    if not media:
        raise SystemExit(f"✗ 目录内没有音视频文件：{src}\n  提示：采集产物通常在 <输出>/Download/UIDxxx_昵称_发布作品/ 下")

    items = core.make_items(media, min_chars=min_chars,
                            raw_dir=None if no_resume else raw_dir)

    # 编号口径：若有「页面顺序.txt」就按抖音作品页顺序（01 = 置顶/首条），
    # 否则按发布时间倒序（最新在前）—— 两种都比你按文件名瞎猜靠谱。
    page = core.load_page_order(src)
    if page:
        items = core.reorder_by_page(items, page)
    else:
        items.sort(key=lambda i: (i["info"]["key"] is not None,
                                  i["info"]["key"] or (), i["stem"]), reverse=True)
        for n, it in enumerate(items, 1):
            it["idx"] = n

    picked = core.parse_selection(select, items)
    if not picked:
        raise SystemExit("✗ 没有选中任何条目")
    items = picked

    todo = [i for i in items if i["status"] != "done"]
    total_audio = sum(i["dur"] for i in items)
    done_audio = sum(i["dur"] for i in items if i["status"] == "done")
    started = time.time()

    progress = core.write_progress(out_dir, items, started, done_audio, 0.0, total_audio)
    print(f"选中 {len(items)} 条（总库 {len(media)} 条）｜已完成 {len(items) - len(todo)} 条"
          f"｜本次转写 {len(todo)} 条｜音频合计 {core.fmt_sec(total_audio)}")
    print(f"进度页：{progress}")

    if todo:
        for cmd, hint in (("ffmpeg", "安装 ffmpeg 并加入 PATH"), ("ffprobe", "同上")):
            if not shutil.which(cmd):
                raise SystemExit(f"✗ 找不到 {cmd}：{hint}")

        miss = missing_models(fmodel)
        if miss:
            mb = sum(m for _m, m in miss)
            print(f"\n⚠ 首次使用需下载 {len(miss)} 个模型，约 {mb} MB：")
            for m, size in miss:
                print(f"    · {m}（约 {size} MB）")
            print("  funasr 会在开始转写时自动下载到本地缓存，只需一次。\n")

        import torch
        from funasr import AutoModel

        # funasr 默认 INFO 级日志会把模型加载细节全打出来，淹没我们自己的进度行
        import logging
        logging.getLogger("funasr").setLevel(logging.WARNING)
        logging.getLogger("modelscope").setLevel(logging.WARNING)

        dev = resolve_device(device or cfg.get("device", "auto"))
        label = f"（{torch.cuda.get_device_name(0)}）" if dev == "cuda" else ""
        print(f"\n设备：{dev}{label}｜模型：{fmodel}")

        t0 = time.time()
        model = AutoModel(
            model=MODEL_ALIAS.get(fmodel, fmodel),
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=dev,
            disable_update=True,
        )
        print(f"模型加载完成，用时 {time.time() - t0:.0f} 秒\n")

        work = tempfile.mkdtemp(prefix="dyasr_")
        wall = 0.0
        try:
            for it in todo:
                it["status"] = "run"
                core.write_progress(out_dir, items, started, done_audio, wall, total_audio)
                print(f"[{it['idx']}/{len(items)}] {it['name']} ({int(it['dur'])}s) …",
                      end=" ", flush=True)
                t1 = time.time()
                try:
                    wav = _extract_wav(it["path"], work)
                    kw = {"input": wav, "batch_size_s": need}
                    if hotword:
                        kw["hotword"] = hotword
                    res = model.generate(**kw)
                    text = core.clean_text(res[0]["text"]) if res else ""
                    if core.char_count(text) >= min_chars:
                        with open(os.path.join(raw_dir, it["stem"] + ".txt"), "w",
                                  encoding="utf-8") as f:
                            f.write(text)
                        it["status"], it["chars"] = "done", core.char_count(text)
                        cost = time.time() - t1
                        print(f"{cost:.0f}s  {it['chars']}字  ({it['dur'] / max(cost, 0.1):.0f}x 实时)")
                    else:
                        it["status"] = "fail"
                        print(f"内容过短（{core.char_count(text)} 字），已丢弃")
                except Exception as e:                      # 单条失败不中断整批
                    it["status"] = "fail"
                    print(f"失败 {type(e).__name__}: {str(e)[:120]}")
                done_audio += it["dur"]
                wall += time.time() - t1
                core.write_progress(out_dir, items, started, done_audio, wall, total_audio)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    terms_map = terms_mod.load_terms()
    core.save_meta(out_dir, items)      # 时长旁挂，供 rebuild 在源视频删除后仍能显示
    doc, csv_path = core.build_outputs(
        out_dir, items, engine=f"funasr/{fmodel}", doc_name=doc_name,
        terms_map=terms_map, raw_dir=raw_dir)

    ok = sum(1 for i in items if i["status"] == "done")
    fail = [i for i in items if i["status"] == "fail"]
    print(f"\n✓ 完成：{ok}/{len(items)} 条" + (f"，失败 {len(fail)} 条" if fail else ""))
    for i in fail:
        print(f"  失败：{i['name']}")
    print(f"  合集：{doc}\n  索引：{csv_path}\n  进度页：{progress}")

    # 源视频用完即弃：稿件、时长、标点都已在硬盘上，视频只是占地方。
    # 只删「本次转写成功」的那些；**未选中的、转写失败的都不动** —— 用户可能
    # 只是想先转几条看看，剩下的回头再说，不能顺手把人家的候选池清空。
    if not keep_videos:
        all_stems = {os.path.splitext(os.path.basename(p))[0] for p in media}
        done_stems = {i["stem"] for i in items if i["status"] == "done"}
        keep, _delete = clean_mod.plan(all_stems, done_stems)
        n, freed, left = clean_mod.clean_videos(src, keep_stems=keep)
        if n:
            clean_mod.prune_empty_dirs(src)
            print(f"\n✓ 已删除 {n} 个已转写的源视频，释放 {core.human_size(freed)}"
                  + (f"；保留 {left} 个（未选中或转写失败）" if left else ""))
            print("  （想保留源视频：加 --keep-videos）")

    if fail:
        print("\n重试失败项：直接再跑同一条 transcribe 命令即可（成功的会自动跳过）")
        return 1
    return 0
