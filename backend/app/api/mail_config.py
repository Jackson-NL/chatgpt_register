"""邮箱配置 API（独立模块，主入口不是 /settings）。

- GET  /api/mail-config        脱敏返回当前配置
- POST /api/mail-config        保存配置（敏感字段支持占位符“不修改”语义）
- POST /api/mail-config/test   连接测试（支持未保存配置测试）
"""
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from ..config import BASE_DIR, settings
from ..schemas import (
    CFTempEmailConfig,
    MailConfigOut,
    MailConfigTestRequest,
    MailConfigUpdate,
    OutlookConfig,
)
from ..services.mail_providers import validate_outlook_pool
from ..services.mail_providers.base import redact_error
from .settings import _persist_env

router = APIRouter()

_MASK = "••••••••"
_ENV_FILE = BASE_DIR / ".env"

# 最近一次测试结果（进程内存，仅作展示；重启后为空）
_last_test: dict | None = None


# ---------- 工具 ----------

def _outlook_accounts() -> list[tuple[str, str]]:
    from ..services.mail_providers import parse_outlook_pool

    return parse_outlook_pool(settings.outlook_accounts_pool)


def _masked_samples() -> list[str]:
    from ..services.mail_providers import mask_outlook_sample

    return [mask_outlook_sample(email) for email, _ in _outlook_accounts()[:3]]


def _updated_at() -> str | None:
    try:
        ts = _ENV_FILE.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        return None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _persist_outlook_pool(value: str) -> None:
    # JSON quoting keeps multiline credentials in one valid dotenv assignment.
    _persist_env("outlook_accounts_pool", json.dumps(value, ensure_ascii=True))


def _persist_cf_custom_pool(value: str) -> None:
    # JSON quoting keeps multiline addresses in one valid dotenv assignment.
    _persist_env("cf_temp_email_custom_pool", json.dumps(value, ensure_ascii=True))


def _config_out() -> MailConfigOut:
    from ..services.mail_providers import custom_mailbox_pool_state, mask_custom_pool_sample, validate_custom_pool

    custom_pool, _ = validate_custom_pool(settings.cf_temp_email_custom_pool)
    custom_pool_status_counts, custom_pool_items = custom_mailbox_pool_state(custom_pool)
    accounts = _outlook_accounts()
    return MailConfigOut(
        provider=settings.mail_provider or "cf_temp_email",
        cf_temp_email=CFTempEmailConfig(
            enabled=settings.cf_temp_email_enabled,
            base_url=settings.cf_temp_email_base_url,
            domain=settings.cf_temp_email_domain,
            address_mode=settings.cf_temp_email_address_mode or "generated",
            custom_pool_count=len(custom_pool),
            custom_pool_sample=[mask_custom_pool_sample(address) for address in custom_pool[:3]],
            custom_pool_status_counts=custom_pool_status_counts,
            custom_pool_items=custom_pool_items,
            inbox_address=settings.cf_temp_email_inbox_address,
            has_inbox_jwt=bool(settings.cf_temp_email_inbox_jwt),
            name_prefix=settings.cf_temp_email_name_prefix,
            random_length=settings.cf_temp_email_random_length,
            poll_interval=settings.cf_temp_email_poll_interval,
            poll_timeout=settings.cf_temp_email_poll_timeout,
            max_retries=settings.cf_temp_email_max_retries,
            rate_limit_backoff=settings.cf_temp_email_rate_limit_backoff,
            has_site_password=bool(settings.cf_temp_email_site_password),
        ),
        outlook=OutlookConfig(
            enabled=settings.outlook_enabled,
            mode=settings.outlook_mode or "manual_pool",
            accounts_count=len(accounts),
            accounts_sample=_masked_samples(),
            poll_interval=settings.outlook_poll_interval,
            poll_timeout=settings.outlook_poll_timeout,
            sender_filter=settings.outlook_sender_filter,
            subject_filter=settings.outlook_subject_filter,
            imap_host=settings.outlook_imap_host,
            imap_port=settings.outlook_imap_port,
            imap_ssl=settings.outlook_imap_ssl,
            graph_tenant_id=settings.outlook_graph_tenant_id,
            graph_client_id=settings.outlook_graph_client_id,
            has_graph_client_secret=bool(settings.outlook_graph_client_secret),
        ),
        updated_at=_updated_at(),
        test_status=_last_test,
    )


def _validate_cf_common(payload) -> None:
    base_url = (payload.get("base_url", settings.cf_temp_email_base_url) or "").strip()
    domain = (payload.get("domain", settings.cf_temp_email_domain) or "").strip()
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        raise HTTPException(422, "base_url 必须是 http(s):// 开头的 URL")
    if domain == "":
        raise HTTPException(422, "domain 不能为空")
    address_mode = str(payload.get("address_mode", settings.cf_temp_email_address_mode) or "generated").lower()
    if address_mode not in ("generated", "custom_pool"):
        raise HTTPException(422, "address_mode 只能是 generated 或 custom_pool")
    for key in ("poll_interval", "poll_timeout", "max_retries", "rate_limit_backoff", "random_length"):
        v = payload.get(key)
        if v is not None and (not isinstance(v, int) or v <= 0):
            raise HTTPException(422, f"{key} 必须为正整数")


# ---------- 路由 ----------

@router.get("", response_model=MailConfigOut)
def get_mail_config():
    return _config_out()


@router.post("", response_model=MailConfigOut)
def update_mail_config(payload: MailConfigUpdate):
    requested_provider = payload.provider.lower() if payload.provider is not None else None
    if requested_provider is not None and requested_provider not in ("cf_temp_email", "outlook"):
        raise HTTPException(422, "不支持的邮箱 Provider")

    # 先保存各 Provider 具体配置，再在末尾决定当前启用 Provider。
    # 这样允许同一次请求提交 outlook.enabled/accounts_pool 后再切换 provider。
    if payload.cf_temp_email is not None:
        data = payload.cf_temp_email.model_dump(exclude_none=True)
        _validate_cf_common(data)
        # 敏感字段：占位符/空串表示不修改
        site_password = data.pop("site_password", None)
        custom_pool = data.pop("custom_pool", None)
        inbox_jwt = data.pop("inbox_jwt", None)
        for key in ("base_url", "domain", "name_prefix"):
            if key in data and isinstance(data[key], str):
                data[key] = data[key].strip()
        effective_mode = str(data.get("address_mode", settings.cf_temp_email_address_mode) or "generated").lower()
        effective_pool_text = custom_pool if custom_pool is not None else settings.cf_temp_email_custom_pool
        effective_inbox = str(data.get("inbox_address", settings.cf_temp_email_inbox_address) or "").strip()
        effective_jwt = str(inbox_jwt if inbox_jwt and inbox_jwt != _MASK else settings.cf_temp_email_inbox_jwt or "").strip()
        if effective_mode == "custom_pool":
            from ..services.mail_providers import validate_custom_pool

            effective_pool, pool_errors = validate_custom_pool(effective_pool_text)
            if pool_errors:
                raise HTTPException(422, "自定义邮箱池格式错误：\n" + "\n".join(pool_errors))
            if not effective_pool:
                raise HTTPException(422, "自定义邮箱池不能为空")
            if "@" not in effective_inbox or any(ch.isspace() for ch in effective_inbox):
                raise HTTPException(422, "固定收件邮箱格式不正确")
            if not effective_jwt:
                raise HTTPException(422, "固定收件邮箱 JWT 不能为空")
        if custom_pool is not None:
            from ..services.mail_providers import sync_custom_mailbox_pool, validate_custom_pool

            _, pool_errors = validate_custom_pool(custom_pool)
            if pool_errors:
                raise HTTPException(422, "自定义邮箱池格式错误：\n" + "\n".join(pool_errors))
            settings.cf_temp_email_custom_pool = custom_pool
            _persist_cf_custom_pool(custom_pool)
            sync_custom_mailbox_pool(validate_custom_pool(custom_pool)[0])
        if inbox_jwt and inbox_jwt != _MASK:
            settings.cf_temp_email_inbox_jwt = inbox_jwt.strip()
            _persist_env("cf_temp_email_inbox_jwt", settings.cf_temp_email_inbox_jwt)
        for key, value in data.items():
            setattr(settings, f"cf_temp_email_{key}", value)
            _persist_env(f"cf_temp_email_{key}", value)
        if site_password and site_password != _MASK:
            settings.cf_temp_email_site_password = site_password
            _persist_env("cf_temp_email_site_password", site_password)

    if payload.outlook is not None:
        data = payload.outlook.model_dump(exclude_none=True)
        accounts_pool = data.pop("accounts_pool", None)
        # 账号池：保存前解析校验，格式错误明确报错
        if accounts_pool is not None:
            _, errors = validate_outlook_pool(accounts_pool)
            if errors:
                raise HTTPException(422, "账号池格式错误：\n" + "\n".join(errors))
            settings.outlook_accounts_pool = accounts_pool
            _persist_outlook_pool(accounts_pool)
        graph_secret = data.pop("graph_client_secret", None)
        for key, value in data.items():
            setattr(settings, f"outlook_{key}", value)
            _persist_env(f"outlook_{key}", value)
        if graph_secret and graph_secret != _MASK:
            settings.outlook_graph_client_secret = graph_secret
            _persist_env("outlook_graph_client_secret", graph_secret)

    if requested_provider is not None:
        if requested_provider == "outlook":
            if not settings.outlook_enabled:
                raise HTTPException(422, "Outlook Provider 未启用，不能设为当前注册邮箱")
            accounts, errors = validate_outlook_pool(settings.outlook_accounts_pool)
            if errors:
                raise HTTPException(422, "Outlook 账号池格式错误：\n" + "\n".join(errors))
            if not accounts:
                raise HTTPException(422, "Outlook 账号池为空，不能设为当前注册邮箱")
        if requested_provider == "cf_temp_email" and not settings.cf_temp_email_enabled:
            raise HTTPException(422, "cf_temp_email Provider 未启用，不能设为当前注册邮箱")
        settings.mail_provider = requested_provider
        _persist_env("mail_provider", requested_provider)

    return _config_out()


@router.post("/test")
async def test_mail_config(payload: MailConfigTestRequest):
    global _last_test
    provider_name = (payload.provider or settings.mail_provider or "cf_temp_email").lower()
    config = payload.config or {}

    if provider_name == "cf_temp_email":
        from ..services.mail_providers import CFTempEmailProvider

        # 未保存配置测试：用表单当前值临时构建（site_password 占位符忽略）
        overrides = {}
        cf = config.get("cf_temp_email") or config
        for key in ("base_url", "domain", "name_prefix", "address_mode", "inbox_address"):
            if cf.get(key) is not None and str(cf.get(key)) != "":
                overrides[key] = str(cf[key]).strip()
        if cf.get("custom_pool") is not None:
            overrides["custom_pool"] = str(cf["custom_pool"])
        if cf.get("site_password") not in (None, "", _MASK):
            overrides["site_password"] = str(cf["site_password"])
        if cf.get("inbox_jwt") not in (None, "", _MASK):
            overrides["inbox_jwt"] = str(cf["inbox_jwt"])
        provider = CFTempEmailProvider(**overrides)
    elif provider_name == "outlook":
        from ..services.mail_providers import OutlookProvider

        overrides = {}
        out = config.get("outlook") or config
        if out.get("accounts_pool") is not None:
            overrides["accounts_pool"] = str(out["accounts_pool"])
        provider = OutlookProvider(**overrides)
    else:
        raise HTTPException(422, "不支持的邮箱 Provider")

    try:
        result = await provider.test_connection()
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "provider": provider_name, "message": f"测试异常: {redact_error(e)}"}

    result["tested_at"] = _utc_iso()
    _last_test = result
    return result
