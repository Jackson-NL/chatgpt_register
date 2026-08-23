#!/usr/bin/env python3
"""批量刷新所有带 refresh_token 的账号，统计过期数量。

用法（repo 根目录）：
  python backend\\scripts\\refresh_rt_all.py [--concurrency 5] [--ids 1,2,3]

行为：
  - 对每个带 RT 的账号 POST https://auth.openai.com/oauth/token (grant_type=refresh_token)
  - 成功：回写新 AT/RT/ID token + oauth_refresh_* 记录字段（RT 轮换后旧值即失效，必须回写）
  - 失败：记录 oauth_refresh_status=failed 与错误摘要，不改账号 status
  - 汇总报告写入 output/rt-refresh/<ts>/summary.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "backend" / "data" / "openai_register.db"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # 与 registrator.py OAUTH_CLIENT_ID 一致
DEFAULT_PROXY = "http://127.0.0.1:7890"


def utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def mask(err: str) -> str:
    return re.sub(r"(refresh_token|access_token|id_token)[=:]\S+", r"\1=<masked>", str(err))[:300]


async def refresh_one(client: httpx.AsyncClient, row: dict) -> dict:
    aid = row["id"]
    rt = row["refresh_token"]
    for attempt in range(3):
        try:
            resp = await client.post(
                TOKEN_URL,
                data={"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": rt},
                timeout=30,
            )
            if resp.status_code == 429:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if resp.status_code == 200:
                data = resp.json()
                return {"id": aid, "email": row["email"], "ok": True,
                        "rotated_rt": bool(data.get("refresh_token")),
                        "new_rt": data.get("refresh_token", ""),
                        "new_at": data.get("access_token", ""),
                        "new_idt": data.get("id_token", "")}
            body = ""
            try:
                body = json.dumps(resp.json())[:300]
            except Exception:
                body = resp.text[:300]
            return {"id": aid, "email": row["email"], "ok": False,
                    "status": resp.status_code, "error": mask(body),
                    "expired": resp.status_code == 400 and "invalid_grant" in body}
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:200]
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            return {"id": aid, "email": row["email"], "ok": False, "status": 0, "error": mask(err), "expired": False}
    return {"id": aid, "email": row["email"], "ok": False, "status": 429, "error": "rate_limited", "expired": False}


def save_result(res: dict) -> None:
    con = sqlite3.connect(DB, timeout=30)
    try:
        now = utcnow_str()
        if res.get("ok"):
            con.execute(
                """UPDATE accounts SET access_token=?, refresh_token=?, id_token=?,
                       oauth_refresh_status='ok', oauth_refresh_error='', oauth_refreshed_at=?, last_check_at=?
                 WHERE id=?""",
                (res["new_at"], res["new_rt"] or "", res["new_idt"] or "", now, now, res["id"]),
            )
        else:
            con.execute(
                """UPDATE accounts SET oauth_refresh_status='failed', oauth_refresh_error=?,
                       oauth_refreshed_at=?, last_check_at=? WHERE id=?""",
                (res.get("error", "")[:400], now, now, res["id"]),
            )
        con.commit()
    finally:
        con.close()


async def amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--ids", default="")
    args = parser.parse_args()

    ids = {int(x) for x in re.split(r"[,\s]+", args.ids.strip()) if x}
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    sql = ("SELECT id, email, refresh_token, proxy FROM accounts "
           "WHERE refresh_token IS NOT NULL AND refresh_token != ''")
    params: list = []
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params = list(ids)
    rows = [dict(r) for r in con.execute(sql + " ORDER BY id", params)]
    con.close()

    run_dir = ROOT / "output" / "rt-refresh" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"targets": len(rows), "concurrency": args.concurrency}, ensure_ascii=False), flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    async def runner(row: dict) -> dict:
        async with sem:
            proxies = row["proxy"].strip() or DEFAULT_PROXY
            async with httpx.AsyncClient(proxy=proxies) as client:
                res = await refresh_one(client, row)
            save_result(res)
            tag = "OK " if res["ok"] else f"FAIL {res.get('status')} {'EXPIRED' if res.get('expired') else ''}"
            print(f"[{tag}] id={res['id']} {res['email'][:28]} {'' if res['ok'] else res['error'][:120]}", flush=True)
            return res

    t0 = time.time()
    results = await asyncio.gather(*(runner(r) for r in rows))
    ok = [r for r in results if r["ok"]]
    expired = [r for r in results if r.get("expired")]
    other_fail = [r for r in results if not r["ok"] and not r.get("expired")]

    summary = {
        "total_with_rt": len(rows),
        "refresh_ok": len(ok),
        "expired_invalid_grant": len(expired),
        "other_failed": len(other_fail),
        "duration_sec": round(time.time() - t0, 1),
        "expired_ids": sorted(r["id"] for r in expired),
        "other_failed_detail": [{"id": r["id"], "status": r.get("status"), "error": r.get("error", "")[:150]} for r in other_fail],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "other_failed_detail"}, ensure_ascii=False, indent=2))
    print(f"report: {run_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
