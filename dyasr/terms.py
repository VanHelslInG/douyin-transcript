"""术语纠错表：ASR 对专有名词、行业术语、人名几乎必错，热词只能缓解一部分。

表是纯文本 TSV（错词 <TAB> 正确写法），每行一条，# 开头为注释。
放在 ~/.dyasr/terms.tsv，`dyasr rebuild` 时套用到正文与标题。

设计取舍：raw/*.txt 永远保存 ASR 原文，纠错只在生成汇总产物时套用。
这样改表不需要重跑转写（rebuild 不碰 GPU），也保留了可回溯的原始证据。
"""
import re
from pathlib import Path

from . import paths

DEFAULT_TERMS = """\
# dyasr 术语纠错表
# 格式：错词 <TAB> 正确写法
# 生效时机：dyasr transcribe / dyasr rebuild 生成「合集.md」和「索引.csv」时
# 注意：按行顺序替换（长词优先已自动处理），空行与 # 注释会被忽略。
# 这份表只是通用起点 —— ASR 的系统性错字绝大多数是「领域术语 + 人名」，
# 必须按你实际关注的博主补充，否则等于没有。

# ── 经济 / 金融 ──
结构性发债	明股实债
名股实债	明股实债
名股实责	明股实债
资产负责率	资产负债率
流动型陷阱	流动性陷阱
流动新陷阱	流动性陷阱
通涨	通胀
美联蓄	美联储

# ── 博弈论 / 经济学模型 ──
希克斯汉森	希克斯-汉森
盖尔沙普利	盖尔-沙普利
伯克森悖论	伯克森悖论
纳什均横	纳什均衡

# ── 婚恋 / 社会观察 ──
婚债市场	婚恋市场
沉没陈本	沉没成本
沉默成本	沉没成本
情绪价值	情绪价值
"""

# 行尾注释与空白的清洗
_COMMENT = re.compile(r"\s+#.*$")


def seed_if_missing(force: bool = False) -> Path:
    """首次运行把默认表写到家目录；已存在不覆盖，避免吃掉用户的自定义。"""
    paths.ensure_home()
    p = paths.SEED_TERMS
    if force or not p.exists():
        p.write_text(DEFAULT_TERMS, encoding="utf-8")
    return p


def load_terms(path: Path = None) -> dict:
    """返回 {错词: 正确写法}，长词在前。文件缺失时自动播种默认表。"""
    p = Path(path) if path else paths.SEED_TERMS
    if not p.exists():
        seed_if_missing()
    mapping = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line = _COMMENT.sub("", line)
        if "\t" not in line:
            continue
        wrong, right = line.split("\t", 1)
        wrong, right = wrong.strip(), right.strip()
        if wrong and wrong != right:
            mapping[wrong] = right
    return dict(sorted(mapping.items(), key=lambda kv: -len(kv[0])))


def apply_terms(text, mapping: dict):
    if not mapping or not text:
        return text
    for wrong, right in mapping.items():
        text = text.replace(wrong, right)
    return text


def count_hits(text: str, mapping: dict) -> dict:
    """统计每条规则命中次数，rebuild 时打印，便于发现表里写错或错配的规则。"""
    return {w: text.count(w) for w in mapping if text.count(w)}


# ──────────────────────── 模糊热词表 ────────────────────────

DEFAULT_HOTWORDS = """\
# dyasr 模糊热词表（一行一个「正确写法」）
# 与 terms.tsv 的区别：
#   terms.tsv   = 精确替换，你知道 ASR 错成什么样（如 结构性发债→明股实债）
#   hotwords.txt = 只给正确写法，靠拼音相似度去抓各种变形（名股实债/冥股实债…）
# 生效时机：rebuild 生成产物时，先跑 terms.tsv 精确替换，再跑本表模糊匹配。
# 阈值在 config.json 的 fuzzy_threshold（默认 0.95，调低抓得多但误伤也多）。

# ── 经济 / 金融 ──
明股实债
流动性陷阱
资产负债表衰退
土地财政
城投债
影子银行
财政转移支付
存量博弈
边际效用
帕累托改进
反身性

# ── 博弈论 / 经济学 ──
纳什均衡
伯克森悖论
幸存者偏差
沉没成本
盖尔-沙普利
希克斯-汉森
公共选择理论

# ── 婚恋 / 社会观察 ──
情绪价值
彩礼
嫁妆
生育率
大女主
原生家庭

# ── 常见人名 / 专有名词（按你关注的博主补充） ──
"""


def seed_hotwords(force: bool = False) -> Path:
    paths.ensure_home()
    p = paths.HOTWORDS_FILE
    if force or not p.exists():
        p.write_text(DEFAULT_HOTWORDS, encoding="utf-8")
    return p


def load_hotwords(path: Path = None) -> list:
    """返回模糊匹配用的目标词列表（去重、去注释、去空行）。"""
    p = Path(path) if path else paths.HOTWORDS_FILE
    if not p.exists():
        seed_hotwords()
    out, seen = [], set()
    for line in p.read_text(encoding="utf-8").splitlines():
        s = _COMMENT.sub("", line).strip()
        if not s or s.startswith("#"):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fuzzy_targets(terms_map=None, hotwords=None):
    """模糊匹配的目标词 = 热词表 ∪ terms.tsv 的正确写法。

    为什么合并：你在 terms.tsv 里写过「某错法 → 明股实债」，说明「明股实债」
    是本领域重要术语，那就该顺带用模糊匹配保护它的其他错法变体。
    """
    targets = list(hotwords if hotwords is not None else load_hotwords())
    extra = list((terms_map or {}).values())
    seen, merged = set(), []
    for t in targets + extra:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            merged.append(t)
    return merged
