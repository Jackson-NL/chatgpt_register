"""Cloudflare 临时邮箱 Provider（cf_temp_email）。

基于 TempmailClient（配置驱动），补充 MailIdentity 语义与连接测试。

支持两种地址来源：
- generated：通过 CF API 创建随机地址，地址自身携带收件 JWT；
- custom_pool：从预配置地址池取地址，验证码统一从固定 inbox JWT 收取。
"""
import asyncio
import re
import threading
import time

from ...config import settings
from ...db import SessionLocal
from ...models import CustomMailbox, utcnow
from ..tempmail import TempmailClient, TempmailError
from .base import MailIdentity, MailProvider, MailProviderError, redact_error


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_POOL_LOCK = threading.Lock()
_POOL_ACTIVE: dict[tuple[tuple[str, ...], str], set[str]] = {}
_POOL_RESERVATIONS: dict[str, tuple[tuple[str, ...], str]] = {}
_POOL_CURSOR: dict[tuple[tuple[str, ...], str], int] = {}
_CUSTOM_REGISTRATION_LOCK: asyncio.Lock | None = None


def custom_registration_lock() -> asyncio.Lock:
    """固定收件箱共用一个验证码流，整个注册流程必须串行。"""
    global _CUSTOM_REGISTRATION_LOCK
    if _CUSTOM_REGISTRATION_LOCK is None:
        _CUSTOM_REGISTRATION_LOCK = asyncio.Lock()
    return _CUSTOM_REGISTRATION_LOCK


def parse_custom_pool(pool_text: str) -> list[str]:
    """解析自定义地址池；每行一个地址，忽略空行和 # 注释。"""
    addresses: list[str] = []
    seen: set[str] = set()
    for raw_line in (pool_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        address = raw_line.strip().lower()
        if not address or address.startswith("#"):
            continue
        if _EMAIL_RE.match(address) and address not in seen:
            addresses.append(address)
            seen.add(address)
    return addresses


def validate_custom_pool(pool_text: str) -> tuple[list[str], list[str]]:
    """返回有效地址和不包含敏感信息的格式错误。"""
    addresses: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw_line in enumerate((pool_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        address = raw_line.strip().lower()
        if not address or address.startswith("#"):
            continue
        if not _EMAIL_RE.match(address):
            errors.append(f"第 {index} 行邮箱格式不正确")
            continue
        if address in seen:
            errors.append(f"第 {index} 行邮箱重复")
            continue
        seen.add(address)
        addresses.append(address)
    return addresses, errors


def mask_custom_pool_sample(address: str) -> str:
    local, _, domain = address.partition("@")
    if len(local) <= 3:
        local = f"{local[:1]}***"
    else:
        local = f"{local[:2]}***{local[-1]}"
    return f"{local}@{domain}"


def sync_custom_mailbox_pool(pool: list[str]) -> None:
    """将配置地址池同步到持久化状态表，不重置既有使用记录。"""
    normalized = {str(address).strip().lower() for address in pool if str(address).strip()}
    db = SessionLocal()
    try:
        existing = {item.address: item for item in db.query(CustomMailbox).all()}
        for address in normalized:
            item = existing.get(address)
            if item is None:
                db.add(CustomMailbox(address=address, active=True, status="unused"))
            else:
                item.active = True
                item.updated_at = utcnow()
        for address, item in existing.items():
            if address not in normalized and item.active:
                item.active = False
                item.updated_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def custom_mailbox_pool_state(pool: list[str]) -> tuple[dict[str, int], list[dict]]:
    """返回当前池的脱敏状态清单，地址明文不离开后端。"""
    sync_custom_mailbox_pool(pool)
    db = SessionLocal()
    try:
        items = db.query(CustomMailbox).filter(CustomMailbox.active.is_(True)).order_by(CustomMailbox.id).all()
        counts = {status: 0 for status in ("unused", "in_use", "used", "failed")}
        result = []
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1
            result.append({
                "id": item.id,
                "address": mask_custom_pool_sample(item.address),
                "status": item.status,
                "allocated_at": item.allocated_at.isoformat() if item.allocated_at else None,
                "used_at": item.used_at.isoformat() if item.used_at else None,
            })
        return counts, result
    finally:
        db.close()


def release_custom_mailbox(address: str, *, outcome: str = "failed", error: str = "") -> None:
    """结束地址占用并写入终态；成功后不再回收到可分配池。"""
    normalized = (address or "").strip().lower()
    if not normalized:
        return
    with _POOL_LOCK:
        key = _POOL_RESERVATIONS.pop(normalized, None)
        if key:
            _POOL_ACTIVE.get(key, set()).discard(normalized)
    db = SessionLocal()
    try:
        item = db.query(CustomMailbox).filter(CustomMailbox.address == normalized).one_or_none()
        if item and item.status == "in_use":
            item.status = "used" if outcome == "used" else "failed"
            item.used_at = utcnow()
            item.last_error = "" if outcome == "used" else str(error or "注册未完成")[:500]
            item.updated_at = utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _reserve_custom_mailbox(pool: list[str], inbox_address: str) -> str:
    sync_custom_mailbox_pool(pool)
    key = (tuple(pool), inbox_address.lower())
    with _POOL_LOCK:
        active = _POOL_ACTIVE.setdefault(key, set())
        start = _POOL_CURSOR.get(key, 0)
        db = SessionLocal()
        try:
            states = {item.address: item for item in db.query(CustomMailbox).filter(CustomMailbox.active.is_(True)).all()}
            for offset in range(len(pool)):
                index = (start + offset) % len(pool)
                address = pool[index]
                item = states.get(address)
                if address in active or item is None or item.status != "unused":
                    continue
                item.status = "in_use"
                item.allocated_at = utcnow()
                item.last_error = ""
                item.updated_at = utcnow()
                db.commit()
                active.add(address)
                _POOL_RESERVATIONS[address] = key
                _POOL_CURSOR[key] = (index + 1) % len(pool)
                return address
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    raise MailProviderError(f"自定义邮箱池已耗尽：当前 {len(pool)} 个地址均已使用或不可用")


class CFTempEmailProvider(MailProvider):
    name = "cf_temp_email"

    def __init__(self, **overrides):
        # overrides 支持“未保存配置测试”：base_url/domain/site_password 等
        self.address_mode = (overrides.pop("address_mode", None) or settings.cf_temp_email_address_mode or "generated").lower()
        pool_text = overrides.pop("custom_pool", None)
        if pool_text is None:
            pool_text = settings.cf_temp_email_custom_pool
        self.custom_pool, self.pool_errors = validate_custom_pool(pool_text)
        self.inbox_address = (overrides.pop("inbox_address", None) or settings.cf_temp_email_inbox_address or "").strip().lower()
        self.inbox_jwt = overrides.pop("inbox_jwt", None)
        if self.inbox_jwt is None:
            self.inbox_jwt = settings.cf_temp_email_inbox_jwt
        self.poll_timeout = overrides.pop("poll_timeout", None) or settings.cf_temp_email_poll_timeout
        self.poll_interval = overrides.pop("poll_interval", None) or settings.cf_temp_email_poll_interval
        self.client = TempmailClient(poll_interval=self.poll_interval, **overrides)

    async def create_address(self) -> MailIdentity:
        if self.address_mode == "custom_pool":
            if self.pool_errors:
                raise MailProviderError("自定义邮箱池格式错误：" + "；".join(self.pool_errors))
            if not self.custom_pool:
                raise MailProviderError("自定义邮箱池为空，请先在「邮箱配置」中添加邮箱")
            if not _EMAIL_RE.match(self.inbox_address):
                raise MailProviderError("固定收件邮箱格式不正确")
            if not self.inbox_jwt:
                raise MailProviderError("固定收件邮箱缺少 JWT 凭证")
            address = _reserve_custom_mailbox(self.custom_pool, self.inbox_address)
            try:
                # 记录分配前的最大邮件 ID，避免固定收件箱中的旧验证码被误用。
                mails = await self.client.list_parsed_mails(self.inbox_jwt, limit=50)
                cursor = max((int(item.get("id")) for item in mails if str(item.get("id", "")).isdigit()), default=0)
            except Exception as exc:  # noqa: BLE001
                release_custom_mailbox(address)
                raise MailProviderError(f"cf_temp_email 固定收件箱不可用: {redact_error(exc)}") from exc
            return MailIdentity(
                provider=self.name,
                address=address,
                credential=self.inbox_jwt,
                meta={"custom_pool": True, "inbox_address": self.inbox_address, "after_mail_id": cursor},
            )
        try:
            meta = await self.client.create_address_with_meta()
        except TempmailError as e:
            raise MailProviderError(f"cf_temp_email 创建地址失败: {redact_error(e)}") from e
        return MailIdentity(
            provider=self.name,
            address=meta["address"],
            credential=meta.get("jwt", ""),
            meta={"address_id": meta.get("address_id"), "has_password": bool(meta.get("password"))},
        )

    async def wait_for_code(
        self,
        identity: MailIdentity,
        timeout: int | None = None,
        poll_interval: int | None = None,
    ) -> str:
        jwt = identity.credential if isinstance(identity.credential, str) else ""
        if not jwt:
            raise MailProviderError("cf_temp_email 缺少 JWT 凭证")
        try:
            return await self.client.wait_for_code(
                jwt,
                timeout=timeout,
                poll_interval=poll_interval,
                after_mail_id=identity.meta.get("after_mail_id"),
            )
        except TempmailError as e:
            raise MailProviderError(f"cf_temp_email 等待验证码失败: {redact_error(e)}") from e

    async def test_connection(self) -> dict:
        """非破坏性测试：创建 regtest 前缀测试地址并验证 JWT 可用。

        测试地址前缀固定为 regtest，与业务前缀（默认 reg）区分；返回里会说明。
        """
        start = time.monotonic()
        try:
            if self.address_mode == "custom_pool":
                if self.pool_errors:
                    return {
                        "ok": False,
                        "provider": self.name,
                        "message": "自定义邮箱池格式错误：" + "；".join(self.pool_errors),
                        "latency_ms": int((time.monotonic() - start) * 1000),
                    }
                if not self.custom_pool:
                    raise MailProviderError("自定义邮箱池为空")
                if not _EMAIL_RE.match(self.inbox_address):
                    raise MailProviderError("固定收件邮箱格式不正确")
                if not self.inbox_jwt:
                    raise MailProviderError("固定收件邮箱缺少 JWT 凭证")
                info = await self.client.get_settings(self.inbox_jwt)
                await self.client.list_parsed_mails(self.inbox_jwt, limit=1)
                latency = int((time.monotonic() - start) * 1000)
                balance = info.get("send_balance") if isinstance(info, dict) else None
                return {
                    "ok": True,
                    "provider": self.name,
                    "message": f"固定收件箱连接成功（地址池 {len(self.custom_pool)} 个）",
                    "inbox_address": self.inbox_address,
                    "custom_pool_count": len(self.custom_pool),
                    "send_balance": balance,
                    "latency_ms": latency,
                }
            meta = await self.client.create_address_with_meta(name=f"regtest{int(time.time())}")
            address = meta["address"]
            jwt = meta["jwt"]
            info = await self.client.get_settings(jwt)
            latency = int((time.monotonic() - start) * 1000)
            balance = info.get("send_balance") if isinstance(info, dict) else None
            return {
                "ok": True,
                "provider": self.name,
                "message": "连接成功（已创建 regtest 测试地址并验证凭证）",
                "address": address,
                "send_balance": balance,
                "latency_ms": latency,
            }
        except Exception as e:  # noqa: BLE001
            latency = int((time.monotonic() - start) * 1000)
            return {
                "ok": False,
                "provider": self.name,
                "message": f"连接失败: {redact_error(e)}",
                "latency_ms": latency,
            }
