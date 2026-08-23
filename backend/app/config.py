from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    db_path: str = str(BASE_DIR / "data" / "openai_register.db")
    profiles_dir: str = str(BASE_DIR / "profiles")

    smsbower_api_key: str = ""
    smsbower_base_url: str = "https://smsbower.app/stubs/handler_api.php"
    smsbower_service: str = "dr"
    smsbower_country: int = 73
    smsbower_max_price: float = 0.034
    smsbower_timeout: int = 20
    smsbower_poll_interval: int = 4
    smsbower_poll_timeout: int = 120
    smsbower_mail_ttl_minutes: int = 20

    registration_country_iso: str = "BR"
    registration_country_dialing_code: str = "55"

    concurrency_limit: int = 3
    task_poll_interval: int = 3
    # 批量注册调度参数：首个任务立即提交，后续任务保留短随机间隔。
    batch_submit_delay_min_seconds: float = 0.2
    batch_submit_delay_max_seconds: float = 1.2
    batch_poll_interval_seconds: float = 1.0
    batch_idle_poll_interval_seconds: float = 1.0
    default_proxy: str = "http://127.0.0.1:7890"
    clash_rotate_enabled: bool = True
    clash_controller_url: str = "http://127.0.0.1:9097"
    clash_controller_secret: str = "set-your-secret"
    clash_selector_name: str = "良心云"
    clash_rotate_settle_seconds: float = 1.5
    clash_rotate_max_attempts: int = 12
    # 轮换时在「良心云」Selector 下只切换名称含这些关键词的节点（逗号分隔，留空=不限制）。
    # 当前节点命名为 emoji+中文，如 🇯🇵日本高速01 / 🇸🇬新加坡高速01，故用 日本,新加坡。
    clash_allowed_region_keywords: str = ""
    # 节点延迟上限(ms)：主动测速或历史延迟超过该值视为不可用，跳过并切换下一个节点。0=不限制。
    clash_max_delay_ms: int = 3000
    # Codex OAuth 独立 Mihomo 实例（与注册工作台隔离，避免轮换互相打断）：
    # 代理端点 / 控制器 / Selector。留空则与注册工作台共用 settings.clash_*。
    oauth_proxy: str = ""
    oauth_clash_controller_url: str = ""
    oauth_clash_selector_name: str = ""
    # OAuth 启动前的 Clash 轮换最多等待多久，避免任务永久停在准备阶段。
    oauth_clash_rotate_timeout_seconds: float = 30.0

    new_account_cooldown_minutes: int = 120
    registration_bind_totp: bool = False
    # 注册批次标签：自动写入新账号 accounts.tag，用于区分不同批次/风控策略下注册的账号
    registration_tag: str = ""
    debug_har_dir: str = ""
    debug_screenshot_interval_ms: int = 2000
    debug_trace_enabled: bool = True
    oauth_authorize_url: str = "https://auth.openai.com/api/accounts/authorize"
    oauth_client_id: str = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"

    # ---------- Sub2API 管理端上传 ----------
    sub2api_base_url: str = ""
    sub2api_admin_api_key: str = ""
    sub2api_jwt: str = ""
    sub2api_timeout: float = 30
    sub2api_group_ids: str = ""

    # ---------- 管理员入口 ----------
    # 只从 backend/.env 或运行环境读取，不回传到前端。
    admin_auth_enabled: bool = False
    admin_access_key: str = ""
    admin_session_ttl_seconds: int = 8 * 60 * 60
    admin_cookie_secure: bool = False
    admin_login_window_seconds: int = 300
    admin_login_max_attempts: int = 8

    # ---------- 邮箱 Provider 配置（独立邮箱配置模块） ----------
    # 当前启用 Provider：cf_temp_email | outlook
    mail_provider: str = "cf_temp_email"

    # Cloudflare 临时邮箱（cf_temp_email）
    cf_temp_email_enabled: bool = True
    cf_temp_email_base_url: str = "https://temp-api.708651.xyz"
    cf_temp_email_domain: str = "708651.xyz"
    cf_temp_email_site_password: str = ""
    # generated: 通过 CF API 创建新地址；custom_pool: 使用预配置地址池，统一从 inbox_address 收信。
    cf_temp_email_address_mode: str = "generated"
    cf_temp_email_custom_pool: str = ""
    cf_temp_email_inbox_address: str = ""
    cf_temp_email_inbox_jwt: str = ""
    cf_temp_email_name_prefix: str = "reg"
    cf_temp_email_random_length: int = 10
    cf_temp_email_poll_interval: int = 4
    cf_temp_email_poll_timeout: int = 180
    cf_temp_email_max_retries: int = 3
    cf_temp_email_rate_limit_backoff: int = 10

    # Outlook（第一阶段以账号池 manual_pool 为主，imap/graph 仅预留）
    outlook_enabled: bool = False
    outlook_mode: str = "manual_pool"
    outlook_accounts_pool: str = ""
    outlook_poll_interval: int = 5
    outlook_poll_timeout: int = 180
    outlook_sender_filter: str = ""
    outlook_subject_filter: str = ""
    outlook_imap_host: str = "outlook.office365.com"
    outlook_imap_port: int = 993
    outlook_imap_ssl: bool = True
    outlook_graph_tenant_id: str = ""
    outlook_graph_client_id: str = ""
    outlook_graph_client_secret: str = ""


settings = Settings()
