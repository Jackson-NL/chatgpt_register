"""批量注册 API：创建、查询进度、日志和取消。"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Batch, Registration
from ..schemas import BatchCreate, BatchOut, RegistrationOut
from ..services.batch import BatchCoordinator
from ..services.registrations import RegistrationService

router = APIRouter()

SERVICE: BatchCoordinator | None = None


def get_service() -> BatchCoordinator:
    if SERVICE is None:
        raise HTTPException(503, "批量注册服务未初始化")
    return SERVICE


def init_batches(reg_service: RegistrationService) -> BatchCoordinator:
    global SERVICE
    SERVICE = BatchCoordinator(reg_service)
    return SERVICE


@router.post("", response_model=BatchOut)
async def create_batch(payload: BatchCreate, db: Session = Depends(get_db)):
    if payload.gmail_mode:
        running_gmail_batch = db.scalar(
            select(Batch)
            .where(Batch.status == "running", Batch.gmail_mode == True)  # noqa: E712
            .order_by(Batch.id.desc())
            .limit(1)
        )
        if running_gmail_batch:
            raise HTTPException(
                409,
                f"已有 Gmail 订单批量正在运行（batch #{running_gmail_batch.id}），请等待完成或停止后再启动",
            )
    try:
        batch_id = await get_service().start(
            target=payload.target,
            concurrency=payload.concurrency,
            proxy=payload.proxy,
            headless=payload.headless,
            debug_mode=payload.debug_mode,
            debug_trace=payload.debug_trace,
            bind_totp=payload.bind_totp,
            gmail_mode=payload.gmail_mode,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    batch = db.get(Batch, batch_id)
    regs = db.scalars(select(Registration).where(Registration.batch_id == batch_id).order_by(Registration.id.desc())).all()
    out = BatchOut.model_validate(batch)
    out.registrations = [RegistrationOut.model_validate(r) for r in regs]
    return out


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "批量任务不存在")
    regs = db.scalars(select(Registration).where(Registration.batch_id == batch_id).order_by(Registration.id.desc())).all()
    out = BatchOut.model_validate(batch)
    out.registrations = [RegistrationOut.model_validate(r) for r in regs]
    return out


@router.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "批量任务不存在")
    ok = await get_service().cancel(batch_id)
    return {"ok": ok, "batch_id": batch_id, "status": "canceled"}


@router.get("/{batch_id}/logs")
def get_batch_logs(batch_id: int, after: int = 0, limit: int = 300, db: Session = Depends(get_db)):
    """读取批量协调阶段日志，覆盖 Gmail 注册创建 registration 之前的阶段。"""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "批量任务不存在")
    try:
        lines = json.loads(batch.logs_json or "[]")
    except (TypeError, ValueError):
        lines = []
    safe_after = max(0, int(after or 0))
    safe_limit = min(max(1, int(limit or 300)), 1000)
    output = [line for line in lines if int(line.get("seq", 0)) > safe_after]
    output = output[-safe_limit:]
    return {
        "logs": output,
        "next": int(lines[-1].get("seq", safe_after)) if lines else safe_after,
        "total": len(lines),
    }


@router.delete("/{batch_id}/logs")
def clear_batch_logs(batch_id: int):
    if not get_service().clear_logs(batch_id):
        raise HTTPException(404, "批量任务不存在")
    return {"ok": True, "batch_id": batch_id, "cleared": True}
