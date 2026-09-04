"""提链浏览器回退链路。

Stripe 段被 bot 检测拦截时，改用真实 Chromium 执行 ``fetch()``：
1. Chromium 不支持启动参数里带认证代理，先在本地起一个 CONNECT 桥，
   负责补 ``Proxy-Authorization`` 和 cliproxy 要求的 CONNECT ``Host`` 头；
2. Chromium 经桥指向 checkout 出口代理，导航 chatgpt.com 取得真实来源；
3. 把页面交给 ``BrowserStripeTransportFactory``（ChatGPT 段仍走 HTTP，
   保留 ID/TH 双出口切换；scope="all" 时全浏览器）。
"""

from __future__ import annotations

import base64
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .link_proxies import split_proxy_url
from .payment_link_extractor.browser_transport import (
    BrowserStripeTransportFactory,
    BrowserTransportFactory,
)
from .payment_link_extractor.config import DEFAULT_USER_AGENT, country_config


class LocalConnectProxy:
    """线程版本地 CONNECT 代理桥（仅支持 HTTPS 隧道，够 Chromium 使用）。"""

    _MAX_HEADER = 64 * 1024

    def __init__(self, upstream_proxy: str):
        parsed = urlsplit(str(upstream_proxy or "").strip() or "http://127.0.0.1:7890")
        self.upstream_scheme = (parsed.scheme or "http").lower()
        self.upstream_host = parsed.hostname or "127.0.0.1"
        self.upstream_port = parsed.port or (443 if self.upstream_scheme == "https" else 80)
        username = parsed.username or ""
        password = parsed.password or ""
        if username:
            from urllib.parse import unquote

            token = f"{unquote(username)}:{unquote(password)}"
            self.auth_header = "Basic " + base64.b64encode(token.encode()).decode()
        else:
            self.auth_header = ""
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.port = 0

    def start(self) -> str:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(16)
        server.settimeout(0.5)
        self._server = server
        self.port = server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="link-connect-proxy", daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _open_upstream(self) -> socket.socket:
        sock = socket.create_connection((self.upstream_host, self.upstream_port), timeout=20)
        if self.upstream_scheme == "https":
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.upstream_host)
        return sock

    def _handle(self, conn: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            conn.settimeout(30)
            header = self._read_header(conn)
            if header is None:
                return
            first_line = header.split("\r\n", 1)[0]
            parts = first_line.split()
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                return
            target = parts[1]
            conn.settimeout(None)
            upstream = self._open_upstream()
            request = (
                f"CONNECT {target} HTTP/1.1\r\n"
                f"Host: {self.upstream_host}:{self.upstream_port}\r\n"
            )
            if self.auth_header:
                request += f"Proxy-Authorization: {self.auth_header}\r\n"
            request += "Proxy-Connection: keep-alive\r\n\r\n"
            upstream.sendall(request.encode())
            response = self._read_header(upstream)
            if response is None or " 200" not in response.split("\r\n", 1)[0]:
                conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self._relay(conn, upstream)
        except (OSError, ValueError, socket.timeout):
            pass
        finally:
            for sock in (conn, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    @staticmethod
    def _read_header(sock: socket.socket) -> str | None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(8192)
            if not chunk:
                return None
            data += chunk
            if len(data) > LocalConnectProxy._MAX_HEADER:
                return None
        return data.decode(errors="replace")

    @staticmethod
    def _relay(a: socket.socket, b: socket.socket) -> None:
        def pipe(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        threads = [
            threading.Thread(target=pipe, args=(a, b), daemon=True),
            threading.Thread(target=pipe, args=(b, a), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()


class BrowserExtractionContext:
    """每次尝试一个 Chromium 实例；with 退出时全部关闭。"""

    def __init__(
        self,
        checkout_proxy: str,
        *,
        country: str = "GB",
        scope: str = "stripe",
        headless: bool = True,
        settle_seconds: float = 3.0,
    ):
        self.checkout_proxy = str(checkout_proxy or "").strip()
        self.country = str(country or "GB").upper()
        self.scope = "all" if scope == "all" else "stripe"
        self.headless = headless
        self.settle_seconds = settle_seconds
        self._bridge = LocalConnectProxy(self.checkout_proxy) if self.checkout_proxy else None
        self._playwright = None
        self._browser = None
        self.page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        _, _, locale, timezone = country_config(self.country)
        bridge_url = self._bridge.start() if self._bridge else None
        try:
            self._playwright = sync_playwright().start()
            launch_kwargs: dict[str, object] = {
                "headless": self.headless,
                "args": ["--disable-dev-shm-usage", "--no-first-run", "--disable-features=IsolateOrigins"],
            }
            if bridge_url:
                launch_kwargs["proxy"] = {"server": bridge_url}
            self._browser = self._playwright.chromium.launch(**launch_kwargs)
            context = self._browser.new_context(
                user_agent=DEFAULT_USER_AGENT,
                locale=locale,
                timezone_id=timezone,
                viewport={"width": 1366, "height": 850},
            )
            self.page = context.new_page()
            try:
                self.page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
            except Exception:  # noqa: BLE001  挑战页/超时不阻断：fetch 仅需浏览器环境
                pass
            time.sleep(self.settle_seconds)
        except Exception:
            self._cleanup()
            raise
        if self.scope == "all":
            return BrowserTransportFactory(self.page)
        return BrowserStripeTransportFactory(self.page)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self._cleanup()
        return False

    def _cleanup(self) -> None:
        for closer in (getattr(self.page, "close", None), getattr(self._browser, "close", None)):
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    pass
        self.page = None
        self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None
