import base64
import json
from datetime import datetime, timezone


def parse_jwt_exp(token: str) -> datetime | None:
    """解析 JWT payload 的 exp（UTC naive datetime）；解析失败返回 None。"""
    if not token or token.count(".") != 2:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if not exp:
            return None
        return datetime.fromtimestamp(exp, timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None
