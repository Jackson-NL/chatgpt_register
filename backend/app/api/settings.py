import json
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..db import get_db
from ..models import UiSetting
from ..schemas import SettingsOut

router = APIRouter()

_ENV_FILE = Path(BASE_DIR) / ".env"


def _persist_env(field: str, value) -> None:
    """把字段写回 .env，保证重启后仍生效。"""
    key = field.upper()
    lines = []
    if _ENV_FILE.exists():
        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("/ui")
def get_ui_settings(db: Session = Depends(get_db)):
    row = db.get(UiSetting, "ui")
    if not row or not row.value:
        return {}
    try:
        return json.loads(row.value)
    except Exception:  # noqa: BLE001
        return {}


@router.put("/ui")
def put_ui_settings(payload: dict, db: Session = Depends(get_db)):
    row = db.get(UiSetting, "ui")
    if not row:
        row = UiSetting(key="ui", value=json.dumps(payload, ensure_ascii=False))
        db.add(row)
    else:
        row.value = json.dumps(payload, ensure_ascii=False)
    db.commit()
    return {"ok": True}


@router.get("", response_model=SettingsOut)
def get_settings():
    return SettingsOut(
        smsbower_service=settings.smsbower_service,
        smsbower_country=settings.smsbower_country,
        smsbower_max_price=settings.smsbower_max_price,
        smsbower_base_url=settings.smsbower_base_url,
        smsbower_has_api_key=bool(settings.smsbower_api_key),
        concurrency_limit=settings.concurrency_limit,
        default_proxy=settings.default_proxy,
        new_account_cooldown_minutes=settings.new_account_cooldown_minutes,
        registration_bind_totp=settings.registration_bind_totp,
        registration_tag=settings.registration_tag,
        sub2api_base_url=settings.sub2api_base_url,
        sub2api_timeout=settings.sub2api_timeout,
        sub2api_group_ids=settings.sub2api_group_ids,
        sub2api_has_admin_api_key=bool(settings.sub2api_admin_api_key),
        sub2api_has_jwt=bool(settings.sub2api_jwt),
    )


@router.post("", response_model=SettingsOut)
def update_settings(payload: dict):
    for field in ("smsbower_api_key", "smsbower_service", "smsbower_country", "smsbower_max_price", "smsbower_base_url",
                  "concurrency_limit", "default_proxy", "new_account_cooldown_minutes", "registration_bind_totp"):
        value = payload.get(field)
        if value is not None and value != "":
            setattr(settings, field, value)
            _persist_env(field, value)
    # registration_tag 允许显式清空（空串=后续注册不打标签）
    if payload.get("registration_tag") is not None:
        value = str(payload["registration_tag"]).strip()[:64]
        setattr(settings, "registration_tag", value)
        _persist_env("registration_tag", value)
    for field in ("sub2api_base_url", "sub2api_timeout"):
        value = payload.get(field)
        if value is not None and value != "":
            setattr(settings, field, value)
            _persist_env(field, value)
    if "sub2api_group_ids" in payload and payload["sub2api_group_ids"] is not None:
        value = str(payload["sub2api_group_ids"]).strip()
        setattr(settings, "sub2api_group_ids", value)
        _persist_env("sub2api_group_ids", value)
    for field in ("sub2api_admin_api_key", "sub2api_jwt"):
        value = payload.get(field)
        if value is not None and value != "" and value != "••••••••":
            setattr(settings, field, value)
            _persist_env(field, value)
    return get_settings()


@router.post("/smsbower/test")
async def test_smsbower():
    """测试 smsbower 连接：查询余额验证 API Key 可用。"""
    from ..services.smsbower import SmsbowerClient

    try:
        balance = await SmsbowerClient().get_balance()
        return {"ok": True, "balance": balance}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}
