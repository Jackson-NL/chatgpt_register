#!/usr/bin/env python3
"""诊断单账号 OAuth 登录卡点：提交 email/password 后 dump 页面文本+截图。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.services.browser_stack import build_launch_options  # noqa: E402
from app.services.registrator import CODE_INPUT_SELECTORS, PASSWORD_INPUT_SELECTORS, find_and_fill, wait_spa_ready  # noqa: E402
import pyotp  # noqa: E402

EMAIL_SELECTORS = ['input[type="email"]', 'input[name="email"]', 'input[autocomplete="username"]']
ACCOUNT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 45
OUT = ROOT / "output" / "oauth-diag" / time.strftime("%Y%m%d-%H%M%S")
OUT.mkdir(parents=True, exist_ok=True)


async def main() -> int:
    con = sqlite3.connect(BACKEND / "data" / "openai_register.db")
    con.row_factory = sqlite3.Row
    acc = con.execute("SELECT * FROM accounts WHERE id=?", (ACCOUNT_ID,)).fetchone()
    con.close()
    if not acc:
        print("account not found")
        return 1

    proxy = (acc["proxy"] or settings.default_proxy).strip()
    launch_options = build_launch_options(proxy, str(acc["profile_path"]), headless=True)
    async with __import__("camoufox.async_api", fromlist=["AsyncCamoufox"]).AsyncCamoufox(**launch_options) as browser:
        context = browser if launch_options.get("persistent_context") else await browser.new_context(locale="en-US")
        page = context.pages[0] if context.pages else await context.new_page()
        page.on("console", lambda m: print(f"[console] {m.type}: {m.text[:120]}"))

        await page.goto("https://auth.openai.com/log-in", wait_until="domcontentloaded", timeout=60000)
        await wait_spa_ready(page, pause_ms=800)

        async def dump(stage: str) -> None:
            body = ""
            try:
                body = await page.locator("body").inner_text(timeout=3000)
            except Exception as e:
                body = f"<inner_text fail: {e}>"
            shot = OUT / f"acc_{ACCOUNT_ID}_{stage}.png"
            try:
                await page.screenshot(path=str(shot), full_page=True)
            except Exception as e:
                shot = f"screenshot fail: {e}"
            print(f"\n===== {stage} url={page.url}")
            print(body[:1500].replace("\n", " | "))
            print(f"screenshot: {shot}")

        # email
        filled = await find_and_fill(page, EMAIL_SELECTORS, str(acc["email"]))
        print(f"email filled={filled}")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(4000)
        await dump("after_email")

        # password
        pw_filled = await find_and_fill(page, PASSWORD_INPUT_SELECTORS, str(acc["password"]))
        print(f"password filled={pw_filled}")
        await page.keyboard.press("Enter")
        for i in range(6):
            await page.wait_for_timeout(5000)
            url = str(page.url)
            has_code = False
            for sel in CODE_INPUT_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() and await loc.is_visible():
                        has_code = True
                        break
                except Exception:
                    continue
            print(f"[{i*5+5}s] url={url} code_input_visible={has_code}")
            if has_code:
                break
        await dump("final")

        # 如果出现验证码输入框，尝试填 TOTP 并观察结果
        for sel in CODE_INPUT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    code = pyotp.TOTP(str(acc["totp_secret"])).now()
                    ok = await find_and_fill(page, [sel], code)
                    print(f"totp filled={ok} (selector={sel})")
                    await page.keyboard.press("Enter")
                    break
            except Exception:
                continue
        await page.wait_for_timeout(8000)
        await dump("after_totp")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
