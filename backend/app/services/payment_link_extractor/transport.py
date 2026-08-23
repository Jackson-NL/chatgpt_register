from __future__ import annotations

import time
import uuid
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlsplit, urlunsplit

try:
    import requests
except ImportError:  # pragma: no cover - installation issue handled at runtime
    requests = None  # type: ignore

from .config import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from .errors import ConfigurationError, NetworkError, ProtocolError
from .logging_utils import compact_url, emit_log, safe_log_text
from .models import ExtractionConfig

try:
    from curl_cffi.requests import Session as CurlCffiSession  # type: ignore
except ImportError:  # pragma: no cover
    CurlCffiSession = None  # type: ignore

try:
    from curl_cffi.requests import RequestException as CurlCffiRequestException  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from curl_cffi.requests.errors import RequestException as CurlCffiRequestException  # type: ignore
    except ImportError:  # pragma: no cover
        CurlCffiRequestException = None  # type: ignore

try:
    from curl_cffi.requests import HTTPError as CurlCffiHTTPError  # type: ignore
except ImportError:  # pragma: no cover
    try:
        from curl_cffi.requests.errors import HTTPError as CurlCffiHTTPError  # type: ignore
    except ImportError:  # pragma: no cover
        CurlCffiHTTPError = None  # type: ignore


class TransportFactory(Protocol):
    def chatgpt(self, config: ExtractionConfig, proxy: str) -> Any: ...

    def stripe(self, config: ExtractionConfig) -> Any: ...


def new_session() -> Any:
    if CurlCffiSession is not None:
        return CurlCffiSession(impersonate="firefox")
    if requests is None:
        raise ConfigurationError("requests is required; install requirements.txt")
    return requests.Session()


def safe_close(session: Any) -> None:
    close = getattr(session, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def normalize_proxy_url(proxy: str) -> str:
    text = str(proxy or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    try:
        parsed = urlsplit(text)
    except Exception:
        return text
    if not parsed.scheme or not parsed.netloc:
        return text
    # curl-cffi needs remote DNS resolution for hostname-based SOCKS proxies.
    # Normalize the common socks5 spelling so user-provided proxy URLs work
    # consistently with both curl-cffi and requests.
    scheme = parsed.scheme.lower()
    if scheme == "socks5":
        scheme = "socks5h"
    host = parsed.hostname or ""
    if not host:
        return text
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    auth = ""
    if parsed.username is not None:
        auth = quote(unquote(parsed.username), safe="%")
        if parsed.password is not None:
            auth += ":" + quote(unquote(parsed.password), safe="%")
        auth += "@"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError as exc:
        raise ValueError("proxy contains an invalid port") from exc
    netloc = auth + host + port
    return urlunsplit((scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def set_proxy_url(session: Any, proxy: str) -> None:
    normalized = normalize_proxy_url(proxy)
    session.proxies = {"http": normalized, "https": normalized} if normalized else {}
    if normalized:
        apply_proxy_connect_host(session, normalized)


def apply_proxy_connect_host(session: Any, proxy_url: str) -> None:
    """Cliproxy-style proxies require the CONNECT ``Host`` header to point at
    the proxy itself (``host:port``) instead of the tunnel target; libcurl
    sends the target host by default and the proxy rejects it with 400.
    Override the CONNECT Host header whenever a curl_cffi session is used.
    """
    curl = getattr(session, "curl", None)
    if curl is None:
        return
    try:
        from curl_cffi import CurlOpt  # type: ignore
    except Exception:
        return
    try:
        parsed = urlsplit(str(proxy_url or ""))
    except Exception:
        return
    host = parsed.hostname or ""
    if not host:
        return
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        port = 80
    host_header = f"Host: {host}:{port}".encode("utf-8")
    try:
        curl.setopt(CurlOpt.PROXYHEADER, [host_header])
    except Exception:
        pass


def stage_http_request(
    session: Any,
    stage: str,
    method: str,
    url: str,
    log: Any | None = None,
    **kwargs: Any,
) -> Any:
    started = time.perf_counter()
    emit_log(log, f"{stage}: {method.upper()} {compact_url(url)}")
    try:
        response = session.request(method.upper(), url, **kwargs)
    except Exception as exc:
        detail = safe_log_text(exc)
        emit_log(log, f"{stage}: request error={detail}")
        if is_network_exception(exc):
            raise NetworkError(stage, detail) from exc
        raise
    emit_log(
        log,
        f"{stage}: HTTP {response.status_code} elapsed={time.perf_counter() - started:.2f}s",
    )
    return response


def is_network_exception(exc: BaseException) -> bool:
    """Return whether an exception indicates a transport failure.

    HTTP errors are deliberately excluded: an HTTP response means the transport
    completed, even when the provider returned a 4xx or 5xx status.
    """
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True

    if requests is not None:
        request_exceptions = requests.exceptions
        transport_exceptions = (
            request_exceptions.ConnectionError,
            request_exceptions.Timeout,
            request_exceptions.ChunkedEncodingError,
        )
        if isinstance(exc, transport_exceptions):
            return True

    if CurlCffiRequestException is not None:
        if isinstance(exc, CurlCffiRequestException):
            if CurlCffiHTTPError is not None and isinstance(exc, CurlCffiHTTPError):
                return False
            return type(exc).__name__ in {
                "ConnectionError",
                "ConnectTimeout",
                "ProxyError",
                "ReadTimeout",
                "SSLError",
                "Timeout",
            }

    return False


def response_json(response: Any, stage: str) -> dict[str, Any]:
    try:
        payload = response.json() or {}
    except Exception as exc:
        raise ProtocolError(502, f"{stage} invalid json: {safe_log_text(exc)}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError(502, f"{stage} returned non-object json")
    return payload


class DefaultTransportFactory:
    def chatgpt(self, config: ExtractionConfig, proxy: str) -> Any:
        device_id = str(uuid.uuid4())
        session = new_session()
        session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": f"{country_locale(config)},en;q=0.9",
                "Authorization": f"Bearer {config.access_token}",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
                "Content-Type": "application/json",
                "oai-device-id": device_id,
                "oai-language": country_locale(config),
                "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "Cookie": f"oai-did={device_id}",
            }
        )
        set_proxy_url(session, proxy)
        return session

    def stripe(self, config: ExtractionConfig) -> Any:
        session = new_session()
        session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": f"{country_locale(config)},en;q=0.9",
            }
        )
        set_proxy_url(session, config.checkout_proxy)
        return session


def country_locale(config: ExtractionConfig) -> str:
    # Config is normalized before a transport is created. Keep this helper
    # dependency-free so fake factories can use the same interface.
    from .config import country_config

    return country_config(config.country)[2]
