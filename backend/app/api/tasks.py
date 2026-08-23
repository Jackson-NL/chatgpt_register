from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SmsActivation, Task
from ..schemas import TaskCreate, TaskOut

router = APIRouter()


@router.post("", response_model=list[TaskOut])
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    tasks = []
    for _ in range(max(1, payload.count)):
        task = Task(
            country=payload.country,
            concurrency_group=payload.concurrency_group,
        )
        db.add(task)
        tasks.append(task)
    db.commit()
    for t in tasks:
        db.refresh(t)
    return tasks


@router.get("", response_model=list[TaskOut])
def list_tasks(limit: int = 100, db: Session = Depends(get_db)):
    return db.scalars(select(Task).order_by(Task.id.desc()).limit(limit)).all()


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/{task_id}/activations")
def list_activations(task_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(SmsActivation).where(SmsActivation.task_id == task_id)).all()


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status in ("pending", "running", "otp_waiting"):
        task.status = "cancelled"
        db.commit()
    return {"ok": True}
