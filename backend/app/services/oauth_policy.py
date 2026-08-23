"""Codex OAuth 资格统一策略。

规则默认 fail closed：只有 mail_provider == "gmail" 的账号才可能进入
Codex OAuth；cf_temp_email / outlook / unknown 一律拒绝。前端候选过滤
与后端所有 OAuth 入口都必须复用这里的判定，禁止各自用邮箱域名推断。
"""

GMAIL_PROVIDER = "gmail"

BLOCK_NOT_GMAIL = "仅 Gmail 来源账号允许进入 Codex OAuth"
BLOCK_NO_PROFILE = "缺少 profile"
BLOCK_HAS_REFRESH_TOKEN = "已有 refresh_token"


def normalized_mail_provider(account) -> str:
    value = str(getattr(account, "mail_provider", "") or "").strip().lower()
    return value or "unknown"


def oauth_block_reason(account) -> str:
    """返回空字符串表示允许进入 Codex OAuth，否则返回明确拒绝原因。"""
    if normalized_mail_provider(account) != GMAIL_PROVIDER:
        return BLOCK_NOT_GMAIL
    if not getattr(account, "profile_path", ""):
        return BLOCK_NO_PROFILE
    if getattr(account, "refresh_token", ""):
        return BLOCK_HAS_REFRESH_TOKEN
    return ""


def oauth_eligibility(account) -> dict:
    reason = oauth_block_reason(account)
    return {
        "mail_provider": normalized_mail_provider(account),
        "oauth_eligible": not reason,
        "oauth_block_reason": reason,
    }
