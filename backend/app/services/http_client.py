"""网络层：curl_cffi impersonate="chrome"（TLS/JA3 指纹伪装）

替代裸 curl.exe / httpx，请求层指纹与浏览器 Chrome 一致，
用于 CF 临时邮箱 API / SMSBower API 等外部请求。
"""
import asyncio
import json
from typing import Any

from curl_cffi import requests as curl_requests

from ..config import settings

PROXY = settings.default_proxy
IMPERSONATE = "chrome"


def _get_kwargs(proxy: str = PROXY, timeout: float = 20.0) -> dict:
    kw: dict[str, Any] = {
        "impersonate": IMPERSONATE,
        "timeout": timeout,
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        },
    }
    if proxy:
        kw["proxies"] = {"http": proxy, "https": proxy}
    return kw


def get_sync(url: str, params: dict | None = None, proxy: str = PROXY, timeout: float = 20.0) -> str:
    """同步 GET（TLS 指纹 Chrome）"""
    resp = curl_requests.get(url, params=params, **_get_kwargs(proxy, timeout))
    resp.raise_for_status()
    return resp.text


def post_json_sync(url: str, body: dict, proxy: str = PROXY, timeout: float = 20.0, headers: dict | None = None) -> dict:
    """同步 POST JSON（TLS 指纹 Chrome）"""
    kw = _get_kwargs(proxy, timeout)
    kw["headers"]["Content-Type"] = "application/json"
    if headers:
        kw["headers"].update(headers)
    resp = curl_requests.post(url, data=json.dumps(body), **kw)
    resp.raise_for_status()
    return resp.json()


def get_json_sync(url: str, headers: dict | None = None, proxy: str = PROXY, timeout: float = 20.0) -> dict:
    kw = _get_kwargs(proxy, timeout)
    if headers:
        kw["headers"].update(headers)
    resp = curl_requests.get(url, **kw)
    resp.raise_for_status()
    return resp.json()


# ---------- 异步封装 ----------

async def get(url: str, params: dict | None = None, proxy: str = PROXY, timeout: float = 20.0) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_sync(url, params, proxy, timeout))


async def get_json(url: str, headers: dict | None = None, proxy: str = PROXY, timeout: float = 20.0) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_json_sync(url, headers, proxy, timeout))


async def post_json(url: str, body: dict, proxy: str = PROXY, timeout: float = 20.0, headers: dict | None = None) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: post_json_sync(url, body, proxy, timeout, headers))
