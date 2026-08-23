"""调试抓包/截图缓存：供有头调试时给前端/助手实时拉取证据。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ..config import settings

# 内存缓存：reg_id -> {screenshot: bytes, har: list, trace_path: str}
_DEBUG_SCREENSHOTS: dict[int, bytes] = {}
_DEBUG_HAR: dict[int, list[dict]] = {}
_DEBUG_TRACE_PATH: dict[int, str] = {}
_DEBUG_LOCK = asyncio.Lock()

HAR_REDACT_KEYS = {"authorization", "cookie", "set-cookie", "x-api-key"}

def _redact_headers(headers: dict) -> dict:
    out = {}
    for k, v in (headers or {}).items():
        if k.lower() in HAR_REDACT_KEYS:
            out[k] = "[hidden]"
        else:
            out[k] = v
    return out

def _redact_url(url: str) -> str:
    # 脱敏 token/code/state
    import re
    url = re.sub(r"(?i)(code|state|token)=([^&]+)", r"\1=[hidden]", url)
    return url[:2000]

async def attach_debug_capture(context, page, reg_id: int, har_path: str | None = None) -> None:
    """给 playwright context/page 挂抓包与截图能力。"""
    _DEBUG_HAR.setdefault(reg_id, [])
    har_buffer = _DEBUG_HAR[reg_id]

    async def on_request(request):
        try:
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "type": "request",
                "method": request.method,
                "url": _redact_url(request.url),
                "headers": _redact_headers(await request.all_headers() if hasattr(request, "all_headers") else {}),
                "resourceType": getattr(request, "resource_type", ""),
            }
            # postData 按需截断
            try:
                pd = request.post_data or ""
                if pd:
                    entry["postData"] = pd[:2000]
            except Exception:
                pass
            har_buffer.append(entry)
            if len(har_buffer) > 2000:
                del har_buffer[:500]
        except Exception:
            pass

    async def on_response(response):
        try:
            req = response.request
            entry = {
                "ts": time.strftime("%H:%M:%S"),
                "type": "response",
                "method": req.method if req else "",
                "url": _redact_url(response.url),
                "status": response.status,
                "headers": _redact_headers(await response.all_headers() if hasattr(response, "all_headers") else {}),
            }
            har_buffer.append(entry)
            if len(har_buffer) > 2000:
                del har_buffer[:500]
        except Exception:
            pass

    try:
        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))
    except Exception:
        pass

    # 可选：context.tracing 供后续导出 zip
    if har_path:
        _DEBUG_TRACE_PATH[reg_id] = har_path
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        except Exception:
            pass

async def capture_screenshot(page, reg_id: int) -> bytes | None:
    try:
        data = await page.screenshot(type="png", full_page=False)
        _DEBUG_SCREENSHOTS[reg_id] = data
        return data
    except Exception:
        return _DEBUG_SCREENSHOTS.get(reg_id)

def get_screenshot(reg_id: int) -> bytes | None:
    return _DEBUG_SCREENSHOTS.get(reg_id)

def get_har(reg_id: int) -> list[dict]:
    return list(_DEBUG_HAR.get(reg_id, []))

def get_trace_path(reg_id: int) -> str | None:
    return _DEBUG_TRACE_PATH.get(reg_id)

async def stop_tracing(context, reg_id: int) -> str | None:
    path = _DEBUG_TRACE_PATH.get(reg_id)
    if not path:
        return None
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        await context.tracing.stop(path=path)
        return path
    except Exception:
        return path if Path(path).exists() else None

def clear_debug_capture(reg_id: int) -> None:
    _DEBUG_SCREENSHOTS.pop(reg_id, None)
    _DEBUG_HAR.pop(reg_id, None)
    _DEBUG_TRACE_PATH.pop(reg_id, None)

def ensure_debug_dir() -> Path:
    p = Path(settings.db_path).parent / "debug_har"
    p.mkdir(parents=True, exist_ok=True)
    return p
