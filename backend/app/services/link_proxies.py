"""cliproxy 风格代理 URL 的会话轮换与地区改写。

代理用户名格式：``{prefix}-region-{REGION}-sid-{SID}-t-{TTL}``，
轮换 sid 即可拿到新出口 IP；region 写 ``Rand`` 时由网关随机分配，
提链前需要把 ID/TH 出口固定下来（印尼 0 元链路：checkout=ID、update=TH）。
"""

from __future__ import annotations

import random
import re
from urllib.parse import urlsplit, urlunsplit

# 用户名段（@ 前、scheme:// 后）形如 prefix-region-XX-sid-SID-t-5
_CLIPROXY_USER_RE = re.compile(
    r"^(?P<prefix>.+?)-region-(?P<region>[A-Za-z0-9]+)-sid-(?P<sid>[A-Za-z0-9]+)-t-(?P<ttl>\d+)$"
)
_SID_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SID_LENGTH = 8


def split_proxy_url(proxy: str) -> tuple[str, str, str]:
    """拆成 (scheme://host:port, username, password)，无认证时后两项为空串。"""
    text = str(proxy or "").strip()
    if not text:
        return "", "", ""
    parsed = urlsplit(text)
    host = parsed.hostname or ""
    if not host:
        return text, "", ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        netloc = f"{host}:{parsed.port}" if parsed.port else host
    except ValueError:
        netloc = parsed.netloc
    scheme = parsed.scheme or "http"
    return f"{scheme}://{netloc}", parsed.username or "", parsed.password or ""


def _match_username(username: str) -> re.Match[str] | None:
    from urllib.parse import unquote

    return _CLIPROXY_USER_RE.match(unquote(username or ""))


def is_cliproxy_session(proxy: str) -> bool:
    _, username, _ = split_proxy_url(proxy)
    return _match_username(username) is not None


def _rebuild(base: str, username: str, password: str, *, region: str | None = None, sid: str | None = None) -> str:
    match = _match_username(username)
    if not match:
        return base
    from urllib.parse import quote

    new_region = (region or match.group("region")).strip()
    new_sid = (sid or match.group("sid")).strip()
    new_user = f"{match.group('prefix')}-region-{new_region}-sid-{new_sid}-t-{match.group('ttl')}"
    auth = quote(new_user, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    scheme, _, hostport = base.partition("://")
    return f"{scheme}://{auth}@{hostport}"


def new_sid() -> str:
    return "".join(random.choices(_SID_ALPHABET, k=_SID_LENGTH))


def rotate_proxy_sid(proxy: str) -> str:
    """轮换 cliproxy 会话 sid；非 cliproxy 代理原样返回。"""
    base, username, password = split_proxy_url(proxy)
    if not username or not _match_username(username):
        return str(proxy or "").strip()
    return _rebuild(base, username, password, sid=new_sid())


def apply_proxy_region(proxy: str, region: str) -> str:
    """把 cliproxy 用户名里的 region 固定为指定国家（如 ID/TH）；其余原样返回。"""
    region = str(region or "").strip().upper()
    base, username, password = split_proxy_url(proxy)
    if not username or not region or not _match_username(username):
        return str(proxy or "").strip()
    return _rebuild(base, username, password, region=region)


def proxy_region(proxy: str) -> str:
    _, username, _ = split_proxy_url(proxy)
    match = _match_username(username)
    return match.group("region").upper() if match else ""
