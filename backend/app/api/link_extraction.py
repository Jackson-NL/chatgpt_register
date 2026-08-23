from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import LinkExtractionAccountsOut, LinkExtractionCreate, LinkExtractionJobOut
from ..services.link_extraction import LinkExtractionService

router = APIRouter()
SERVICE = LinkExtractionService()


def get_service() -> LinkExtractionService:
    return SERVICE


def _item_out(item) -> dict:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "account_id": item.account_id,
        "email": item.email,
        "status": item.status,
        "stage": item.stage,
        "progress": item.progress,
        "session_kind": item.session_kind,
        "checkout_session_id": item.checkout_session_id,
        "currency": item.currency,
        "amount_due": item.amount_due,
        "provider_url": item.provider_url,
        "paypal_url": item.paypal_url,
        "gopay_url": item.gopay_url,
        "gcash_url": item.gcash_url,
        "error": item.error,
        "network_error": item.network_error,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
    }


@router.get("/accounts", response_model=LinkExtractionAccountsOut)
def list_link_accounts(q: str = "", has_token: bool = True, page: int = 1, page_size: int = 50, db: Session = Depends(get_db)):
    return SERVICE.list_accounts(db, q=q, has_token=has_token, page=page, page_size=page_size)


@router.post("/jobs", response_model=LinkExtractionJobOut)
async def create_link_job(
    payload: LinkExtractionCreate,
    db: Session = Depends(get_db),
    service: LinkExtractionService = Depends(get_service),
):
    try:
        job = await service.create_job(payload, db)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    service.start_job(job.id)
    return job


@router.get("/jobs", response_model=list[LinkExtractionJobOut])
def list_link_jobs(limit: int = 30, db: Session = Depends(get_db)):
    return LinkExtractionService.list_jobs(db, limit=limit)


@router.get("/jobs/{job_id}", response_model=LinkExtractionJobOut)
def get_link_job(job_id: int, db: Session = Depends(get_db)):
    job = LinkExtractionService.get_job(db, job_id)
    if not job:
        raise HTTPException(404, "提链任务不存在")
    return job


@router.get("/jobs/{job_id}/items")
def list_link_items(job_id: int, db: Session = Depends(get_db)):
    if not LinkExtractionService.get_job(db, job_id):
        raise HTTPException(404, "提链任务不存在")
    return [_item_out(item) for item in LinkExtractionService.list_items(db, job_id)]


@router.get("/jobs/{job_id}/logs")
def get_link_logs(job_id: int, after: int = 0, limit: int = 300, db: Session = Depends(get_db)):
    if not LinkExtractionService.get_job(db, job_id):
        raise HTTPException(404, "提链任务不存在")
    return LinkExtractionService.get_logs(db, job_id, after=after, limit=limit)


@router.post("/jobs/{job_id}/cancel", response_model=LinkExtractionJobOut)
async def cancel_link_job(job_id: int, service: LinkExtractionService = Depends(get_service)):
    job = await service.cancel_job(job_id)
    if not job:
        raise HTTPException(404, "提链任务不存在")
    return job
