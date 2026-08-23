import asyncio
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import registrations
from app.db import Base
from app.models import Account, Registration
from app.services.registrations import _registration_placeholder_phone
from app.services import registrations as registration_service
from app.services.registrations import RegistrationService


def _db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_cancel_registration_endpoint_marks_running_registration_canceled(monkeypatch):
    db = _db_session()
    reg = Registration(status="running", proxy="http://127.0.0.1:7890")
    db.add(reg)
    db.commit()
    reg_id = reg.id

    class FakeService:
        def cancel_registration(self, rid):
            assert rid == reg_id
            reg = db.get(Registration, rid)
            reg.status = "canceled"
            db.commit()
            return True

    monkeypatch.setattr(registrations, "SERVICE", FakeService())

    result = asyncio.run(registrations.cancel_registration(reg_id, db=db))

    assert result == {"ok": True, "registration_id": reg_id, "status": "canceled"}
    assert db.get(Registration, reg_id).status == "canceled"


def test_registration_placeholder_phone_avoids_legacy_and_existing_values():
    db = _db_session()
    db.add_all(
        [
            Account(phone="mail_172", email="legacy@example.com"),
            Account(phone="mail_reg_172", email="first@example.com"),
        ]
    )
    db.commit()

    assert _registration_placeholder_phone(db, 172) == "mail_reg_172_2"


# ------------------------------------------------------------------
# 静默期节点轮换：仅当没有其他进行中注册时，新任务启动前换出口
# ------------------------------------------------------------------

def _patch_rotate(monkeypatch, calls):
    async def fake_rotate(*, log=None, proxy="", controller_url="", selector_name=""):
        calls.append(proxy)
        return {"ok": True, "before": "nodeA", "after": "nodeB", "ip": "203.0.113.7"}

    monkeypatch.setattr("app.services.clash_verge.rotate_clash_proxy_for_round", fake_rotate)


def test_quiesced_rotation_runs_when_no_other_registration_active(monkeypatch):
    calls = []
    _patch_rotate(monkeypatch, calls)

    service = RegistrationService(concurrency=2)
    service._active = 1  # 只有当前任务在跑：允许动全局出口
    asyncio.run(service._rotate_node_for_fresh_registration(99, gmail_mode=False, proxy="http://reg-proxy:1"))

    assert calls == ["http://reg-proxy:1"]


def test_quiesced_rotation_skips_when_other_registrations_running(monkeypatch):
    calls = []
    _patch_rotate(monkeypatch, calls)

    service = RegistrationService(concurrency=3)
    service._active = 2  # 还有别的浏览器在注册：不能切换全局出口
    asyncio.run(service._rotate_node_for_fresh_registration(99, gmail_mode=False, proxy="http://p:1"))

    assert calls == []


def test_quiesced_rotation_lock_rechecks_active_after_waiting(monkeypatch):
    """轮换进行中其他任务拿到信号量启动时，必须等锁并在锁内复查后放弃轮换。"""
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_rotate(*, log=None, proxy="", controller_url="", selector_name=""):
        calls.append(proxy)
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr("app.services.clash_verge.rotate_clash_proxy_for_round", slow_rotate)

    from app.config import settings

    service = RegistrationService(concurrency=3)
    service._active = 1

    async def scenario():
        first = asyncio.create_task(service._rotate_node_for_fresh_registration(1, gmail_mode=False, proxy="http://a:1"))
        await started.wait()
        service._active = 2  # 模拟等待期间另一个浏览器已启动
        second = asyncio.create_task(service._rotate_node_for_fresh_registration(2, gmail_mode=False, proxy="http://b:1"))
        await asyncio.sleep(0.05)
        assert calls == ["http://a:1"]  # 第二个还在等锁
        release.set()
        await asyncio.gather(first, second)
        assert calls == ["http://a:1"]  # 复查 _active>1 后放弃

    asyncio.run(scenario())


def test_quiesced_rotation_skips_gmail_batch_and_disabled_setting(monkeypatch):
    calls = []
    _patch_rotate(monkeypatch, calls)

    from app.config import settings

    service = RegistrationService(concurrency=2)
    service._active = 1
    # Gmail 批量模式由 batch 协调器每轮轮换，这里跳过避免重复切换
    asyncio.run(service._rotate_node_for_fresh_registration(99, gmail_mode=True, proxy="http://p:1"))
    assert calls == []

    monkeypatch.setattr(settings, "clash_rotate_enabled", False)
    asyncio.run(service._rotate_node_for_fresh_registration(99, gmail_mode=False, proxy="http://p:1"))
    assert calls == []


def test_quiesced_rotation_failure_is_non_fatal(monkeypatch):
    async def failing_rotate(*, log=None, proxy="", controller_url="", selector_name=""):
        return {"ok": False, "error": "controller unreachable"}

    monkeypatch.setattr("app.services.clash_verge.rotate_clash_proxy_for_round", failing_rotate)

    service = RegistrationService(concurrency=2)
    service._active = 1
    # 轮换失败不能阻塞注册主流程
    asyncio.run(service._rotate_node_for_fresh_registration(99, gmail_mode=False, proxy="http://p:1"))


def test_release_debug_registration_endpoint_only_releases_debug_wait(monkeypatch):
    db = _db_session()
    reg = Registration(status="debug_waiting", error="email: final failure")
    db.add(reg)
    db.commit()
    reg_id = reg.id

    class FakeService:
        def release_debug_registration(self, rid):
            assert rid == reg_id
            return True

    monkeypatch.setattr(registrations, "SERVICE", FakeService())

    result = asyncio.run(registrations.release_debug_registration(reg_id, db=db))

    assert result == {"ok": True, "registration_id": reg_id, "status": "releasing_debug"}
    assert db.get(Registration, reg_id).status == "debug_waiting"


def test_log_redact_endpoints_get_and_toggle_switch():
    from app.services.registrator import is_redact_enabled, set_redact_enabled

    set_redact_enabled(True)
    try:
        # 默认脱敏开启
        assert registrations.get_log_redact() == {"enabled": True}
        # 切换为明文
        assert registrations.set_log_redact(registrations.LogRedactBody(enabled=False)) == {"enabled": False}
        assert is_redact_enabled() is False
        assert registrations.get_log_redact() == {"enabled": False}
        # 恢复脱敏
        assert registrations.set_log_redact(registrations.LogRedactBody(enabled=True)) == {"enabled": True}
        assert is_redact_enabled() is True
    finally:
        set_redact_enabled(True)


def test_registration_clear_logs_removes_memory_and_persisted_lines(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    reg = Registration(
        status="running",
        logs_json='[{"seq": 1, "ts": "10:00:00", "msg": "旧日志"}]',
    )
    db.add(reg)
    db.commit()
    reg_id = reg.id
    db.close()

    monkeypatch.setattr(registration_service, "SessionLocal", Session)
    service = RegistrationService()
    service._log_buffers[reg_id] = [{"seq": 2, "ts": "10:00:01", "msg": "内存日志"}]

    assert service.clear_logs(reg_id) is True
    assert service._log_buffers[reg_id] == []

    check = Session()
    try:
        assert check.get(Registration, reg_id).logs_json == "[]"
    finally:
        check.close()


def test_cancel_registration_releases_debug_wait(monkeypatch):
    service = RegistrationService()
    event = asyncio.Event()
    service._debug_events[17] = event

    monkeypatch.setattr(registration_service, "SessionLocal", _db_session)

    assert service.release_debug_registration(17) is True
    assert event.is_set()
    assert service.release_debug_registration(17) is False


def test_cancel_registration_releases_debug_wait_before_canceling_task(monkeypatch):
    db = _db_session()
    reg = Registration(status="debug_waiting")
    db.add(reg)
    db.commit()
    reg_id = reg.id

    async def exercise():
        service = RegistrationService()
        event = asyncio.Event()
        service._debug_events[reg_id] = event
        task = asyncio.create_task(asyncio.sleep(60))
        registration_service._JOBS[reg_id] = task
        try:
            assert service.cancel_registration(reg_id) is True
            assert event.is_set()
            assert task.cancelling() == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            registration_service._JOBS.pop(reg_id, None)

    asyncio.run(exercise())
