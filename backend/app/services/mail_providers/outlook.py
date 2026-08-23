"""Outlook Provider（第一阶段：账号池 manual_pool）。

- 用户在页面粘贴账号池，每行一个：`email,password` 或 `email----password`
- create_address 不是“创建新邮箱”，而是从池中轮换取用一个身份
- wait_for_code：无 IMAP/Graph 收信基础设施时明确返回“自动收信未启用”
  （IMAP/Graph 字段仅预留，默认不启用，不在此大规模实现 OAuth 授权）
"""
import re
import time

from ...config import settings
from .base import MailIdentity, MailProvider, MailProviderError

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_pool_text(pool_text: str) -> str:
    return (pool_text or "").replace(r"\r\n", "\n").replace(r"\n", "\n").replace(r"\r", "\r")


def _split_pool_line(line: str) -> tuple[str, str, str]:
    if "----" in line:
        email, sep, password = line.partition("----")
    elif "," in line:
        email, sep, password = line.partition(",")
    else:
        return "", "", ""
    return email.strip(), sep, password.strip()


def parse_outlook_pool(pool_text: str) -> list[tuple[str, str]]:
    """解析账号池文本 → [(email, password), ...]；空行/注释行忽略。

    每行支持两种分隔：`email,password` 或 `email----password`。
    """
    accounts: list[tuple[str, str]] = []
    for raw_line in _normalize_pool_text(pool_text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        email, sep, password = _split_pool_line(line)
        if not email or not password:
            continue
        accounts.append((email, password))
    return accounts


def validate_outlook_pool(pool_text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """解析并校验账号池；返回 (有效账号, 错误行描述[不回显密码])。"""
    accounts: list[tuple[str, str]] = []
    errors: list[str] = []
    for idx, raw_line in enumerate(_normalize_pool_text(pool_text).splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        email, sep, password = _split_pool_line(line)
        if not sep:
            errors.append(f"第 {idx} 行缺少分隔符，需使用 email,password 或 email----password")
            continue
        if not _EMAIL_RE.match(email):
            errors.append(f"第 {idx} 行邮箱格式不正确")
            continue
        if not password:
            errors.append(f"第 {idx} 行密码为空")
            continue
        accounts.append((email, password))
    return accounts, errors


def mask_outlook_sample(email: str) -> str:
    """脱敏样例：user@x.com → us***@x.com"""
    if "@" in email:
        local, _, domain = email.partition("@")
        if len(local) > 3:
            masked_local = f"{local[:2]}***{local[-1]}"
        else:
            masked_local = f"{local[0]}***"
        return f"{masked_local}@{domain}"
    return "***"


class OutlookProvider(MailProvider):
    name = "outlook"

    def __init__(self, **overrides):
        self.mode = overrides.pop("mode", None) or settings.outlook_mode or "manual_pool"
        pool_text = overrides.pop("accounts_pool", None)
        if pool_text is None:
            pool_text = settings.outlook_accounts_pool
        self.accounts, self.pool_errors = validate_outlook_pool(pool_text)
        self.poll_timeout = overrides.pop("poll_timeout", None) or settings.outlook_poll_timeout
        self.poll_interval = overrides.pop("poll_interval", None) or settings.outlook_poll_interval
        self.sender_filter = overrides.pop("sender_filter", settings.outlook_sender_filter) or ""
        self.subject_filter = overrides.pop("subject_filter", settings.outlook_subject_filter) or ""
        self.imap_host = overrides.pop("imap_host", None) or settings.outlook_imap_host
        self.imap_port = overrides.pop("imap_port", None) or settings.outlook_imap_port
        self.imap_ssl = overrides.pop("imap_ssl", settings.outlook_imap_ssl)
        self._cursor = 0

    @property
    def has_pool(self) -> bool:
        return len(self.accounts) > 0

    async def create_address(self) -> MailIdentity:
        if not self.has_pool:
            raise MailProviderError("Outlook 账号池为空，请先在「邮箱配置」中导入账号")
        # 轮换取用池中账号（并发注册避免都拿同一个）
        email, password = self.accounts[self._cursor % len(self.accounts)]
        self._cursor += 1
        return MailIdentity(
            provider=self.name,
            address=email,
            credential={"password": password, "mode": self.mode},
            meta={"pool_index": self._cursor - 1, "imap_enabled": bool(self.imap_host and self.imap_port)},
        )

    async def wait_for_code(
        self,
        identity: MailIdentity,
        timeout: int | None = None,
        poll_interval: int | None = None,
    ) -> str:
        timeout = timeout if timeout is not None else self.poll_timeout
        # 第一阶段没有收信基础设施（IMAP 收信/Graph 订阅均为预留），明确提示
        raise MailProviderError(
            "manual_pool 已配置但自动收信未启用：当前 Outlook 模式不会自动收取验证码，"
            "请配置 IMAP 或后续接入 Graph 后再使用（预留字段：imap_host/imap_port/imap_ssl）"
        )

    async def test_connection(self) -> dict:
        """测试账号池：解析并校验格式，返回数量与脱敏样例。"""
        start = time.monotonic()
        if self.pool_errors:
            return {
                "ok": False,
                "provider": self.name,
                "message": "账号池格式错误：" + "；".join(self.pool_errors),
                "accounts_count": len(self.accounts),
                "latency_ms": int((time.monotonic() - start) * 1000),
            }
        if not self.has_pool:
            return {
                "ok": False,
                "provider": self.name,
                "message": "账号池为空：请粘贴至少一行 email,password 或 email----password",
                "accounts_count": 0,
                "latency_ms": 0,
            }
        samples = [mask_outlook_sample(email) for email, _ in self.accounts[:3]]
        latency = int((time.monotonic() - start) * 1000)
        return {
            "ok": True,
            "provider": self.name,
            "message": f"账号池有效（{len(self.accounts)} 个账号）。注意：manual_pool 模式自动收信未启用，验证码需人工处理或配置 IMAP。",
            "accounts_count": len(self.accounts),
            "samples": samples,
            "latency_ms": latency,
        }
