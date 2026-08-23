from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi.responses import JSONResponse, Response

from ..db import get_db
from ..models import Registration
from ..schemas import RegistrationCreate, RegistrationOut
from ..services.registrations import RegistrationService

router = APIRouter()

# 由 main.py lifespan 注入单例
SERVICE: RegistrationService | None = None


class LogRedactBody(BaseModel):
    enabled: bool


def get_service() -> RegistrationService:
    if SERVICE is None:
        raise HTTPException(503, "注册服务未初始化")
    return SERVICE


@router.get("/log-redact")
def get_log_redact():
    """当前日志脱敏开关状态。"""
    from ..services.registrator import is_redact_enabled

    return {"enabled": is_redact_enabled()}


@router.post("/log-redact")
def set_log_redact(payload: LogRedactBody):
    """切换日志脱敏开关：False 时新产生的日志会输出明文密码/TOTP/验证码（仅调试用）。"""
    from ..services.registrator import emit_log, set_redact_enabled

    enabled = set_redact_enabled(payload.enabled)
    emit_log(f"[system] 日志明文显示已{'开启' if enabled else '关闭'}（仅影响之后新产生的日志）")
    return {"enabled": enabled}


@router.post("", response_model=RegistrationOut)
async def create_registration(payload: RegistrationCreate, db: Session = Depends(get_db)):
    reg_id = await get_service().submit(
        proxy=payload.proxy, headless=payload.headless, bind_totp=payload.bind_totp,
        debug_mode=payload.debug_mode, debug_trace=payload.debug_trace,
        gmail_alias=payload.gmail_alias, gmail_mail_id=payload.gmail_mail_id,
    )
    return db.get(Registration, reg_id)


@router.get("", response_model=list[RegistrationOut])
def list_registrations(limit: int = 50, db: Session = Depends(get_db)):
    return db.scalars(select(Registration).order_by(Registration.id.desc()).limit(limit)).all()


@router.get("/{registration_id}", response_model=RegistrationOut)
def get_registration(registration_id: int, db: Session = Depends(get_db)):
    reg = db.get(Registration, registration_id)
    if not reg:
        raise HTTPException(404, "注册任务不存在")
    return reg


@router.post("/{registration_id}/cancel")
async def cancel_registration(registration_id: int, db: Session = Depends(get_db)):
    reg = db.get(Registration, registration_id)
    if not reg:
        raise HTTPException(404, "注册任务不存在")
    ok = get_service().cancel_registration(registration_id)
    return {"ok": ok, "registration_id": registration_id, "status": "canceled"}


@router.post("/{registration_id}/debug/release")
async def release_debug_registration(registration_id: int, db: Session = Depends(get_db)):
    reg = db.get(Registration, registration_id)
    if not reg:
        raise HTTPException(404, "注册任务不存在")
    if reg.status != "debug_waiting":
        raise HTTPException(409, "该注册任务当前不在调试暂停状态")
    if not get_service().release_debug_registration(registration_id):
        raise HTTPException(409, "调试等待已结束或任务已退出")
    return {"ok": True, "registration_id": registration_id, "status": "releasing_debug"}


@router.get("/{registration_id}/logs")
def get_registration_logs(registration_id: int, after: int = 0, limit: int = 200):
    return get_service().get_logs(registration_id, after=after, limit=limit)


@router.delete("/{registration_id}/logs")
def clear_registration_logs(registration_id: int):
    if not get_service().clear_logs(registration_id):
        raise HTTPException(404, "注册任务不存在")
    return {"ok": True, "registration_id": registration_id, "cleared": True}


@router.get("/{registration_id}/debug/screenshot")
def get_debug_screenshot(registration_id: int):
    """有头调试：返回最新截图 PNG（前端轮询/助手抓屏用）。"""
    try:
        from ..services.debug_capture import get_screenshot
    except Exception:
        raise HTTPException(404, "调试捕获未启用")
    data = get_screenshot(registration_id)
    if not data:
        raise HTTPException(404, "暂无截图（任务未进入浏览器阶段或 debug_trace 未开启）")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{registration_id}/debug/har")
def get_debug_har(registration_id: int, limit: int = 500, db: Session = Depends(get_db)):
    """有头调试：返回本任务的 HAR 抓包增量（已脱敏）。"""
    reg = db.get(Registration, registration_id)
    if not reg:
        raise HTTPException(404, "注册任务不存在")
    try:
        from ..services.debug_capture import get_har
    except Exception:
        return {"registration_id": registration_id, "items": [], "total": 0}
    items = get_har(registration_id)
    total = len(items)
    if limit > 0:
        items = items[-min(limit, 2000):]
    return {"registration_id": registration_id, "items": items, "total": total}


@router.get("/{registration_id}/debug/trace")
def get_debug_trace(registration_id: int, db: Session = Depends(get_db)):
    """有头调试：下载 Playwright trace zip（可用 https://trace.playwright.dev 打开）。"""
    reg = db.get(Registration, registration_id)
    if not reg:
        raise HTTPException(404, "注册任务不存在")
    try:
        from ..services.debug_capture import get_trace_path
        from pathlib import Path
    except Exception:
        raise HTTPException(404, "调试捕获未启用")
    path = get_trace_path(registration_id)
    if not path or not Path(path).exists():
        raise HTTPException(404, "暂无 trace（任务未完成或 debug_trace 未开启）")
    data = Path(path).read_bytes()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="reg_{registration_id}_trace.zip"'},
    )


@router.get("/{registration_id}/debug/status")
def get_debug_status(registration_id: int, db: Session = Depends(get_db)):
    reg = db.get(Registration, registration_id)
    if not reg:
        raise HTTPException(404, "注册任务不存在")
    try:
        from ..services.debug_capture import get_har, get_screenshot, get_trace_path
        has_screenshot = get_screenshot(registration_id) is not None
        har_count = len(get_har(registration_id))
        trace_path = get_trace_path(registration_id)
        from pathlib import Path
        has_trace = bool(trace_path and Path(trace_path).exists())
    except Exception:
        has_screenshot = False
        har_count = 0
        has_trace = False
    return {
        "registration_id": registration_id,
        "status": reg.status,
        "debug_mode": bool(reg.debug_mode),
        "debug_trace": bool(getattr(reg, "debug_trace", False)),
        "has_screenshot": has_screenshot,
        "har_count": har_count,
        "has_trace": has_trace,
    }
