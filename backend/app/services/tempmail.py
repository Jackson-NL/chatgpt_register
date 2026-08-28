"""CF 临时邮箱服务适配（temp-api.708651.xyz）

创建随机邮箱地址 → 轮询收件 → 提取验证码
网络层：curl_cffi impersonate="chrome"（TLS 指纹）

配置全部来自 Settings（.env），不再写死 base_url/domain；
支持站点访问密码（x-custom-auth）、429 退避、可配置名前缀与随机后缀长度。
"""
import asyncio
import json
import random
import re
import string

from ..config import settings
from .http_client import get_json, post_json

# 兼容旧脚本的模块级默认值：实际取值以 Settings 为准
BASE_URL = settings.cf_temp_email_base_url
DOMAIN = settings.cf_temp_email_domain

_MASK_RE = re.compile(r"(eyJ[A-Za-z0-9_.\-]{20,}|Bearer\s+eyJ[A-Za-z0-9_.\-]{20,})")


class TempmailError(Exception):
    pass


def _extract_code(text: str) -> str | None:
    """从邮件文本提取验证码（对齐 worker/src/email/extract_code.ts 的规则）"""
    if not text:
        return None
    delim = r"\s*(?:[:：]|\bis\b|是|为|です)[\s:：]*"
    cn_ja_ko = r"验证码|认证码|确认码|認証コード|인증\s*코드|코드"
    en_kw = r"verification\s*code|confirm(?:ation)?\s*code|security\s*code|passcode|OTP|pin\s*code"
    all_kw = f"{cn_ja_ko}|{en_kw}"
    patterns = [
        re.compile(rf"\bcode{delim}(\d{{4,12}})\b", re.I),
        re.compile(rf"(?:{all_kw}){delim}(\d{{4,12}})\b", re.I),
        re.compile(rf"\bcode{delim}([A-Za-z0-9]{{4,12}})\b", re.I),
        re.compile(rf"(?:{all_kw}){delim}([A-Za-z0-9]{{4,12}})\b", re.I),
    ]
    for p in patterns:
        m = p.search(text)
        if m and m.group(1) and not _looks_like_date(m.group(1)):
            return m.group(1)
    m = re.search(r"(?:^|\s)(\d{4,12})(?:\s|$|\.|,)", text)
    if m and m.group(1) and not _looks_like_date(m.group(1)):
        return m.group(1)
    return None


def _looks_like_date(digits: str) -> bool:
    if len(digits) == 4:
        n = int(digits)
        if 1900 <= n <= 2099:
            return True
    if len(digits) == 8:
        year = int(digits[:4])
        month = int(digits[4:6])
        day = int(digits[6:8])
        if 1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31:
            return True
    return False


def _http_error_info(exc: Exception) -> tuple[str, int | None]:
    """尽量从 curl_cffi 异常中取出响应体与状态码，供日志/错误提示（脱敏）。"""
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    detail = ""
    if resp is not None:
        try:
            detail = resp.text[:200]
        except Exception:  # noqa: BLE001
            pass
    detail = _MASK_RE.sub("<jwt>", detail)
    return detail, status


class TempmailClient:
    """CF 临时邮箱客户端。

    参数缺省时全部读取 Settings（.env），保证注册流程使用统一邮箱配置模块的值。
    """

    def __init__(
        self,
        base_url: str | None = None,
        domain: str | None = None,
        site_password: str | None = None,
        name_prefix: str | None = None,
        random_length: int | None = None,
        poll_interval: float | None = None,
        max_retries: int | None = None,
        rate_limit_backoff: float | None = None,
    ):
        self.base_url = (base_url or settings.cf_temp_email_base_url).rstrip("/")
        self.domain = domain or settings.cf_temp_email_domain
        self.site_password = site_password if site_password is not None else settings.cf_temp_email_site_password
        self.name_prefix = name_prefix or settings.cf_temp_email_name_prefix
        self.random_length = random_length if random_length is not None else settings.cf_temp_email_random_length
        self.poll_interval = poll_interval if poll_interval is not None else settings.cf_temp_email_poll_interval
        self.max_retries = max_retries if max_retries is not None else settings.cf_temp_email_max_retries
        self.rate_limit_backoff = rate_limit_backoff if rate_limit_backoff is not None else settings.cf_temp_email_rate_limit_backoff

    # ---------- 请求辅助 ----------

    def _headers(self, jwt: str = "") -> dict:
        headers = {}
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
        if self.site_password:
            headers["x-custom-auth"] = self.site_password
        return headers

    async def _post_with_backoff(self, url: str, body: dict) -> dict:
        """POST JSON，429/网络错误按 max_retries 退避重试。"""
        for attempt in range(self.max_retries):
            try:
                return await post_json(url, body=body, headers=self._headers())
            except Exception as exc:  # noqa: BLE001
                detail, status = _http_error_info(exc)
                if status == 429 and attempt < self.max_retries - 1:
                    await asyncio.sleep(self.rate_limit_backoff * (attempt + 1))
                    continue
                raise TempmailError(f"请求失败 (HTTP {status or '-'}) {detail or str(exc)}".strip()) from exc
        raise TempmailError("请求失败：重试次数用尽")

    async def _get_with_backoff(self, url: str, jwt: str) -> dict:
        for attempt in range(self.max_retries):
            try:
                return await get_json(url, headers=self._headers(jwt))
            except Exception as exc:  # noqa: BLE001
                detail, status = _http_error_info(exc)
                if status == 429 and attempt < self.max_retries - 1:
                    await asyncio.sleep(self.rate_limit_backoff * (attempt + 1))
                    continue
                raise TempmailError(f"查询失败 (HTTP {status or '-'}) {detail or str(exc)}".strip()) from exc
        raise TempmailError("查询失败：重试次数用尽")

    # ---------- 核心操作 ----------

    def random_name(self) -> str:
        """name_prefix + 随机小写字母数字后缀。"""
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=self.random_length))
        return f"{self.name_prefix}{suffix}"

    async def create_address(self, name: str | None = None) -> tuple[str, str]:
        """创建邮箱地址，返回 (address, jwt)"""
        if not name:
            name = self.random_name()
        resp = await self._post_with_backoff(
            f"{self.base_url}/api/new_address",
            body={"name": name, "domain": self.domain},
        )
        if isinstance(resp, dict) and resp.get("address") and resp.get("jwt"):
            return resp["address"], resp["jwt"]
        raise TempmailError(f"创建地址失败: {str(resp)[:200]}")

    async def create_address_with_meta(self, name: str | None = None) -> dict:
        """创建邮箱地址，返回完整元信息 {address, jwt, address_id, password?}（供 MailIdentity 使用）。"""
        if not name:
            name = self.random_name()
        resp = await self._post_with_backoff(
            f"{self.base_url}/api/new_address",
            body={"name": name, "domain": self.domain},
        )
        if isinstance(resp, dict) and resp.get("address") and resp.get("jwt"):
            return resp
        raise TempmailError(f"创建地址失败: {str(resp)[:200]}")

    async def get_settings(self, jwt: str) -> dict:
        """地址设置/凭证验证：GET /api/settings。成功即 JWT 有效。"""
        return await self._get_with_backoff(f"{self.base_url}/api/settings", jwt)

    async def list_parsed_mails(self, jwt: str, limit: int = 10) -> list[dict]:
        resp = await self._get_with_backoff(f"{self.base_url}/api/parsed_mails?limit={limit}&offset=0", jwt)
        if isinstance(resp, dict):
            return resp.get("results", [])
        raise TempmailError(f"查询邮件失败: {resp}")

    async def wait_for_code(
        self,
        jwt: str,
        timeout: float | None = None,
        poll_interval: float | None = None,
        after_mail_id: int | None = None,
        recipient: str | None = None,
    ) -> str:
        """轮询收件箱直到拿到验证码；429/网络错误自动退避重试。

        recipient：当多个账号共用一个固定收件箱时，按邮件 sender 中的真实收件人
        （如 noreply_at_tm.openai.com_<local>@duck.com 含 <local>@duck.com）精确匹配，
        避免并发时验证码串线。
        """
        timeout = timeout if timeout is not None else settings.cf_temp_email_poll_timeout
        poll_interval = poll_interval if poll_interval is not None else self.poll_interval
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                mails = await self.list_parsed_mails(jwt, limit=10)
                for mail in mails:
                    if after_mail_id is not None:
                        raw_id = mail.get("id")
                        if str(raw_id).isdigit() and int(raw_id) <= int(after_mail_id):
                            continue
                    if recipient is not None:
                        hay = " ".join(str(mail.get(k, "") or "") for k in ("sender", "source", "address", "subject", "text", "html"))
                        if recipient not in hay:
                            continue
                    text = " ".join(str(mail.get(k, "") or "") for k in ("html", "text", "subject"))
                    code = _extract_code(text)
                    if code:
                        return code
            except Exception:
                pass
            await asyncio.sleep(poll_interval)
        raise TempmailError("等待验证码超时")


if __name__ == "__main__":
    async def _t():
        client = TempmailClient()
        address, jwt = await client.create_address()
        print(f"地址: {address}")
        print("测试通过")
    asyncio.run(_t())
