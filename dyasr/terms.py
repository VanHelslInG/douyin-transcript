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
