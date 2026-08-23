"""邮箱 Provider 注册表：注册流程统一从这里取 Provider。"""
from .base import MailIdentity, MailProvider, MailProviderError, get_mail_provider
from .cf_temp_email import (
    CFTempEmailProvider,
    custom_registration_lock,
    mask_custom_pool_sample,
    parse_custom_pool,
    release_custom_mailbox,
    validate_custom_pool,
)
from .outlook import OutlookProvider, mask_outlook_sample, parse_outlook_pool, validate_outlook_pool

__all__ = [
    "MailIdentity",
    "MailProvider",
    "MailProviderError",
    "get_mail_provider",
    "CFTempEmailProvider",
    "custom_registration_lock",
    "mask_custom_pool_sample",
    "parse_custom_pool",
    "release_custom_mailbox",
    "validate_custom_pool",
    "OutlookProvider",
    "parse_outlook_pool",
    "validate_outlook_pool",
    "mask_outlook_sample",
]
