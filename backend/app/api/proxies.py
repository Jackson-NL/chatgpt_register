import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Account, Proxy
from ..schemas import ProxyCreate, ProxyOut, ProxyUpdate

router = APIRouter()


@router.get("", response_model=list[ProxyOut])
def list_proxies(status: str | None = None, q: str | None = None, db: Session = Depends(get_db)):
    qs = select(Proxy)
    if status and status != "all":
        qs = qs.where(Proxy.status == status)
    if q:
        qs = qs.where(Proxy.url.like(f"%{q}%"))
    return db.scalars(qs.order_by(Proxy.id.desc())).all()


@router.post("", response_model=ProxyOut)
def create_proxy(payload: ProxyCreate, db: Session = Depends(get_db)):
    exists = db.scalar(select(Proxy).where(Proxy.url == payload.url))
    if exists:
        return exists
    proxy = Proxy(url=payload.url, country=payload.country)
    db.add(proxy)
    db.commit()
    db.refresh(proxy)
    return proxy


@router.patch("/{proxy_id}", response_model=ProxyOut)
def update_proxy(proxy_id: int, payload: ProxyUpdate, db: Session = Depends(get_db)):
    proxy = db.get(Proxy, proxy_id)
    if not proxy:
        raise HTTPException(404, "代理不存在")
    if payload.status is not None:
        proxy.status = payload.status
    if payload.country is not None:
        proxy.country = payload.country
    db.commit()
    db.refresh(proxy)
    return proxy


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, db: Session = Depends(get_db)):
    proxy = db.get(Proxy, proxy_id)
    if not proxy:
        raise HTTPException(404, "代理不存在")
    db.delete(proxy)
    db.commit()
    return {"ok": True}


@router.post("/{proxy_id}/test")
def test_proxy(proxy_id: int, db: Session = Depends(get_db)):
    """真实连通性测试：解析 url 的 host:port，TCP 连接探测。"""
    import socket

    proxy = db.get(Proxy, proxy_id)
    if not proxy:
        raise HTTPException(404, "代理不存在")
    host_port = proxy.url.replace("http://", "").replace("https://", "").replace("socks5://", "")
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
    else:
        host, port = host_port, "80"
    port = int(port)
    start = time.time()
    try:
        sock = socket.create_connection((host, port), timeout=8)
        sock.close()
        latency_ms = int((time.time() - start) * 1000)
        proxy.status = "ok"
        db.commit()
        return {"ok": True, "latency_ms": latency_ms, "status": "online"}
    except Exception as exc:  # noqa: BLE001
        proxy.status = "failed"
        db.commit()
        return {"ok": False, "error": str(exc)[:160], "status": "offline"}
