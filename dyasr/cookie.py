"""Cookie 导入：把浏览器拿到的登录态转成 dyasr 能用的单行 Cookie。

为什么要单独做一个模块：用户不该去 F12 里翻请求头。只要 Agent 能用浏览器
打开 douyin.com（用户扫码登录一次），就能从浏览器的 cookie API 里导出全部
Cookie —— 注意 **必须用 context.cookies() 这类 API，不能用 document.cookie**，
因为 sessionid / sid_tt 是 HttpOnly 的，JS 读不到。

支持的输入格式（自动识别，不用指定）：
  1. Playwright / Puppeteer storage state：{"cookies":[{"name","value",...}], ...}
  2. DevTools 导出的 Cookie 数组：[{"name":"ttwid","value":"..."}, ...]
  3. Netscape cookies.txt（curl --cookie-jar 的格式，浏览器扩展常用）
  4. 原始请求头字符串：ttwid=xxx; odin_tt=yyy
"""
import json
import os
import re
from email.utils import parsedate_to_datetime

from . import paths

# 缺了这些基本等于没登录
CRITICAL = ("sessionid", "sessionid_ss", "sid_tt", "ttwid")
EXPECTED = CRITICAL + ("odin_tt", "uid_tt", "uid_tt_ss", "passport_csrf_token",
                       "sid_guard", "passport_auth_mix_state")

_DOUYIN_DOMAIN = re.compile(r"(^|\.)douyin\.com$", re.I)


def _keep(domain: str) -> bool:
    """只保留抖音的 Cookie。空 domain 表示来自原始字符串，一律保留。"""
    if not domain:
        return True
    d = domain.lstrip(".").lower()
    return bool(_DOUYIN_DOMAIN.search(d))


def _from_json(text: str):
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if isinstance(data, dict):
        # 兼容 {"ttwid": "xxx", "sid_tt": "yyy"} 这种扁平字典
        return [(str(k), str(v), "") for k, v in data.items()]
    out = []
    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            continue
        out.append((str(item["name"]), str(item.get("value", "")),
                    str(item.get("domain", ""))))
    return out


def _from_netscape(text: str):
    out = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#") and "\t" not in line:
            continue
        parts = line.strip().split("\t")
        if len(parts) >= 7:
            domain, _flag, _path, _secure, _exp, name, value = parts[:7]
            out.append((name, value, domain.lstrip("#")))
    return out


def _from_header(text: str):
    out = []
    for part in text.replace("\n", ";").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            out.append((name.strip(), value.strip(), ""))
    return out


def parse(text: str):
    """自动识别格式，返回 [(name, value, domain)]，已过滤非抖音域。"""
    text = (text or "").strip()
    if not text:
        return []
    pairs = None
    if text[0] in "[{":
        try:
            pairs = _from_json(text)
        except Exception:
            pairs = None
    if pairs is None and "\t" in text:
        pairs = _from_netscape(text)
    if not pairs:
        pairs = _from_header(text)
    seen, out = set(), []
    for name, value, domain in pairs:
        if not name or name in seen or not _keep(domain):
            continue
        seen.add(name)
        out.append((name, value, domain))
    return out


def to_header(pairs) -> str:
    """拼成请求头里那一行；关键键放前面，便于人工核对。"""
    def rank(name):
        return (CRITICAL + EXPECTED).index(name) if name in (CRITICAL + EXPECTED) else 99
    ordered = sorted(pairs, key=lambda p: rank(p[0]))
    return "; ".join(f"{n}={v}" for n, v, _d in ordered)


def import_text(text, path=None):
    pairs = parse(text)
    if not pairs:
        raise SystemExit("✗ 没能从这段输入里解析出任何抖音 Cookie。\n"
                         "  支持：Playwright storage state JSON / Cookie 数组 JSON / "
                         "Netscape cookies.txt / 原始请求头字符串")
    return save(pairs, path)


def import_file(file_path, path=None):
    with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
        return import_text(f.read(), path)


def save(pairs, path=None):
    target = path or paths.cookie_path()
    paths.ensure_home()
    header = to_header(pairs)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "\n")
    os.chmod(target, 0o600)
    return target, header, dict((n, v) for n, v, _d in pairs)


def status():
    """离线检查：有没有、够不够、什么时候过期。不发网络请求。"""
    p = paths.cookie_path()
    if not p.exists():
        return {"present": False, "path": str(p), "keys": [], "missing": list(CRITICAL),
                "expires_at": None, "days_left": None, "bytes": 0}
    raw = p.read_text(encoding="utf-8-sig", errors="ignore").strip()
    pairs = dict((n, v) for n, v, _d in parse(raw)) if raw else {}
    missing = [k for k in CRITICAL if k not in pairs]
    info = {"present": bool(raw), "path": str(p), "keys": sorted(pairs),
            "missing": missing, "expires_at": None, "days_left": None,
            "bytes": len(raw)}
    sg = pairs.get("sid_guard", "")
    if sg:
        # sid_guard 的值是 URL 编码的：xxx%7C<uid>%7C<ttl>%7CThu%2C+31-Dec-2026...
        from urllib.parse import unquote_plus
        fields = unquote_plus(sg).split("|")
        if len(fields) >= 4:
            try:
                exp = parsedate_to_datetime(fields[3].strip())
                import datetime as _dt
                info["expires_at"] = exp.strftime("%Y-%m-%d")
                info["days_left"] = (exp - _dt.datetime.now(exp.tzinfo)).days
            except Exception:
                pass
    return info


def clear():
    p = paths.cookie_path()
    if p.exists():
        p.unlink()
        return True
    return False


def describe(info) -> str:
    """给人类看的一行结论。"""
    if not info["present"]:
        return "未配置"
    if info["missing"]:
        return f"不完整，缺 {'、'.join(info['missing'])}（多半是导出的不是登录态）"
    if info["days_left"] is not None:
        if info["days_left"] < 0:
            return f"已过期（{info['expires_at']}）"
        if info["days_left"] <= 7:
            return f"可用，但 {info['expires_at']} 就到期（剩 {info['days_left']} 天）"
        return f"可用，{info['expires_at']} 到期（剩 {info['days_left']} 天）"
    return "可用（未解析到到期时间）"
