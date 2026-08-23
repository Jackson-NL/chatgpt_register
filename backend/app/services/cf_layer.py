"""CF 层：Turnstile 坐标点击 + 轮询 + cf_clearance 提取复用 + 组合判定

- 检测 Turnstile iframe → 定位 checkbox 坐标点击 → 轮询完成
- 提取 cf_clearance cookie 复用
- 组合判定：页面阶段 + cookie + 响应头多信号判断是否被 CF 拦截
"""
import asyncio
import json
from urllib.parse import urlparse

# ------------------------------------------------------------------
# Turnstile 处理
# ------------------------------------------------------------------

TURNSTILE_IFRAME_SELECTORS = [
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[title*="challenge"]',
    'iframe[src*="turnstile"]',
    'iframe[src*="cf-turnstile"]',
]

TURNSTILE_BOX_SELECTORS = [
    'input[type="checkbox"][name="cf-turnstile-response"]',
    'input[type="checkbox"][id*="turnstile"]',
    'input[type="checkbox"][name*="cf"]',
]


async def detect_turnstile(page) -> bool:
    """检测页面是否有 Turnstile 挑战 iframe"""
    for sel in TURNSTILE_IFRAME_SELECTORS:
        if await page.locator(sel).count() > 0:
            return True
    return False


async def solve_turnstile(page, max_wait_s: float = 30.0) -> bool:
    """定位 checkbox 坐标并点击，轮询直到挑战完成"""
    if not await detect_turnstile(page):
        return True  # 无 Turnstile 视为已通过

    deadline = asyncio.get_event_loop().time() + max_wait_s
    while asyncio.get_event_loop().time() < deadline:
        # 尝试直接点 checkbox（坐标点击）
        clicked = False
        for sel in TURNSTILE_BOX_SELECTORS:
            box = page.locator(sel).first
            if await box.count() and await box.is_visible():
                try:
                    await box.click(timeout=5000)
                    clicked = True
                    break
                except Exception:
                    # 可能被遮挡，改用坐标点击
                    try:
                        bbox = await box.bounding_box()
                        if bbox:
                            x = bbox["x"] + bbox["width"] / 2
                            y = bbox["y"] + bbox["height"] / 2
                            await page.mouse.click(x, y)
                            clicked = True
                            break
                    except Exception:
                        pass

        if not clicked:
            # iframe 内 checkbox 坐标点击
            frame = page.locator(TURNSTILE_IFRAME_SELECTORS[0]).first
            if await frame.count():
                try:
                    bbox = await frame.bounding_box()
                    if bbox:
                        # checkbox 通常在 iframe 左侧约 25% 位置
                        await page.mouse.click(
                            bbox["x"] + bbox["width"] * 0.25,
                            bbox["y"] + bbox["height"] * 0.5,
                        )
                except Exception:
                    pass

        await asyncio.sleep(2)

        # 轮询：挑战完成（iframe 消失或 challenge 元素消失）
        if not await detect_turnstile(page):
            return True

        # 检查页面是否已可交互（出现预期的登录/注册元素）
        try:
            if await page.locator('input[type="email"], input[type="tel"], input[name="code"]').count() > 0:
                return True
        except Exception:
            pass

    return False


# ------------------------------------------------------------------
# cf_clearance 提取 / 复用
# ------------------------------------------------------------------

def extract_cf_clearance(context) -> str:
    """从浏览器上下文提取 cf_clearance cookie"""
    try:
        cookies = context.cookies()
        for c in cookies:
            if c["name"] == "cf_clearance":
                return c["value"]
    except Exception:
        pass
    return ""


async def get_cf_clearance_cookie(context) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: extract_cf_clearance(context))


def build_cf_headers(cf_clearance: str, user_agent: str = "") -> dict:
    """用 cf_clearance 构造后续请求头（复用）"""
    headers = {
        "User-Agent": user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    if cf_clearance:
        headers["Cookie"] = f"cf_clearance={cf_clearance}"
    return headers


# ------------------------------------------------------------------
# 组合判定：多信号判断是否被 CF 拦截
# ------------------------------------------------------------------

def combined_judgment(state: dict, context=None, response=None) -> dict:
    """组合判定是否被 Cloudflare 拦截

    信号源：
    1. 页面阶段（cloudflare_challenge / page_error）
    2. 页面标题（Just a moment / Oops）
    3. 响应头（cf-ray / 403）
    4. cookie（cf_clearance 是否存在）
    """
    signals = []

    phase = state.get("phase", "")
    title = state.get("title", "")
    if phase in ("cloudflare_challenge", "page_error"):
        signals.append(f"phase={phase}")
    if "Just a moment" in title:
        signals.append("title=just_a_moment")
    if "Oops, an error" in title:
        signals.append("title=oops_error")

    if response is not None:
        try:
            status = getattr(response, "status", 0)
            if status in (403, 429):
                signals.append(f"http={status}")
            ray = response.headers.get("cf-ray", "")
            if ray:
                signals.append("has_cf_ray")
        except Exception:
            pass

    if context is not None:
        try:
            if not extract_cf_clearance(context):
                signals.append("no_cf_clearance")
            else:
                signals.append("has_cf_clearance")
        except Exception:
            pass

    blocked = any(s.startswith(("phase=cloudflare", "phase=page_error", "title=just", "title=oops", "http=403", "http=429")) for s in signals)
    return {
        "blocked": blocked,
        "signals": signals,
        "cf_clearance": extract_cf_clearance(context) if context is not None else "",
    }
