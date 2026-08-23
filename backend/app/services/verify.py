"""浏览器验货服务：Camoufox 真实浏览器内 fetch /backend-api/me 验证 access_token 存活。

为什么必须用浏览器：curl_cffi / 普通 HTTP 请求会命中 Cloudflare 指纹拦截返回 403，
无法区分「token 失效」和「被拦截」。浏览器内 fetch 走真实指纹 + 代理，结果可信。
同时提供 JWT exp 解析，用于展示 token 剩余有效期。
"""
import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..db import SessionLocal
from ..models import Account, HealthCheck
from ..models import utcnow
from .browser_stack import build_launch_options

CHATGPT_HOME = "https://chatgpt.com/"
DEFAULT_PROXY = "http://127.0.0.1:7890"


def parse_jwt_exp(token: str) -> datetime | None:
    """解析 JWT payload 的 exp（UTC naive datetime）；解析失败返回 None。"""
    if not token or token.count(".") != 2:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not exp:
            return None
        return datetime.fromtimestamp(exp, timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


async def browser_fetch_me(profile_path: str, proxy: str = DEFAULT_PROXY) -> dict:
    """在账号自己的持久化 profile 会话内 fetch /backend-api/me。

    这里故意不注入数据库里的 access_token：注入 token 只能证明 token 可用，
    不能证明 profile 中的浏览器登录态可用。返回值包含 authenticated，调用方
    必须同时检查 HTTP 200 和身份字段，不能只根据页面 URL 判断。
    """
    from camoufox.async_api import AsyncCamoufox

    start = time.time()
    try:
        path = Path(str(profile_path or "")).expanduser()
        if not path.is_dir():
            return {
                "error": f"profile 不存在或不是目录: {path}",
                "duration_ms": int((time.time() - start) * 1000),
            }

        launch_options = build_launch_options(proxy, str(path), headless=True)
        async with AsyncCamoufox(**launch_options) as browser:
            context = browser if launch_options.get("persistent_context") else await browser.new_context(locale="en-US")
            pg = context.pages[0] if context.pages else await context.new_page()
            await pg.goto(CHATGPT_HOME, timeout=45000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(2500)
            result = await pg.evaluate(
                """
                async () => {
                    try {
                        const r = await fetch('/backend-api/me', {
                            credentials: 'include',
                            headers: { 'Accept': 'application/json' },
                        });
                        const text = await r.text();
                        let data = {};
                        try { data = JSON.parse(text); } catch (e) {}
                        const user = data && typeof data.user === 'object' ? data.user : {};
                        const email = data.email || user.email || '';
                        const user_id = user.id || data.user_id || data.id || '';
                        const plan = data.plan_type || data.plan || user.plan_type || '';
                        return {
                            status: r.status,
                            authenticated: r.status === 200 && Boolean(email || user_id),
                            email,
                            user_id,
                            plan,
                            body: text.slice(0, 80),
                        };
                    } catch (e) { return { error: String(e) }; }
                }
                """,
            )
            result["page_url"] = str(getattr(pg, "url", ""))
            result["duration_ms"] = int((time.time() - start) * 1000)
            return result
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200], "duration_ms": int((time.time() - start) * 1000)}


class VerifyService:
    """使用账号 profile 的真实登录态验货并落库。"""

    async def verify_account(self, account_id: int, proxy: str | None = None) -> dict:
        db = SessionLocal()
        try:
            account = db.get(Account, account_id)
            if not account:
                return {"ok": False, "error": "账号不存在"}

            effective_proxy = (proxy or account.proxy or DEFAULT_PROXY).strip()
            if not account.profile_path:
                fetched = {"error": "该账号没有 profile_path，无法验证浏览器登录态", "duration_ms": 0}
            else:
                fetched = await browser_fetch_me(account.profile_path, effective_proxy)

            if fetched.get("error"):
                outcome = {
                    "result": "fail",
                    "detail": f"profile 验活异常: {fetched['error']}",
                    "duration_ms": fetched.get("duration_ms", 0),
                }
            else:
                status = fetched.get("status")
                fetched_email = str(fetched.get("email") or "").strip().lower()
                account_email = str(account.email or "").strip().lower()
                if status == 200 and not fetched.get("authenticated"):
                    outcome = {
                        "result": "fail",
                        "detail": "profile 页面可打开，但 /backend-api/me 未返回已登录身份",
                        "duration_ms": fetched.get("duration_ms", 0),
                    }
                elif status == 200 and fetched_email and account_email and fetched_email != account_email:
                    outcome = {
                        "result": "fail",
                        "detail": f"profile 账号不匹配 · 本地={account.email} · 页面={fetched.get('email')}",
                        "duration_ms": fetched.get("duration_ms", 0),
                    }
                elif status == 200 and fetched.get("authenticated"):
                    outcome = {
                        "result": "pass",
                        "detail": (
                            f"profile session 200 OK · user={fetched.get('user_id', '')} "
                            f"· email={fetched.get('email', '')} · plan={fetched.get('plan', '')}"
                        ),
                        "duration_ms": fetched.get("duration_ms", 0),
                    }
                elif status == 401:
                    outcome = {"result": "fail", "detail": "401 Unauthorized · profile 登录态已失效或未登录", "duration_ms": fetched.get("duration_ms", 0)}
                else:
                    outcome = {"result": "fail", "detail": f"HTTP {status} · {fetched.get('body', '')[:60]}", "duration_ms": fetched.get("duration_ms", 0)}

            db.add(HealthCheck(
                account_id=account_id,
                check_type="browser",
                result=outcome["result"],
                detail=outcome["detail"][:400],
            ))
            account.last_check_at = utcnow()
            account.status = "active" if outcome["result"] == "pass" else "unhealthy"
            db.commit()
            return {"ok": True, **outcome}
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            return {"ok": False, "error": str(exc)[:200]}
        finally:
            db.close()
