"""Browser-backed transport for the Stripe segment of the extraction flow.

纯 HTTP 的 Stripe 请求会被 bot 检测拦截（Elements session 拿不到
setup_intent/payment_intent），这里把 Stripe 段的请求改由真实 Chromium 的
``page.evaluate(fetch)`` 发出，携带真实浏览器 TLS 指纹与 JS 环境。
ChatGPT 段默认仍走 HTTP transport（保留 ID/TH 双出口逐步切换）。
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from .config import DEFAULT_USER_AGENT, country_config
from .transport import DefaultTransportFactory, TransportFactory  # noqa: F401  (re-export)


_FETCH_SCRIPT = """
async (req) => {
  const controller = new AbortController();
  const timeout = Number(req.timeout || 0);
  let timer = null;
  if (timeout > 0) {
    timer = setTimeout(() => controller.abort(), timeout * 1000);
  }
  try {
    const options = {
      method: req.method,
      headers: req.headers || {},
      body: req.body === null || req.body === undefined ? undefined : req.body,
      credentials: req.credentials || "same-origin",
      signal: controller.signal,
    };
    if (req.referrer) options.referrer = req.referrer;
    const response = await fetch(req.url, options);
    const text = await response.text();
    const headers = {};
    response.headers.forEach((value, key) => { headers[key] = value; });
    return { status: response.status, text, headers };
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}
"""

# 浏览器 fetch 禁止脚本设置的头（由浏览器自身补全，设置反而破坏请求）
_FORBIDDEN_BROWSER_HEADERS = {
    "accept-charset",
    "accept-encoding",
    "access-control-request-headers",
    "access-control-request-method",
    "connection",
    "content-length",
    "cookie",
    "cookie2",
    "date",
    "dnt",
    "expect",
    "host",
    "keep-alive",
    "origin",
    "permissions-policy",
    "referer",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "user-agent",
    "via",
}


class BrowserFetchResponse:
    def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None):
        self.status_code = int(status_code)
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        return json.loads(self.text)


class BrowserFetchSession:
    """requests-like session backed by Playwright ``page.evaluate(fetch)``.

    仅做传输层：调用方提供已建好的浏览器页面并负责导航/认证，本类只镜像
    extractor 库用到的 ``requests.Session`` 子集。页面走本地 CONNECT 桥代理，
    出口国家由桥的上游代理决定，这里不再注入代理配置。
    """

    def __init__(self, page: Any):
        if page is None:
            raise ValueError("page is required")
        self.page = page
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> BrowserFetchResponse:
        headers = dict(self.headers)
        headers.update({str(k): str(v) for k, v in dict(kwargs.get("headers") or {}).items()})

        body = None
        if kwargs.get("json") is not None:
            body = json.dumps(kwargs["json"], separators=(",", ":"))
            headers.setdefault("Content-Type", "application/json")
        elif kwargs.get("data") is not None:
            body = self._encode_form(kwargs["data"])
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        referrer = headers.get("Referer") or headers.get("referrer")
        clean_headers = self._browser_safe_headers(headers)
        full_url = self._with_params(url, kwargs.get("params"))
        req = {
            "method": method.upper(),
            "url": full_url,
            "headers": clean_headers,
            "body": body,
            "timeout": kwargs.get("timeout"),
            "credentials": "include"
            if urlsplit(full_url).hostname in {"chatgpt.com", "chat.openai.com"}
            else "omit",
            "referrer": referrer,
        }
        raw = self.page.evaluate(_FETCH_SCRIPT, req)
        return BrowserFetchResponse(
            status_code=raw.get("status", 0),
            text=str(raw.get("text", "")),
            headers=dict(raw.get("headers") or {}),
        )

    def get(self, url: str, **kwargs: Any) -> BrowserFetchResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> BrowserFetchResponse:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        return None

    @staticmethod
    def _browser_safe_headers(headers: dict[str, str]) -> dict[str, str]:
        safe: dict[str, str] = {}
        for key, value in headers.items():
            lower = key.lower()
            if lower.startswith("proxy-") or lower.startswith("sec-"):
                continue
            if lower in _FORBIDDEN_BROWSER_HEADERS:
                continue
            safe[key] = value
        return safe

    @staticmethod
    def _encode_form(data: Any) -> str:
        if isinstance(data, str):
            return data
        return urlencode(data, doseq=True)

    @staticmethod
    def _with_params(url: str, params: Any) -> str:
        if not params:
            return url
        parsed = urlsplit(url)
        query = parsed.query
        extra = urlencode(params, doseq=True)
        query = f"{query}&{extra}" if query else extra
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))


class BrowserTransportFactory:
    """全部请求都走浏览器页面的 transport factory（chatgpt 也被拦时使用）。"""

    def __init__(self, page: Any):
        if page is None:
            raise ValueError("page is required")
        self.page = page

    def chatgpt(self, config: Any, proxy: str) -> BrowserFetchSession:
        device_id = str(uuid.uuid4())
        session = BrowserFetchSession(self.page)
        locale = _country_locale(config)
        session.headers.update(
            {
                "Accept": "*/*",
                "Accept-Language": f"{locale},en;q=0.9",
                "Authorization": f"Bearer {str(config.access_token).removeprefix('Bearer ').strip()}",
                "Content-Type": "application/json",
                "oai-device-id": device_id,
                "oai-language": locale,
            }
        )
        return session

    def stripe(self, config: Any) -> BrowserFetchSession:
        session = BrowserFetchSession(self.page)
        locale = _country_locale(config)
        session.headers.update(
            {
                "Accept-Language": f"{locale},en;q=0.9",
            }
        )
        return session


class BrowserStripeTransportFactory:
    """Hybrid factory：ChatGPT 请求走普通 transport；Stripe 走浏览器 fetch。"""

    def __init__(self, page: Any, base_factory: Any | None = None):
        if page is None:
            raise ValueError("page is required")
        self.page = page
        if base_factory is None:
            base_factory = DefaultTransportFactory()
        self.base_factory = base_factory

    def chatgpt(self, config: Any, proxy: str) -> Any:
        return self.base_factory.chatgpt(config, proxy)

    def stripe(self, config: Any) -> BrowserFetchSession:
        return BrowserTransportFactory(self.page).stripe(config)


def _country_locale(config: Any) -> str:
    try:
        return str(country_config(str(config.country))[2])
    except Exception:
        mapping = {"ID": "id-ID", "TH": "th-TH", "PH": "en-PH", "GB": "en-GB", "US": "en-US"}
        return mapping.get(str(getattr(config, "country", "")).upper(), "en-US")
