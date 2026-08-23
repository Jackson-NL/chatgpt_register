"""提链工作台后台任务服务。

源项目的提链函数是同步实现，这里只负责账号选择、任务持久化、并发控制、日志投影
和取消协作；实际 Checkout/Stripe 流程继续由 payment_link_extractor 包负责。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import String, desc, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Account, LinkExtractionItem, LinkExtractionJob, utcnow
from .payment_link_extractor import extract_payment_link
from .payment_link_extractor.config import country_config, normalize_payment_method
from .payment_link_extractor.errors import ExtractionCancelled, NetworkError
from .payment_link_extractor.models import ExtractionConfig


MAX_LOG_LINES = 2000
STAGE_PROGRESS = {
    "queued": 0,
    "eligibility_check": 10,
    "checkout": 15,
    "checkout_update": 25,
    "stripe_init": 35,
    "elements_session": 50,
    "taxes": 65,
    "payment_confirmation": 80,
    "redirect_resolution": 95,
    "completed": 100,
}
TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
_JOBS: dict[int, asyncio.Task] = {}
_CANCEL_EVENTS: dict[int, threading.Event] = {}


def _safe_log_text(value: object, limit: int = 600) -> str:
    """日志允许展示阶段和接口结果，但不能泄露 Bearer/token 参数。"""
    text = str(value or "")
    text = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer [hidden]", text)
    text = re.sub(
        r"(?i)(access_token|refresh_token|id_token|password|totp_secret|authorization)\s*[:=]\s*[^,\s}]+",
        r"\1=[hidden]",
        text,
    )
    text = re.sub(r"(?i)(code|state|token)=([^&\s]+)", r"\1=[hidden]", text)
    return text[:limit]


def _payload_value(payload: Any, name: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _unique_ids(values: Any) -> list[int]:
    result: list[int] = []
    for value in values or []:
        try:
            account_id = int(value)
        except (TypeError, ValueError):
            continue
        if account_id > 0 and account_id not in result:
            result.append(account_id)
    if not result:
        raise ValueError("至少选择一个账号")
    return result


def _stage_name(stage: str) -> tuple[str, str]:
    value = str(stage or "queued")
    if value.startswith("checkout_kind:"):
        kind = value.split(":", 1)[1].strip()
        return "checkout", kind
    return value, ""


class LinkExtractionService:
    def __init__(self, extractor: Callable[..., Any] | None = None):
        self.extractor = extractor or extract_payment_link
        self._log_lock = asyncio.Lock()

    @staticmethod
    def list_accounts(db: Session, *, q: str = "", has_token: bool = True, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = max(10, min(200, int(page_size or 50)))
        query = select(Account)
        if has_token:
            query = query.where(Account.access_token != "")
        keyword = str(q or "").strip()
        if keyword:
            pattern = f"%{keyword}%"
            query = query.where((Account.email.ilike(pattern)) | (Account.phone.ilike(pattern)) | (Account.id.cast(String).ilike(pattern)))
        total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
        rows = list(db.scalars(query.order_by(desc(Account.id)).offset((page - 1) * page_size).limit(page_size)).all())
        items = [
            {
                "id": account.id,
                "email": account.email or "",
                "phone": account.phone or "",
                "status": account.status or "",
                "plan_type": account.plan_type or "",
                "has_access_token": bool(account.access_token),
                "has_refresh_token": bool(account.refresh_token),
                "has_profile": bool(account.profile_path),
                "profile_path": account.profile_path or "",
                "proxy": account.proxy or "",
            }
            for account in rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def create_job(self, payload: Any, db: Session) -> LinkExtractionJob:
        account_ids = _unique_ids(_payload_value(payload, "account_ids", []))
        country, *_ = country_config(str(_payload_value(payload, "country", "GB") or "GB"))
        payment_method = normalize_payment_method(_payload_value(payload, "payment_method", "paypal"))
        concurrency = max(1, min(5, int(_payload_value(payload, "concurrency", 2) or 2)))
        apply_update = bool(_payload_value(payload, "apply_checkout_update", True))
        oaics_only = bool(_payload_value(payload, "oaics_only", False))
        checkout_proxy = str(_payload_value(payload, "checkout_proxy", "") or "").strip()
        update_proxy = str(_payload_value(payload, "update_proxy", "") or "").strip()

        account_map = {account.id: account for account in db.scalars(select(Account).where(Account.id.in_(account_ids))).all()}
        missing_ids = [str(account_id) for account_id in account_ids if account_id not in account_map]
        if missing_ids:
            raise ValueError(f"账号不存在: {', '.join(missing_ids)}")

        job = LinkExtractionJob(
            status="pending",
            total=len(account_ids),
            pending=0,
            running=0,
            succeeded=0,
            failed=0,
            canceled=0,
            concurrency=concurrency,
            country=country,
            payment_method=payment_method,
            apply_checkout_update=apply_update,
            oaics_only=oaics_only,
            config_json=json.dumps(
                {
                    "checkout_proxy": checkout_proxy,
                    "update_proxy": update_proxy,
                    "country": country,
                    "payment_method": payment_method,
                    "apply_checkout_update": apply_update,
                    "oaics_only": oaics_only,
                },
                ensure_ascii=False,
            ),
            logs_json="[]",
        )
        db.add(job)
        db.flush()
        for account_id in account_ids:
            account = account_map[account_id]
            has_token = bool((account.access_token or "").strip())
            item = LinkExtractionItem(
                job_id=job.id,
                account_id=account.id,
                email=account.email or "",
                status="pending" if has_token else "failed",
                stage="queued" if has_token else "completed",
                progress=0 if has_token else 100,
                error="" if has_token else "账号缺少 access_token",
            )
            db.add(item)
            if has_token:
                job.pending += 1
            else:
                job.failed += 1
        db.commit()
        db.refresh(job)
        return job

    def start_job(self, job_id: int) -> None:
        task = _JOBS.get(job_id)
        if task and not task.done():
            return
        _CANCEL_EVENTS[job_id] = threading.Event()
        _JOBS[job_id] = asyncio.create_task(self.run_job(job_id))

    async def _append_log(self, job_id: int, message: str) -> None:
        async with self._log_lock:
            db = SessionLocal()
            try:
                job = db.get(LinkExtractionJob, job_id)
                if not job:
                    return
                try:
                    lines = json.loads(job.logs_json or "[]")
                except (TypeError, ValueError):
                    lines = []
                seq = int(lines[-1].get("seq", 0)) + 1 if lines else 1
                lines.append({"seq": seq, "ts": time.strftime("%H:%M:%S"), "msg": _safe_log_text(message)})
                job.logs_json = json.dumps(lines[-MAX_LOG_LINES:], ensure_ascii=False)
                db.commit()
            finally:
                db.close()

    async def _set_stage(self, job_id: int, item_id: int, stage: str) -> None:
        db = SessionLocal()
        try:
            item = db.get(LinkExtractionItem, item_id)
            if not item:
                return
            normalized, kind = _stage_name(stage)
            item.stage = normalized[:64]
            item.progress = STAGE_PROGRESS.get(normalized, item.progress)
            if kind:
                item.session_kind = kind[:64]
            db.commit()
        finally:
            db.close()

    async def _mark_running(self, job_id: int, item_id: int) -> bool:
        db = SessionLocal()
        try:
            job = db.get(LinkExtractionJob, job_id)
            item = db.get(LinkExtractionItem, item_id)
            if not job or not item or job.status == "canceled" or item.status != "pending":
                return False
            item.status = "running"
            item.stage = "checkout"
            item.progress = STAGE_PROGRESS["checkout"]
            item.started_at = utcnow()
            job.pending = max(0, job.pending - 1)
            job.running += 1
            db.commit()
            return True
        finally:
            db.close()

    async def _finish_item(self, job_id: int, item_id: int, status: str, *, result: dict[str, Any] | None = None, error: str = "", network_error: bool = False) -> None:
        db = SessionLocal()
        try:
            job = db.get(LinkExtractionJob, job_id)
            item = db.get(LinkExtractionItem, item_id)
            if not job or not item:
                return
            if job.status == "canceled" and status not in {"failed", "canceled"}:
                status = "canceled"
                error = "任务已取消"
            old_status = item.status
            item.status = status
            item.stage = "completed" if status == "succeeded" else ("canceled" if status == "canceled" else "failed")
            item.progress = 100 if status in {"succeeded", "failed", "canceled"} else item.progress
            item.error = _safe_log_text(error, 1000) if error else ""
            item.network_error = bool(network_error)
            item.finished_at = utcnow()
            if result:
                item.checkout_session_id = str(result.get("checkout_session_id") or "")
                item.session_kind = str(result.get("session_kind") or item.session_kind or "")
                item.currency = str(result.get("currency") or "")
                item.amount_due = result.get("amount_due")
                item.provider_url = str(result.get("provider_url") or "")
                item.paypal_url = str(result.get("paypal") or result.get("paypal_url") or "")
                item.gopay_url = str(result.get("gopay") or result.get("gopay_url") or "")
                item.gcash_url = str(result.get("gcash") or result.get("gcash_url") or "")
                item.result_json = json.dumps(result, ensure_ascii=False)
            if old_status == "running":
                job.running = max(0, job.running - 1)
            elif old_status == "pending":
                job.pending = max(0, job.pending - 1)
            if old_status not in TERMINAL_STATUSES:
                if status == "succeeded":
                    job.succeeded += 1
                elif status == "failed":
                    job.failed += 1
                elif status == "canceled":
                    job.canceled += 1
            db.commit()
        finally:
            db.close()

    async def _run_item(self, job_id: int, item_id: int, config: dict[str, Any]) -> None:
        if not await self._mark_running(job_id, item_id):
            return
        db = SessionLocal()
        try:
            item = db.get(LinkExtractionItem, item_id)
            account = db.get(Account, item.account_id) if item else None
            job = db.get(LinkExtractionJob, job_id)
            if not item or not account or not job:
                await self._finish_item(job_id, item_id, "failed", error="账号记录不存在")
                return
            checkout_proxy = str(config.get("checkout_proxy") or account.proxy or settings.default_proxy or "").strip()
            update_proxy = str(config.get("update_proxy") or checkout_proxy).strip()
            extraction_config = ExtractionConfig(
                access_token=account.access_token,
                checkout_proxy=checkout_proxy,
                update_proxy=update_proxy,
                country=str(config.get("country") or "GB"),
                payment_method=str(config.get("payment_method") or "paypal"),
                apply_checkout_update=bool(config.get("apply_checkout_update", True)),
                verbose=False,
                oaics_only=bool(config.get("oaics_only", False)),
            )
        finally:
            db.close()

        cancel_event = _CANCEL_EVENTS.get(job_id) or threading.Event()
        loop = asyncio.get_running_loop()

        def call_on_loop(coro: Any) -> None:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                future.result(timeout=10)
            except Exception:
                future.cancel()

        def stage_callback(stage: str) -> None:
            call_on_loop(self._set_stage(job_id, item_id, stage))

        def log_callback(message: str) -> None:
            call_on_loop(self._append_log(job_id, f"账号 #{item_id}: {message}"))

        await self._append_log(job_id, f"账号 #{item_id} 开始提链")
        try:
            result = await asyncio.to_thread(
                self.extractor,
                extraction_config,
                cancel_event=cancel_event,
                stage_callback=stage_callback,
                log_callback=log_callback,
            )
            result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            await self._finish_item(job_id, item_id, "succeeded", result=result_dict)
            await self._append_log(job_id, f"账号 #{item_id} 提链成功")
        except ExtractionCancelled:
            await self._finish_item(job_id, item_id, "canceled", error="任务已取消")
        except Exception as error:  # noqa: BLE001
            await self._finish_item(job_id, item_id, "failed", error=str(error), network_error=isinstance(error, NetworkError))
            await self._append_log(job_id, f"账号 #{item_id} 提链失败: {_safe_log_text(error, 300)}")

    async def run_job(self, job_id: int) -> None:
        db = SessionLocal()
        concurrency = 1
        try:
            job = db.get(LinkExtractionJob, job_id)
            if not job:
                return
            concurrency = max(1, int(job.concurrency or 1))
            try:
                config = json.loads(job.config_json or "{}")
            except (TypeError, ValueError):
                config = {}
            item_ids = [item.id for item in db.scalars(select(LinkExtractionItem).where(LinkExtractionItem.job_id == job_id, LinkExtractionItem.status == "pending").order_by(LinkExtractionItem.id)).all()]
            if job.status != "canceled":
                job.status = "running"
                job.started_at = job.started_at or utcnow()
                db.commit()
        finally:
            db.close()

        semaphore = asyncio.Semaphore(concurrency)

        async def worker(item_id: int) -> None:
            async with semaphore:
                if _CANCEL_EVENTS.get(job_id, threading.Event()).is_set():
                    await self._finish_item(job_id, item_id, "canceled", error="任务已取消")
                    return
                await self._run_item(job_id, item_id, config)

        await asyncio.gather(*(worker(item_id) for item_id in item_ids))
        db = SessionLocal()
        try:
            job = db.get(LinkExtractionJob, job_id)
            if not job:
                return
            if job.status != "canceled":
                job.status = "succeeded" if job.failed == 0 else "failed"
                job.finished_at = utcnow()
            else:
                job.pending = 0
                job.running = 0
                job.finished_at = job.finished_at or utcnow()
            db.commit()
        finally:
            db.close()
            _CANCEL_EVENTS.pop(job_id, None)

    async def cancel_job(self, job_id: int) -> LinkExtractionJob | None:
        event = _CANCEL_EVENTS.get(job_id)
        if event:
            event.set()
        db = SessionLocal()
        try:
            job = db.get(LinkExtractionJob, job_id)
            if not job:
                return None
            if job.status not in TERMINAL_STATUSES:
                job.status = "canceled"
                job.error = "用户取消任务"
                job.finished_at = utcnow()
                items = db.scalars(select(LinkExtractionItem).where(LinkExtractionItem.job_id == job_id, LinkExtractionItem.status.not_in(TERMINAL_STATUSES))).all()
                for item in items:
                    item.status = "canceled"
                    item.stage = "canceled"
                    item.progress = 100
                    item.error = "用户取消任务"
                    item.finished_at = utcnow()
                job.pending = 0
                job.running = 0
                job.canceled = sum(1 for item in db.scalars(select(LinkExtractionItem).where(LinkExtractionItem.job_id == job_id, LinkExtractionItem.status == "canceled")).all())
                db.commit()
                await self._append_log(job_id, "任务已取消，正在停止当前请求")
            db.refresh(job)
            return job
        finally:
            db.close()

    @staticmethod
    def list_jobs(db: Session, limit: int = 30) -> list[LinkExtractionJob]:
        return list(db.scalars(select(LinkExtractionJob).order_by(desc(LinkExtractionJob.id)).limit(max(1, min(100, limit)))).all())

    @staticmethod
    def get_job(db: Session, job_id: int) -> LinkExtractionJob | None:
        return db.get(LinkExtractionJob, job_id)

    @staticmethod
    def list_items(db: Session, job_id: int) -> list[LinkExtractionItem]:
        return list(db.scalars(select(LinkExtractionItem).where(LinkExtractionItem.job_id == job_id).order_by(LinkExtractionItem.id)).all())

    @staticmethod
    def get_logs(db: Session, job_id: int, after: int = 0, limit: int = 300) -> dict[str, Any]:
        job = db.get(LinkExtractionJob, job_id)
        if not job:
            return {"logs": [], "next": after}
        try:
            lines = json.loads(job.logs_json or "[]")
        except (TypeError, ValueError):
            lines = []
        filtered = [line for line in lines if int(line.get("seq", 0)) > max(0, int(after or 0))]
        selected = filtered[: max(1, min(500, int(limit or 300)))]
        next_cursor = int(selected[-1].get("seq", after)) if selected else int(after or 0)
        return {"logs": selected, "next": next_cursor}
