"""文本级模糊纠错：把「读音接近某个术语」的片段替换成正确写法。

为什么不用 funasr 自带的那份：`funasr.utils.postprocess_hotwords` 一 import 就会
把 torch 整个拉起来，实测 23.6 秒——而 rebuild 的全部意义就是「秒级重建」，
为了纠错多等半分钟不值当。这里只用 pypinyin + rapidfuzz，两者导入都在 0.2 秒内。

算法（与 funasr 同思路）：
  1. 全文逐字求拼音，带缓存，避免重复计算
  2. 用「目标词长度 ±1」的滑窗扫全文
  3. 滑窗拼音与目标拼音做 fuzz.ratio，超过阈值即为候选
  4. 按分数从高到低贪心取不重叠的替换

与 terms.tsv 的分工：
  · terms.tsv  = 精确替换，确定、可穷举的错法（快，先跑）
  · 本模块     = 模糊匹配，覆盖「鬼知道 ASR 会错成什么样」的情况（慢一点，后跑）
"""
from functools import lru_cache

_LAZY = None


def _lazy_pinyin(ch: str) -> str:
    """单字转拼音（不带声调），非汉字原样返回。"""
    global _LAZY
    if _LAZY is None:
        try:
            from pypinyin import lazy_pinyin
        except ImportError:
            _LAZY = False
        else:
            _LAZY = lazy_pinyin
    if not _LAZY:
        return ch
    try:
        parts = _LAZY(ch, errors="default")
    except Exception:
        return ch
    return "".join(parts) if parts else ch


@lru_cache(maxsize=8192)
def _char_key(ch: str) -> str:
    return _lazy_pinyin(ch).lower()


def pinyin_key(text: str) -> str:
    return "".join(_char_key(c) for c in text)


def available() -> bool:
    """模糊纠错是否可用（需要 pypinyin + rapidfuzz）。"""
    try:
        import pypinyin  # noqa: F401
        import rapidfuzz  # noqa: F401
        return True
    except ImportError:
        return False


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


class FuzzyCorrector:
    """按拼音相似度把错字窗口替换成目标术语。

    三条防误伤的硬约束（都是实测被误伤逼出来的）：
      1. 窗口内必须全是汉字/字母数字 —— 否则「情绪、价值」会被吞成「情绪价值」
      2. 窗口与目标长度差 ≤ 1 —— 否则「生育」会被替换成「生育率」
      3. 窗口或目标互为子串时跳过 —— 已经是正确写法就别再动
    再加「首字拼音」索引做预筛，把 O(词数) 的比对压到 O(1~2)。
    """

    def __init__(self, targets, threshold=0.95, min_len=2):
        from rapidfuzz import fuzz  # 只有真要用时才导入
        self._fuzz = fuzz
        self.threshold = float(threshold)
        self.targets = []
        for t in targets:
            t = (t or "").strip()
            if len(t) >= min_len and all(_is_han(c) or c.isalnum() for c in t):
                py = pinyin_key(t)
                self.targets.append((t, py, len(t)))
        # 首字拼音 → 目标列表，滑窗时只跟同首音的比较
        self._by_head = {}
        for t, py, ln in self.targets:
            if py:
                self._by_head.setdefault(py[0], []).append((t, py, ln))

    def __bool__(self):
        return bool(self.targets)

    def apply(self, text):
        """返回 (纠正后的文本, 命中列表[(原文, 替换为, 分数)])。"""
        if not text or not self.targets:
            return text, []

        keys = [_char_key(c) for c in text]
        lens = sorted({t[2] for t in self.targets})
        lo, hi = max(1, min(lens) - 1), max(lens) + 1

        cands = []
        n = len(text)
        for win in range(lo, hi + 1):
            for start in range(0, n - win + 1):
                end = start + win
                seg = text[start:end]
                if not all(_is_han(c) or c.isalnum() for c in seg):
                    continue                      # 窗口里夹了标点 → 替换会把标点一起吞掉
                seg_py = "".join(keys[start:end])
                for target, tgt_py, tgt_len in self._by_head.get(seg_py[:1], ()):
                    if abs(tgt_len - win) > 1:
                        continue                  # 长度差太大，必然不是同一个词
                    if seg == target or seg in target or target in seg:
                        continue                  # 已经是正确写法（或它的片段）
                    score = self._fuzz.ratio(seg_py, tgt_py) / 100.0
                    if score >= self.threshold:
                        cands.append((score, start, end, target, seg))

        if not cands:
            return text, []

        # 分数高的优先；同分时长的优先（更具体的术语更可信）
        cands.sort(key=lambda c: (-c[0], -(c[2] - c[1])))
        taken, picked = [], []
        for score, a, b, target, seg in cands:
            if any(not (b <= x or a >= y) for x, y in taken):
                continue
            taken.append((a, b))
            picked.append((a, b, target, seg, score))

        picked.sort(key=lambda p: p[0])
        out, cursor = [], 0
        for a, b, target, seg, _score in picked:
            out.append(text[cursor:a])
            out.append(target)
            cursor = b
        out.append(text[cursor:])
        return "".join(out), [(seg, target, round(sc, 3)) for _a, _b, target, seg, sc in picked]
