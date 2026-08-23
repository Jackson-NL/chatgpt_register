import asyncio
import subprocess
import urllib.parse

from ..config import settings
from .process_utils import hidden_subprocess_kwargs

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0"
PROXY = "http://127.0.0.1:7890"


class SmsbowerError(Exception):
    pass


class SmsbowerClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.smsbower_api_key
        self.base_url = settings.smsbower_base_url
        self.timeout = settings.smsbower_timeout

    async def _get(self, action: str, **params) -> str:
        if not self.api_key:
            raise SmsbowerError("SMSBOWER_API_KEY 未配置")
        params = {"api_key": self.api_key, "action": action, **params}
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        last_err = ""
        for attempt in range(3):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "curl.exe", "-x", PROXY, "-sS", "--connect-timeout", str(self.timeout), url,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    **hidden_subprocess_kwargs(),
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout + 5)
                if proc.returncode != 0:
                    last_err = f"curl failed: {stderr.decode().strip()[:200]}"
                    await asyncio.sleep(1)
                    continue
                return stdout.decode().strip()
            except (asyncio.TimeoutError, OSError) as error:
                last_err = str(error)
                await asyncio.sleep(1)
        raise SmsbowerError(last_err or "curl 重试后仍失败")

    async def get_balance(self) -> float:
        text = await self._get("getBalance")
        if not text.startswith("ACCESS_BALANCE"):
            raise SmsbowerError(f"getBalance 失败: {text}")
        return float(text.split(":", 1)[1])

    async def get_number(self, service: str | None = None, country: int | None = None, max_price: float | None = None) -> tuple[str, str]:
        text = await self._get(
            "getNumber",
            service=service or settings.smsbower_service,
            country=str(country or settings.smsbower_country),
            maxPrice=str(max_price if max_price is not None else settings.smsbower_max_price),
        )
        if not text.startswith("ACCESS_NUMBER"):
            raise SmsbowerError(f"getNumber 失败: {text}")
        _, activation_id, phone = text.split(":")
        return activation_id, phone

    async def get_status(self, activation_id: str) -> tuple[str, str]:
        text = await self._get("getStatus", id=activation_id)
        if text.startswith("STATUS_OK"):
            return "code", text.split(":", 1)[1]
        if text.startswith("STATUS_WAIT_CODE"):
            return "wait", ""
        return text, ""

    async def set_status(self, activation_id: str, status: int, last_code: str | None = None) -> str:
        params = {"id": activation_id, "status": str(status)}
        if last_code:
            params["lastCode"] = last_code
        return await self._get("setStatus", **params)

    async def get_prices(self, service: str | None = None, country: int | None = None) -> str:
        return await self._get(
            "getPricesV3",
            service=service or settings.smsbower_service,
            country=str(country or settings.smsbower_country),
        )
