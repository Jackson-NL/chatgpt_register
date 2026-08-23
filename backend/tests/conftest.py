"""确保 clash 轮换相关测试不受 backend/.env 中 CLASH_ALLOWED_REGION_KEYWORDS 影响。

生产环境的 .env 默认会限制日本/新加坡节点，但大部分用例使用通用节点名（node-a 等），
需要通过 autouse fixture 把地区关键词重置为空，保持用例确定性。需要验证地区过滤的用例
在各自内部显式设置该值。
"""
import pytest

from app.services import clash_verge


@pytest.fixture(autouse=True)
def _reset_region_keywords(monkeypatch):
    monkeypatch.setattr(clash_verge.settings, "clash_allowed_region_keywords", "")
