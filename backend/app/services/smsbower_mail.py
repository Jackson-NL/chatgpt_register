"""SMSBower Mail API 客户端：租临时 Gmail、收验证码、管理激活。"""
import asyncio
import json
import urllib.parse

from ..config import settings
from .process_utils import hidden_subprocess_kwargs

BASE_URL = "https://smsbower.page/api/mail"
REQUEST_TIMEOUT = 20


class SmsbowerMailError(Exception):
    pass


class SmsbowerMailClient:
    """临时 Gmail 邮箱 API 封装。

    三个核心接口：
    - get_activation(service, domain, alias, max_price) → {mail, mailId}
    - get_code(mail_id) → {received, code, pending}
    - set_status(mail_id, status) → complete/cancel 激活
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.smsbower_api_key

    async def _request(self, action: str, **params) -> dict:
        if not self.api_key:
            raise SmsbowerMailError("SMSBOWER_API_KEY 未配置")
        url = f"{BASE_URL}/{action}?api_key={self.api_key}&" + urllib.parse.urlencode(params)
        last_err = ""
        for attempt in range(3):
            try:
                cmd = ["curl.exe", "-sS"]
                proxy = str(settings.default_proxy or "").strip()
                if proxy:
                    cmd.extend(["-x", proxy])
                cmd.extend(["--connect-timeout", str(REQUEST_TIMEOUT), url])
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **hidden_subprocess_kwargs(),
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=REQUEST_TIMEOUT + 5)
                if proc.returncode != 0:
                    last_err = f"curl failed: {stderr.decode().strip()[:200]}"
                    await asyncio.sleep(1)
                    continue
                return json.loads(stdout.decode().strip())
            except (asyncio.TimeoutError, OSError, json.JSONDecodeError) as e:
                last_err = str(e)
                await asyncio.sleep(1)
        raise SmsbowerMailError(last_err or "curl 重试后仍失败")

    async def get_activation(
        self,
        service: str = "dr",
        domain: str = "gmail.com",
        alias: bool = True,
        max_price: float = 0.015,
    ) -> tuple[str, str]:
        """租一个临时 Gmail 邮箱。

        Returns:
            (mail_address: str, mail_id: str) 例如 ("habib7777y@gmail.com", "19912987")
        """
        payload = await self._request(
            "getActivation",
            service=service,
            domain=domain,
            alias="1" if alias else "0",
            maxPrice=str(max_price),
        )
        if payload.get("status") != 1:
            err = payload.get("error") or payload.get("message") or "未知错误"
            raise SmsbowerMailError(f"getActivation 失败: {err}")
        mail = str(payload.get("mail", "")).strip().lower()
        mail_id = str(payload.get("mailId", payload.get("mail_id", ""))).strip()
        if not mail or not mail_id:
            raise SmsbowerMailError("getActivation 返回数据不完整")
        return mail, mail_id

    async def get_code(self, mail_id: str) -> tuple[bool, str]:
        """轮询验证码。

        Returns:
            (received: bool, code: str) — received=True 时 code 为验证码
        """
        payload = await self._request("getCode", mailId=mail_id)
        data = payload.get("data")
        nested_code = data.get("code", "") if isinstance(data, dict) else ""
        code = str(payload.get("code") or payload.get("sms") or payload.get("answer") or nested_code).strip()
        if code:
            return True, code

        status = str(payload.get("status", "")).strip().lower()
        err = str(payload.get("error") or payload.get("message") or "").strip()
        # SMSBower Mail 的“未到码”响应可能是空 code、或带 wait/not received 文案。
        # 这类响应是正常轮询状态，不应抛错触发外层立即换邮箱。
        if "not been received" in err.lower() or "not received" in err.lower() or "wait" in err.lower():
            return False, ""
        if status in {"wait", "pending", ""}:
            return False, ""
        raise SmsbowerMailError(f"getCode 失败: {err or '未知响应'}")

    async def get_status(self, mail_id: str) -> dict:
        """查询 activation 状态。

        getCode 在个别场景会返回“未收到”，但 getStatus.data.last_code 已有验证码；
        因此收码流程用它做兜底确认。
        """
        payload = await self._request("getStatus", id=mail_id)
        if payload.get("status") != 1:
            err = payload.get("error") or payload.get("message") or "未知错误"
            raise SmsbowerMailError(f"getStatus 失败: {err}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    async def get_last_code(self, mail_id: str, ignore_code: str = "") -> tuple[bool, str]:
        """从 getStatus.data.last_code 读取最后一次验证码（兜底路径）。"""
        data = await self.get_status(mail_id)
        code = str(data.get("last_code") or "").strip()
        if ignore_code and code == str(ignore_code).strip():
            return False, ""
        return (bool(code), code)

    async def set_status(self, mail_id: str, status: int = 3) -> None:
        """设置激活状态。

        status:
            2 = 取消激活（释放号码）
            3 = 完成激活（确认验证码）
            5 = 等待下一验证码（复用同一 Gmail activation 前调用）
        """
        if status not in (2, 3, 5):
            raise SmsbowerMailError("status 必须为 2（取消）、3（完成）或 5（等待下一验证码）")
        payload = await self._request("setStatus", id=mail_id, status=str(status))
        if payload.get("status") != 1:
            err = payload.get("error") or payload.get("message") or "未知错误"
            raise SmsbowerMailError(f"setStatus 失败: {err}")

    async def prepare_next_code(self, mail_id: str) -> dict:
        """让 activation 进入可接收下一封验证码的状态。

        - status=1/5：已经在等待验证码/下一验证码，直接复用。
        - available_to_get_next_code=true 且已有旧码：调用 setStatus=5。
        - 已取消/不可复用：抛错，调用方应释放会话或重新租号。
        """
        data = await self.get_status(mail_id)
        actual_status = int(data.get("status") or 0)
        if actual_status in (1, 5):
            return data
        if data.get("available_to_get_next_code"):
            try:
                await self.set_status(mail_id, status=5)
            except SmsbowerMailError as exc:
                # setStatus=5 对已经处于等待态的 activation 会报 Bad actual activation status；
                # 复查后若状态确实是等待态，则视为成功。
                if "Bad actual activation status" not in str(exc):
                    raise
                data = await self.get_status(mail_id)
                actual_status = int(data.get("status") or 0)
                if actual_status not in (1, 5):
                    raise
            return await self.get_status(mail_id)
        raise SmsbowerMailError(
            f"activation 不可复用: status={actual_status} "
            f"description={data.get('status_description') or ''}"
        )

    async def poll_code(self, mail_id: str, timeout: int = 180, interval: int = 3, final_checks: int = 10, ignore_code: str = "") -> str:
        """持续轮询直到收到验证码或确认超时。

        超时后再做一组短间隔最终确认，避免验证码刚到达却被外层误判为失败，
        导致浏览器被关闭、activation 被浪费。
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            received, code = await self.get_code(mail_id)
            if received:
                return code
            received, code = await self.get_last_code(mail_id, ignore_code=ignore_code)
            if received:
                return code
            await asyncio.sleep(interval)
        for _ in range(final_checks):
            received, code = await self.get_code(mail_id)
            if received:
                return code
            received, code = await self.get_last_code(mail_id, ignore_code=ignore_code)
            if received:
                return code
            await asyncio.sleep(interval)
        raise SmsbowerMailError(f"轮询验证码超时，已对 mail_id={mail_id} 做最终确认")
