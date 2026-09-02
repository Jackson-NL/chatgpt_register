"""Re-login local 1xx accounts through the normal ChatGPT web login flow.

This script deliberately does not import or call any OAuth authorization/callback
code. It copies each profile to a private temporary directory, commits the copy
only after a successful web login, and preserves the original profile on failure.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pyotp
from camoufox.async_api import AsyncCamoufox
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Account, utcnow  # noqa: E402
from app.services.browser_stack import build_launch_options  # noqa: E402
from app.services.registrator import (  # noqa: E402
    CODE_INPUT_SELECTORS,
    PASSWORD_INPUT_SELECTORS,
    find_and_fill,
    wait_spa_ready,
)


EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[autocomplete="username"]',
    'input[placeholder*="email" i]',
]

LOGIN_BUTTON_RE = re.compile(r"^(continue|next|log in|login|sign in|signin|verify|submit)$", re.I)
OAUTH_PATH_RE = re.compile(r"/(oauth|authorize|callback)(?:/|$)", re.I)
LOGIN_PATH_RE = re.compile(r"/(auth/login|log-in|login)(?:/|$)", re.I)
PASSWORD_ERROR_RE = re.compile(r"incorrect password|invalid password|wrong password|incorrect email or password|invalid credentials|密码错误|密码无效", re.I)
TERMINAL_ERROR_RE = re.compile(r"deactivat|suspend|deleted|停用|封禁|删除", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_url(value: object) -> str:
    try:
        parsed = urlsplit(str(value or ""))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    except Exception:
        return ""


def safe_text(value: object, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)(password|passwd|totp|otp|access_token|refresh_token|id_token)\s*[:=]\s*[^,\s}]+", r"\1=[hidden]", text)
    return text[:limit]


def temp_root() -> Path:
    root = Path(settings.profiles_dir).expanduser().resolve() / "web_relogin_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def copy_profile(source: str, account_id: int) -> Path:
    source_path = Path(source).expanduser()
    target = temp_root() / f"account_{account_id}_{uuid.uuid4().hex}"
    if source_path.is_dir():
        shutil.copytree(source_path, target, symlinks=True)
    elif source_path.exists():
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target / source_path.name)
    else:
        raise FileNotFoundError(f"profile 不存在: {source_path}")

    # A copied Firefox/Camoufox lock must not make the temporary browser attach
    # to a stale process. These files are runtime locks, not login state.
    for name in ("parent.lock", ".parentlock", "lock", "SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = target / name
        if path.exists() or path.is_symlink():
            try:
                path.unlink()
            except OSError:
                pass
    return target


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def commit_profile(copy_path: Path, original: str) -> None:
    target = Path(original).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.parent / f".{target.name}.web_relogin_backup_{uuid.uuid4().hex}"
    if target.exists():
        shutil.move(str(target), str(backup))
    try:
        shutil.move(str(copy_path), str(target))
    except Exception:
        if backup.exists() and not target.exists():
            shutil.move(str(backup), str(target))
        raise
    else:
        remove_tree(backup)


async def visible_text(page) -> str:
    try:
        return safe_text(await page.locator("body").inner_text(timeout=1500), 1800)
    except Exception:
        return ""


async def click_login_button(page) -> bool:
    """Click only a plain login action; never click Google/Apple/phone actions."""
    buttons = page.locator("button")
    try:
        count = await buttons.count()
    except Exception:
        return False
    for index in range(min(count, 40)):
        button = buttons.nth(index)
        try:
            if not await button.is_visible() or await button.is_disabled():
                continue
            label = re.sub(r"\s+", " ", (await button.inner_text()).strip())
            if LOGIN_BUTTON_RE.fullmatch(label):
                await button.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


async def fill_totp(page, code: str) -> bool:
    try:
        result = await page.evaluate(
            r"""
            code => {
              const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
              const inputs = Array.from(document.querySelectorAll('input')).filter(el => {
                const hint = [el.name, el.id, el.placeholder, el.ariaLabel, el.autocomplete].join(' ');
                return visible(el) && (/code|otp|mfa|authenticator|verification/i.test(hint)
                  || el.inputMode === 'numeric' || el.maxLength === 1);
              });
              if (!inputs.length) return false;
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              const targets = inputs.length > 1 ? inputs.slice(0, code.length) : [inputs[0]];
              targets.forEach((el, index) => {
                const value = inputs.length > 1 ? code[index] : code;
                setter.call(el, value);
                el.dispatchEvent(new InputEvent('input', {bubbles: true, data: value, inputType: 'insertText'}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
              });
              return true;
            }
            """,
            code,
        )
        if result:
            return True
    except Exception:
        pass
    return await find_and_fill(page, CODE_INPUT_SELECTORS, code)


async def has_visible(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                return True
        except Exception:
            continue
    return False


async def page_snapshot(page) -> dict[str, str]:
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    return {
        "url": safe_url(getattr(page, "url", "")),
        "title": safe_text(title, 200),
        "body": await visible_text(page),
    }


def is_forbidden_oauth(snapshot: dict[str, str]) -> bool:
    return bool(OAUTH_PATH_RE.search(snapshot.get("url", "")))


def is_challenge(snapshot: dict[str, str]) -> bool:
    signal = f"{snapshot.get('title', '')} {snapshot.get('body', '')}"
    return "just a moment" in signal.lower() or "cloudflare" in signal.lower()


def is_chatgpt_app_page(snapshot: dict[str, str]) -> bool:
    url = snapshot.get("url", "")
    if not url or "chatgpt.com" not in url:
        return False
    if LOGIN_PATH_RE.search(url) or "auth.openai.com" in url:
        return False
    if is_forbidden_oauth(snapshot) or is_challenge(snapshot):
        return False
    return True


def is_logged_in(snapshot: dict[str, str], session: dict | None = None) -> bool:
    """只有页面结构正常且同一 profile 的 /me 返回已认证身份才算登录。"""
    if not is_chatgpt_app_page(snapshot):
        return False
    if session and session.get("authenticated"):
        return True
    body = snapshot.get("body", "")
    if re.search(r"\b(log in|login|sign in|continue with google)\b", body, re.I):
        return False
    return False


async def probe_authenticated_session(page) -> dict:
    """通过当前 profile 的 cookies 验证 ChatGPT 会话，不注入数据库 token。"""
    try:
        return await page.evaluate(
            """
            async () => {
                try {
                    const response = await fetch('/backend-api/me', {
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' },
                    });
                    const text = await response.text();
                    let data = {};
                    try { data = JSON.parse(text); } catch (e) {}
                    const user = data && typeof data.user === 'object' ? data.user : {};
                    const email = data.email || user.email || '';
                    const user_id = user.id || data.user_id || data.id || '';
                    return {
                        status: response.status,
                        authenticated: response.status === 200 && Boolean(email || user_id),
                        email,
                        user_id,
                        body: text.slice(0, 240),
                    };
                } catch (e) {
                    return { status: 0, authenticated: false, error: String(e) };
                }
            }
            """
        )
    except Exception as error:  # noqa: BLE001
        return {"status": 0, "authenticated": False, "error": str(error)[:300]}


async def extract_session_access_token(page, captured: str = "") -> dict:
    """Return a ChatGPT web access_token without using OAuth/add-phone flows."""
    token = str(captured or "").strip()
    session: dict = {}
    try:
        session = await page.evaluate(
            """
            async () => {
                try {
                    const response = await fetch('/api/auth/session', {
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' },
                    });
                    const text = await response.text();
                    let data = {};
                    try { data = JSON.parse(text); } catch (e) {}
                    return { status: response.status, data, body: text.slice(0, 240) };
                } catch (e) {
                    return { status: 0, data: {}, error: String(e) };
                }
            }
            """
        )
    except Exception as error:  # noqa: BLE001
        session = {"status": 0, "data": {}, "error": str(error)[:240]}
    data = session.get("data") if isinstance(session, dict) else {}
    if isinstance(data, dict):
        for key in ("accessToken", "access_token", "token"):
            value = data.get(key)
            if isinstance(value, str) and value.count(".") == 2:
                token = token or value.strip()
                break
    return {"access_token": token, "session": session}


def jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def write_web_access_token(account_id: int, access_token: str, session_email: str = "") -> dict:
    """Persist only the ChatGPT web AT/profile metadata; never writes RT/id_token."""
    token = str(access_token or "").strip()
    if not token:
        raise RuntimeError("未提取到新的 access_token")
    claims = jwt_claims(token)
    auth_claims = claims.get("https://api.openai.com/auth", {}) if isinstance(claims, dict) else {}
    profile_claims = claims.get("https://api.openai.com/profile", {}) if isinstance(claims, dict) else {}
    db = SessionLocal()
    try:
        account = db.get(Account, account_id)
        if not account:
            raise RuntimeError(f"账号不存在: {account_id}")
        account.access_token = token
        account.account_id = (
            auth_claims.get("chatgpt_account_id")
            or profile_claims.get("chatgpt_account_id")
            or account.account_id
        )
        account.plan_type = auth_claims.get("chatgpt_plan_type") or account.plan_type or "free"
        if session_email and str(session_email).strip().lower() == str(account.email or "").strip().lower():
            account.email = session_email
        account.profile_last_used_at = utcnow()
        if not account.profile_source or account.profile_source == "unknown":
            account.profile_source = "web_relogin"
        db.commit()
        return {
            "access_token_saved": True,
            "account_id_saved": bool(account.account_id),
            "plan_type": account.plan_type or "",
        }
    finally:
        db.close()



async def wait_for_stage(page, deadline: float) -> tuple[str, dict[str, str]]:
    while time.monotonic() < deadline:
        snapshot = await page_snapshot(page)
        if is_forbidden_oauth(snapshot):
            return "oauth_forbidden", snapshot
        if is_challenge(snapshot):
            return "cloudflare_challenge", snapshot
        if is_chatgpt_app_page(snapshot):
            session = await probe_authenticated_session(page)
            if is_logged_in(snapshot, session):
                snapshot["session_status"] = str(session.get("status", ""))
                snapshot["session_email"] = safe_text(session.get("email", ""), 200)
                snapshot["session_user_id"] = safe_text(session.get("user_id", ""), 200)
                return "logged_in", snapshot
        if await has_visible(page, PASSWORD_INPUT_SELECTORS):
            return "password", snapshot
        body_text = snapshot.get("body", "")
        # Login may show an email inbox verification code first, with a
        # "Continue with password" fallback. That code is not TOTP; do not
        # fill the authenticator secret there. Prefer password flow.
        if re.search(r"continue with password", body_text, re.I):
            return "password_choice", snapshot
        if await has_visible(page, CODE_INPUT_SELECTORS):
            if re.search(r"check your inbox|verification code we just sent|resend email", body_text, re.I):
                return "email_code_blocked", snapshot
            return "totp", snapshot
        if await has_visible(page, EMAIL_SELECTORS):
            return "email", snapshot
        signal = f"{snapshot['title']} {snapshot['body']}"
        if TERMINAL_ERROR_RE.search(signal):
            return "account_disabled", snapshot
        await page.wait_for_timeout(500)
    return "timeout", await page_snapshot(page)


async def login_account(account: Account, headless: bool, timeout_s: int, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        result = {
            "account_id": account.id,
            "email": str(account.email or ""),
            "profile": str(account.profile_path or ""),
            "status": "failed",
            "stage": "prepare",
            "error": "",
            "url": "",
            "started_at": now_iso(),
            "finished_at": "",
        }
        temp_profile: Path | None = None
        browser = None
        access_token = ""
        seen_auth_requests: list[str] = []
        try:
            if not account.email or not account.password or not account.totp_secret:
                raise RuntimeError("本地账号缺少邮箱、密码或 TOTP")
            if not account.profile_path:
                raise RuntimeError("本地账号缺少 profile_path")
            temp_profile = copy_profile(account.profile_path, account.id)
            launch_options = build_launch_options(
                str(account.proxy or settings.default_proxy or ""),
                str(temp_profile),
                headless=headless,
            )
            result["stage"] = "browser"
            async with AsyncCamoufox(**launch_options) as browser:
                context = browser if launch_options.get("persistent_context") else await browser.new_context(locale="en-US")
                page = context.pages[0] if context.pages else await context.new_page()

                def on_request(request):
                    nonlocal access_token, seen_auth_requests
                    try:
                        url = request.url or ""
                        auth = request.headers.get("authorization", "")
                    except Exception:
                        return
                    if "openai.com" in url or "chatgpt.com" in url:
                        if auth.startswith("Bearer "):
                            seen_auth_requests.append(safe_url(url))
                            if not access_token:
                                access_token = auth[len("Bearer "):].strip()
                        elif len(seen_auth_requests) < 80:
                            seen_auth_requests.append(f"{safe_url(url)} (no-auth)")
                        if len(seen_auth_requests) > 80:
                            seen_auth_requests.pop(0)

                page.on("request", on_request)
                result["stage"] = "open_login"
                await page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
                await wait_spa_ready(page, pause_ms=800)

                deadline = time.monotonic() + max(30, int(timeout_s))
                submitted_email = False
                submitted_password = False
                submitted_totp = False
                while time.monotonic() < deadline:
                    stage, snapshot = await wait_for_stage(page, min(deadline, time.monotonic() + 2.5))
                    result["url"] = snapshot["url"]
                    if stage == "logged_in":
                        session_email = str(snapshot.get("session_email") or "").strip().lower()
                        local_email = str(account.email or "").strip().lower()
                        if session_email and local_email and session_email != local_email:
                            raise RuntimeError(
                                f"profile 账号不匹配: local={account.email} session={snapshot.get('session_email')}"
                            )
                        result["profile_session_status"] = int(snapshot.get("session_status") or 0)
                        result["profile_session_authenticated"] = True
                        result["profile_session_email"] = snapshot.get("session_email", "")
                        token_info = await extract_session_access_token(page, access_token)
                        access_token = str(token_info.get("access_token") or "").strip()
                        if not access_token:
                            token_deadline = time.monotonic() + 8.0
                            while not access_token and time.monotonic() < token_deadline:
                                await page.wait_for_timeout(400)
                                token_info = await extract_session_access_token(page, access_token)
                                access_token = str(token_info.get("access_token") or "").strip()
                        if not access_token:
                            diag = ", ".join(seen_auth_requests[-20:]) if seen_auth_requests else "无"
                            raise RuntimeError(f"已登录但未提取到新的 access_token；观察到请求: {diag}")
                        saved = write_web_access_token(account.id, access_token, str(snapshot.get("session_email") or ""))
                        result.update(saved)
                        result["access_token_captured"] = True
                        result["status"] = "success"
                        result["stage"] = "verified_home"
                        break
                    if stage in {"oauth_forbidden", "cloudflare_challenge", "account_disabled"}:
                        raise RuntimeError(f"{stage}: {snapshot['title']} {snapshot['body']}")
                    if stage == "timeout":
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f"timeout: {snapshot['title']} {snapshot['body']}")
                        await page.wait_for_timeout(400)
                        continue
                    if stage == "email" and not submitted_email:
                        result["stage"] = "email"
                        if not await find_and_fill(page, EMAIL_SELECTORS, str(account.email)):
                            raise RuntimeError("未找到或无法填写邮箱输入框")
                        await page.wait_for_timeout(250)
                        if not await click_login_button(page):
                            raise RuntimeError("未找到安全的邮箱提交按钮")
                        submitted_email = True
                        await page.wait_for_timeout(800)
                        continue
                    if stage == "password_choice":
                        result["stage"] = "password_choice"
                        clicked = False
                        for locator in (page.get_by_text("Continue with password", exact=True), page.locator('text=Continue with password').first):
                            try:
                                if await locator.count() and await locator.is_visible():
                                    await locator.click(timeout=5000)
                                    clicked = True
                                    break
                            except Exception:
                                continue
                        if not clicked:
                            raise RuntimeError("邮箱验证码页存在但无法点击 Continue with password")
                        await page.wait_for_timeout(900)
                        continue
                    if stage == "email_code_blocked":
                        raise RuntimeError("登录需要邮箱验证码，当前流程没有邮箱收码能力")
                    if stage == "password" and not submitted_password:
                        result["stage"] = "password"
                        if not await find_and_fill(page, PASSWORD_INPUT_SELECTORS, str(account.password)):
                            raise RuntimeError("未找到或无法填写密码输入框")
                        await page.wait_for_timeout(250)
                        if not await click_login_button(page):
                            raise RuntimeError("未找到安全的密码提交按钮")
                        submitted_password = True
                        await page.wait_for_timeout(800)
                        continue
                    if stage == "totp" and not submitted_totp:
                        result["stage"] = "totp"
                        try:
                            code = pyotp.TOTP(str(account.totp_secret)).now()
                        except Exception as error:
                            raise RuntimeError("TOTP secret 无效") from error
                        if not await fill_totp(page, code):
                            raise RuntimeError("未找到或无法填写 TOTP 输入框")
                        await page.wait_for_timeout(250)
                        if not await click_login_button(page):
                            raise RuntimeError("未找到安全的 TOTP 提交按钮")
                        submitted_totp = True
                        await page.wait_for_timeout(1000)
                        continue
                    snapshot = await page_snapshot(page)
                    signal = f"{snapshot['title']} {snapshot['body']}"
                    if PASSWORD_ERROR_RE.search(signal) and submitted_password and not submitted_totp:
                        raise RuntimeError(f"登录页报错: {signal}")
                    await page.wait_for_timeout(500)

                if result["status"] != "success":
                    snapshot = await page_snapshot(page)
                    raise RuntimeError(f"未确认登录成功: {snapshot['title']} {snapshot['body']}")

            # The persistent context has flushed cookies after the browser exits.
            result["stage"] = "commit"
            commit_profile(temp_profile, account.profile_path)
            temp_profile = None
        except Exception as error:  # noqa: BLE001
            result["error"] = safe_text(error, 1600)
            if browser is not None:
                try:
                    result["url"] = safe_url(getattr(browser, "url", "")) or result["url"]
                except Exception:
                    pass
        finally:
            if temp_profile is not None:
                remove_tree(temp_profile)
            result["finished_at"] = now_iso()
            print(json.dumps({k: v for k, v in result.items() if k not in {"profile"}}, ensure_ascii=False), flush=True)
        return result


def load_accounts(min_id: int, max_id: int, only_missing_refresh: bool = False, account_ids: list[int] | None = None) -> list[Account]:
    db = SessionLocal()
    try:
        statement = select(Account)
        if account_ids:
            statement = statement.where(Account.id.in_(account_ids))
        else:
            statement = statement.where(Account.id >= min_id, Account.id < max_id)
        accounts = list(db.scalars(statement.order_by(Account.id)).all())
        if only_missing_refresh:
            accounts = [
                account for account in accounts
                if str(account.access_token or '').strip() and not str(account.refresh_token or '').strip()
            ]
        return accounts
    finally:
        db.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-id", type=int, default=100)
    parser.add_argument("--max-id", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--only-missing-refresh",
        action="store_true",
        help="只处理 access_token 有值且 refresh_token 为空的账号",
    )
    parser.add_argument(
        "--account-ids",
        default="",
        help="逗号分隔的精确账号 ID；与 --only-missing-refresh 配合使用",
    )
    parser.add_argument("--result", default="")
    args = parser.parse_args()
    if args.concurrency != 3:
        raise SystemExit("本任务要求并发数固定为 3")

    account_ids = [int(value.strip()) for value in args.account_ids.split(",") if value.strip()] if args.account_ids else None
    accounts = load_accounts(
        args.min_id,
        args.max_id,
        only_missing_refresh=args.only_missing_refresh,
        account_ids=account_ids,
    )
    if not accounts:
        raise SystemExit("没有匹配到目标账号")
    semaphore = asyncio.Semaphore(3)
    results = await asyncio.gather(
        *(login_account(account, not args.headed, args.timeout, semaphore) for account in accounts)
    )
    output = Path(args.result) if args.result else ROOT / "output" / f"relogin_1xx_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "web_login_only",
        "login_url": "https://chatgpt.com/auth/login",
        "oauth_used": False,
        "concurrency": 3,
        "account_range": [args.min_id, args.max_id],
        "only_missing_refresh": args.only_missing_refresh,
        "account_ids": [account.id for account in accounts],
        "count": len(results),
        "success": sum(1 for item in results if item["status"] == "success"),
        "failed": sum(1 for item in results if item["status"] != "success"),
        "items": results,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_file": str(output), "count": payload["count"], "success": payload["success"], "failed": payload["failed"]}, ensure_ascii=False))
    return 0 if payload["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
