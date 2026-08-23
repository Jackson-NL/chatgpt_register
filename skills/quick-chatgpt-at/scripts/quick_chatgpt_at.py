#!/usr/bin/env python3
"""Quick ChatGPT web access_token extraction for this repo.

Run from repo root, for example:
  E:\\python\\python3.13.3\\python.exe skills\\quick-chatgpt-at\\scripts\\quick_chatgpt_at.py --ids 33-62 --concurrency 5

The script never prints full tokens. It writes captured AT values to backend/data/openai_register.db.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


def find_repo_root() -> Path:
    p = Path.cwd().resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "backend" / "app").is_dir() and (candidate / "backend" / "data" / "openai_register.db").exists():
            return candidate
    raise SystemExit("Run from openai-register repo root or a descendant; backend/data/openai_register.db was not found.")


ROOT = find_repo_root()
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.services.registrator import (  # noqa: E402
    AsyncCamoufox,
    CODE_INPUT_SELECTORS,
    PASSWORD_INPUT_SELECTORS,
    SUBMIT_BUTTON_TEXT,
    build_launch_options,
    find_and_click,
    find_and_fill,
)
import pyotp  # noqa: E402

DB = BACKEND / "data" / "openai_register.db"
DEFAULT_MARKERS = ("imported-2fa-email-20260818", "imported-single-20260818")


async def evaluate_with_retry(page: Any, expression: str, arg: Any = None, *, has_arg: bool = False, attempts: int = 5) -> Any:
    """Retry transient Firefox execution-context invalidation during SPA navigation."""
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            if has_arg:
                return await page.evaluate(expression, arg)
            return await page.evaluate(expression)
        except Exception as error:  # noqa: BLE001
            last_error = error
            message = str(error).lower()
            transient = (
                "no longer usable" in message
                or "execution context was destroyed" in message
                or "navigation" in message
            )
            if not transient or attempt + 1 >= attempts:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    raise last_error or RuntimeError("page.evaluate failed")


def ts() -> str:
    return time.strftime("%H:%M:%S")


def parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[\s,]+", raw.strip()):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(dict.fromkeys(out))


def safe_url(url: str, n: int = 240) -> str:
    redacted = re.sub(r"(code|state|id_token|access_token|refresh_token|token)=[^&]+", r"\1=<redacted>", str(url or ""))
    return redacted[:n]


def is_jwtish(token: str) -> bool:
    return isinstance(token, str) and token.startswith("eyJ") and token.count(".") >= 2 and len(token) > 100


def jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return {}


def identity_from_token(token: str) -> dict[str, Any]:
    claims = jwt_payload(token)
    profile = claims.get("https://api.openai.com/profile") or {}
    auth = claims.get("https://api.openai.com/auth") or {}
    return {
        "email": (profile.get("email") or claims.get("email") or "").lower(),
        "user_id": profile.get("user_id") or claims.get("sub") or "",
        "account_id": auth.get("chatgpt_account_id") or auth.get("account_id") or "",
        "plan_type": auth.get("chatgpt_plan_type") or "free",
        "exp": claims.get("exp") or 0,
    }


def load_targets(ids: list[int], markers: list[str], include_existing: bool) -> list[dict[str, Any]]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    params: list[Any] = []
    clauses = ["ifnull(profile_path,'')<>''"]
    if not include_existing:
        clauses.append("ifnull(access_token,'')=''")
    if ids:
        clauses.append("id in (%s)" % ",".join("?" for _ in ids))
        params.extend(ids)
    elif markers:
        clauses.append("(" + " or ".join("note like ?" for _ in markers) + ")")
        params.extend([f"%{m}%" for m in markers])
    rows = [dict(r) for r in con.execute(
        f"""
        select id,email,password,totp_secret,proxy,profile_path,length(access_token) as at_len
        from accounts
        where {' and '.join(clauses)}
        order by id
        """,
        params,
    )]
    con.close()
    return rows


def save_token(account_id: int, token: str, ident: dict[str, Any]) -> None:
    con = sqlite3.connect(DB, timeout=30)
    cur = con.cursor()
    cur.execute(
        """
        update accounts
        set access_token=?,
            user_id=coalesce(nullif(?, ''), user_id),
            account_id=coalesce(nullif(?, ''), account_id),
            plan_type=coalesce(nullif(?, ''), plan_type)
        where id=?
        """,
        (token, ident.get("user_id") or "", ident.get("account_id") or "", ident.get("plan_type") or "", account_id),
    )
    con.commit()
    con.close()


async def fill_totp_if_present(page: Any, secret: str) -> bool:
    if not secret:
        return False
    code = pyotp.TOTP(secret).now()
    filled = await find_and_fill(page, CODE_INPUT_SELECTORS, code)
    if not filled:
        try:
            filled = bool(await evaluate_with_retry(page, r"""
            code => {
              const inputs = Array.from(document.querySelectorAll('input')).filter(el =>
                (el.offsetWidth || el.offsetHeight || el.getClientRects().length) &&
                (/code|otp|mfa|authenticator|verification/i.test([el.name, el.id, el.placeholder, el.ariaLabel, el.autocomplete].join(' ')) ||
                 el.inputMode === 'numeric' || el.maxLength === 1)
              );
              if (!inputs.length) return false;
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              if (inputs.length === 1) {
                inputs[0].focus();
                setter.call(inputs[0], code);
                inputs[0].dispatchEvent(new InputEvent('input', { bubbles: true, data: code, inputType: 'insertText' }));
                inputs[0].dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              inputs.slice(0, code.length).forEach((el, i) => {
                el.focus();
                setter.call(el, code[i]);
                el.dispatchEvent(new InputEvent('input', { bubbles: true, data: code[i], inputType: 'insertText' }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
              });
              return true;
            }
            """, code, has_arg=True))
        except Exception:
            filled = False
    if filled:
        await page.wait_for_timeout(250)
        if not await find_and_click(page, SUBMIT_BUTTON_TEXT + ["Verify", "Next", "Continue", "验证", "下一步", "继续"]):
            try:
                await page.keyboard.press("Enter")
            except Exception:
                pass
    return filled


async def click_chatgpt_login(page: Any) -> bool:
    return bool(await evaluate_with_retry(page, r"""
    () => {
      const controls = Array.from(document.querySelectorAll('button,[role=button]')).filter(e =>
        (e.offsetWidth || e.offsetHeight || e.getClientRects().length)
      );
      const close = controls.find(b => {
        const r = b.getBoundingClientRect();
        const text = (b.innerText || b.textContent || '').trim();
        return r.left > window.innerWidth * 0.65 && r.top < 180 && (text === '' || text === '×' || text === '✕');
      });
      if (close) close.click();
      const els = Array.from(document.querySelectorAll('button,a')).filter(e =>
        (e.offsetWidth || e.offsetHeight || e.getClientRects().length)
      );
      const login = els.find(e => /^(Log in|登录)$/i.test((e.innerText || e.textContent || '').trim()))
        || els.find(e => /(Log in|登录)/i.test((e.innerText || e.textContent || '').trim()));
      if (login) { login.click(); return true; }
      return false;
    }
    """))


async def fill_modal_email_and_continue(page: Any, email: str) -> bool:
    ok = bool(await evaluate_with_retry(page, r"""
    email => {
      const inputs = Array.from(document.querySelectorAll('input')).filter(e =>
        (e.offsetWidth || e.offsetHeight || e.getClientRects().length)
      );
      const input = inputs.find(e => /email/i.test([e.placeholder, e.name, e.type, e.autocomplete].join(' '))) || inputs[inputs.length - 1];
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      input.focus();
      setter.call(input, email);
      input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    """, email, has_arg=True))
    if not ok:
        return False
    await page.wait_for_timeout(250)
    clicked = bool(await evaluate_with_retry(page, r"""
    () => {
      const buttons = Array.from(document.querySelectorAll('button')).filter(e =>
        (e.offsetWidth || e.offsetHeight || e.getClientRects().length) && /^(Continue|继续)$/i.test((e.innerText || e.textContent || '').trim())
      );
      const button = buttons[buttons.length - 1];
      if (button) { button.click(); return true; }
      return false;
    }
    """))
    if not clicked:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
    return True


async def scan_session_for_token(page: Any, consider) -> None:
    if "chatgpt.com" not in str(getattr(page, "url", "")):
        return
    try:
        result = await evaluate_with_retry(page, """
        async () => {
          try {
            const r = await fetch('/api/auth/session', { credentials: 'include' });
            return { status: r.status, text: await r.text() };
          } catch (e) { return { error: String(e) }; }
        }
        """)
        for match in re.finditer(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", result.get("text", "") or ""):
            consider(match.group(0), "api_auth_session")
    except Exception:
        pass


async def extract_one(row: dict[str, Any], args: argparse.Namespace, run_dir: Path) -> dict[str, Any]:
    aid = row["id"]
    logs: list[str] = []
    found = {"token": "", "source": ""}
    auth_request_count = 0

    def log(msg: str) -> None:
        line = f"{ts()} acc_{aid} {msg}"
        logs.append(line)
        print(line, flush=True)

    def consider(token: str, source: str) -> None:
        nonlocal auth_request_count
        if not token:
            return
        if token.startswith("Bearer "):
            auth_request_count += 1
            token = token[len("Bearer "):]
        if not found["token"] and is_jwtish(token):
            found["token"] = token
            found["source"] = source

    if not row.get("password") or not row.get("totp_secret"):
        return {"id": aid, "ok": False, "error": "missing password/totp"}

    proxy = row.get("proxy") or settings.default_proxy or ""
    launch_options = build_launch_options(proxy, row["profile_path"], headless=not args.headful)
    try:
        async with AsyncCamoufox(**launch_options) as browser:
            context = browser if launch_options.get("persistent_context") else await browser.new_context(locale="en-US")
            page = context.pages[0] if context.pages else await context.new_page()

            def on_request(request: Any) -> None:
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer ") and "chatgpt.com" in request.url and "backend-anon" not in request.url:
                    consider(auth, "request_authorization")

            page.on("request", on_request)
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=90_000)
            await page.wait_for_timeout(3500)
            await scan_session_for_token(page, consider)

            if not found["token"]:
                body = await evaluate_with_retry(page, "document.body.innerText.slice(0,1200)")
                if re.search(r"\bLog in\b|登录|Log in or sign up", body, re.I):
                    await click_chatgpt_login(page)
                    await page.wait_for_timeout(1000)
                    await fill_modal_email_and_continue(page, row["email"])
                    log("submitted email")
                    await page.wait_for_timeout(4500)

            deadline = time.time() + args.timeout
            password_submitted = False
            totp_submitted = False
            last_action = 0.0
            while time.time() < deadline and not found["token"]:
                url = str(getattr(page, "url", ""))
                await scan_session_for_token(page, consider)
                if found["token"]:
                    break
                if "add-phone" in url:
                    log(f"hit add-phone url={safe_url(url)}")
                    break
                if "chatgpt.com" in url:
                    await page.wait_for_timeout(2500)
                    body = await evaluate_with_retry(page, "document.body.innerText.slice(0,1200)")
                    if re.search(r"\bLog in\b|登录|Log in or sign up", body, re.I) and time.time() - last_action > 5:
                        await click_chatgpt_login(page)
                        await page.wait_for_timeout(800)
                        await fill_modal_email_and_continue(page, row["email"])
                        log("submitted email from chatgpt")
                        last_action = time.time()
                    continue

                if not password_submitted:
                    pw_filled = await find_and_fill(page, PASSWORD_INPUT_SELECTORS + ['input[type="password"]', 'input[name="password"]'], row["password"])
                    if pw_filled:
                        await page.wait_for_timeout(250)
                        if not await find_and_click(page, SUBMIT_BUTTON_TEXT + ["Continue", "Log in", "Next", "登录", "继续", "下一步"]):
                            try:
                                await page.keyboard.press("Enter")
                            except Exception:
                                pass
                        password_submitted = True
                        log("submitted password")
                        await page.wait_for_timeout(5000)
                        continue

                if not totp_submitted:
                    did_totp = await fill_totp_if_present(page, row["totp_secret"])
                    if did_totp:
                        totp_submitted = True
                        log("submitted totp")
                        await page.wait_for_timeout(6000)
                        continue

                if time.time() - last_action > 4:
                    clicked = await evaluate_with_retry(page, r"""
                    () => {
                      const bad = /google|apple|phone|sign up|注册/i;
                      const good = /^(continue|log in|next|verify|继续|登录|下一步|验证)$/i;
                      const els = Array.from(document.querySelectorAll('button,a')).filter(e =>
                        (e.offsetWidth || e.offsetHeight || e.getClientRects().length)
                      );
                      const e = els.find(e => good.test((e.innerText || e.textContent || '').trim()) && !bad.test((e.innerText || e.textContent || '').trim()));
                      if (e) { e.click(); return (e.innerText || e.textContent || '').trim(); }
                      return '';
                    }
                    """)
                    if clicked:
                        log(f"clicked action={clicked[:40]}")
                        last_action = time.time()
                await page.wait_for_timeout(1500)

            if found["token"]:
                ident = identity_from_token(found["token"])
                save_token(aid, found["token"], ident)
                log(f"saved access_token source={found['source']} auth_requests={auth_request_count} jwt_email={ident.get('email') or 'unknown'} exp={ident.get('exp') or 0}")
                return {"id": aid, "ok": True, "source": found["source"], "auth_requests": auth_request_count, "jwt_email": ident.get("email"), "exp": ident.get("exp")}

            shot = run_dir / f"acc_{aid}.png"
            try:
                await page.screenshot(path=str(shot), full_page=True)
            except Exception:
                pass
            final_body = ""
            try:
                final_body = (await evaluate_with_retry(page, "document.body.innerText.slice(0,500)")).replace("\n", " | ")
            except Exception:
                pass
            log(f"no token url={safe_url(getattr(page, 'url', ''))} body={final_body[:220]} screenshot={shot}")
            return {"id": aid, "ok": False, "error": "no token", "url": safe_url(getattr(page, "url", "")), "screenshot": str(shot)}
    except Exception as exc:
        log(f"ERROR {str(exc)[:240]}")
        return {"id": aid, "ok": False, "error": str(exc)[:300]}
    finally:
        (run_dir / f"acc_{aid}.log").write_text("\n".join(logs), encoding="utf-8")


def db_summary(ids: list[int], markers: list[str]) -> dict[str, Any]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    params: list[Any] = []
    clauses = []
    if ids:
        clauses.append("id in (%s)" % ",".join("?" for _ in ids))
        params.extend(ids)
    elif markers:
        clauses.append("(" + " or ".join("note like ?" for _ in markers) + ")")
        params.extend([f"%{m}%" for m in markers])
    where = " where " + " and ".join(clauses) if clauses else ""
    rows = con.execute(f"select id,length(access_token) at_len,profile_path from accounts{where} order by id", params).fetchall()
    con.close()
    return {
        "total": len(rows),
        "with_profile": sum(1 for r in rows if r["profile_path"]),
        "with_access_token": sum(1 for r in rows if r["at_len"]),
        "missing_access_token_ids": [r["id"] for r in rows if not r["at_len"]],
    }


async def amain(args: argparse.Namespace) -> int:
    ids = parse_ids(args.ids) if args.ids else []
    markers = args.markers or list(DEFAULT_MARKERS)
    run_dir = (ROOT / "output" / "quick-chatgpt-at" / time.strftime("%Y%m%d-%H%M%S")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    targets = load_targets(ids, markers, args.include_existing)
    print(json.dumps({"targets": [t["id"] for t in targets], "count": len(targets), "concurrency": args.concurrency, "run_dir": str(run_dir)}, ensure_ascii=False), flush=True)
    sem = asyncio.Semaphore(args.concurrency)

    async def runner(row: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            result = await extract_one(row, args, run_dir)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return result

    results = await asyncio.gather(*(runner(row) for row in targets)) if targets else []
    summary = {
        "attempted": len(targets),
        "saved": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        **db_summary(ids, markers),
    }
    payload = {"results": results, "summary": summary}
    (run_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": summary, "summary_path": str(run_dir / "summary.json")}, ensure_ascii=False, indent=2), flush=True)
    return 0 if not summary["missing_access_token_ids"] else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reuse project Camoufox profiles to capture ChatGPT web access_token and write accounts.access_token.")
    p.add_argument("--ids", default="", help="Account ids/ranges, e.g. 33-62,70,72. If omitted, select by --markers.")
    p.add_argument("--markers", nargs="*", default=list(DEFAULT_MARKERS), help="Account note markers used when --ids is omitted.")
    p.add_argument("--concurrency", type=int, default=5, help="Parallel browsers. Use 5 for batch, 1 for retrying flaky IDs.")
    p.add_argument("--timeout", type=float, default=110, help="Per-account login/capture timeout in seconds after opening/login.")
    p.add_argument("--headful", action="store_true", help="Run headed browser for visual debugging.")
    p.add_argument("--include-existing", action="store_true", help="Also rerun accounts that already have access_token.")
    return p


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain(build_parser().parse_args())))
