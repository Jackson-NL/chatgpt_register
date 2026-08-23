from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import Sub2APIReloginCreate, Sub2APIReloginJobOut, Sub2APIReloginPreviewOut
from ..services.sub2api import Sub2APIError
from ..services.sub2api_relogin import Sub2APIReloginService, _safe_group_ids

router = APIRouter()
SERVICE = Sub2APIReloginService()


def get_service() -> Sub2APIReloginService:
    return SERVICE


def _item_out(item) -> dict:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "remote_account_id": item.remote_account_id,
        "local_account_id": item.local_account_id,
        "email": item.email,
        "remote_status": item.remote_status,
        "remote_error": item.remote_error,
        "status": item.status,
        "reason": item.reason,
        "error": item.error,
        "reauth_endpoint": item.reauth_endpoint,
        "callback_endpoint": item.callback_endpoint,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
    }


@router.get("/preview", response_model=Sub2APIReloginPreviewOut)
async def preview_relogin(group_ids: str, only_error: bool = True, service: Sub2APIReloginService = Depends(get_service)):
    try:
        return await service.preview(_safe_group_ids(group_ids), only_error=only_error)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Sub2APIError as error:
        raise HTTPException(502, str(error)) from error


@router.post("/jobs", response_model=Sub2APIReloginJobOut)
async def create_relogin_job(
    payload: Sub2APIReloginCreate,
    db: Session = Depends(get_db),
    service: Sub2APIReloginService = Depends(get_service),
):
    try:
        job = await service.create_job(payload, db)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Sub2APIError as error:
        raise HTTPException(502, str(error)) from error
    service.start_job(job.id)
    return job


@router.get("/jobs", response_model=list[Sub2APIReloginJobOut])
def list_relogin_jobs(limit: int = 30, db: Session = Depends(get_db)):
    return Sub2APIReloginService.list_jobs(db, limit=limit)


@router.get("/jobs/{job_id}", response_model=Sub2APIReloginJobOut)
def get_relogin_job(job_id: int, db: Session = Depends(get_db)):
    job = Sub2APIReloginService.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Sub2API 重登任务不存在")
    return job


@router.get("/jobs/{job_id}/items")
def list_relogin_items(job_id: int, db: Session = Depends(get_db)):
    job = Sub2APIReloginService.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Sub2API 重登任务不存在")
    return [_item_out(item) for item in Sub2APIReloginService.list_items(db, job_id)]


@router.get("/jobs/{job_id}/logs")
def get_relogin_logs(job_id: int, after: int = 0, limit: int = 300, db: Session = Depends(get_db)):
    job = Sub2APIReloginService.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "Sub2API 重登任务不存在")
    return Sub2APIReloginService.get_logs(db, job_id, after=after, limit=limit)


@router.post("/jobs/{job_id}/cancel", response_model=Sub2APIReloginJobOut)
async def cancel_relogin_job(job_id: int, service: Sub2APIReloginService = Depends(get_service)):
    job = await service.cancel_job(job_id)
    if not job:
        raise HTTPException(404, "Sub2API 重登任务不存在")
    return job
