import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.smsbower_mail import SmsbowerMailClient
from app.services.smsbower_mail import SmsbowerMailError
from app.services.process_utils import hidden_subprocess_kwargs


def test_get_code_accepts_nested_data_code(monkeypatch):
    client = SmsbowerMailClient(api_key="test")

    async def fake_request(action, **params):
        assert action == "getCode"
        assert params == {"mailId": "mail-1"}
        return {"status": 1, "data": {"code": "123456"}}

    monkeypatch.setattr(client, "_request", fake_request)

    received, code = asyncio.run(client.get_code("mail-1"))

    assert received is True
    assert code == "123456"


def test_request_handles_subprocess_start_error_without_unbound_json(monkeypatch):
    client = SmsbowerMailClient(api_key="test")

    async def fail_subprocess(*args, **kwargs):
        raise OSError("subprocess unavailable")

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_subprocess)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    try:
        asyncio.run(client._request("getActivation"))
    except SmsbowerMailError as exc:
        assert "subprocess unavailable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SmsbowerMailError")


def test_hidden_subprocess_kwargs_hide_windows_console():
    options = hidden_subprocess_kwargs()

    if sys.platform == "win32":
        assert options["creationflags"] & 0x08000000
        assert options["startupinfo"].wShowWindow == 0
    else:  # pragma: no cover - CI currently runs on Windows
        assert options == {}


def test_poll_code_falls_back_to_get_status_last_code(monkeypatch):
    client = SmsbowerMailClient(api_key="test")
    calls = {"getCode": 0, "getStatus": 0}

    async def fake_request(action, **params):
        calls[action] += 1
        if action == "getCode":
            return {"status": 0, "error": "Code has not been received yet, please try again later"}
        if action == "getStatus":
            return {"status": 1, "data": {"last_code": "654321"}}
        raise AssertionError(action)

    monkeypatch.setattr(client, "_request", fake_request)

    code = asyncio.run(client.poll_code("mail-1", timeout=1, interval=0, final_checks=0))

    assert code == "654321"
    assert calls == {"getCode": 1, "getStatus": 1}


def test_poll_code_ignores_previous_last_code_until_new_code_arrives(monkeypatch):
    client = SmsbowerMailClient(api_key="test")
    calls = {"getCode": 0, "getStatus": 0}

    async def fake_request(action, **params):
        calls[action] += 1
        if action == "getCode":
            if calls[action] == 1:
                return {"status": 0, "error": "Code has not been received yet, please try again later"}
            return {"status": 1, "code": "222222"}
        if action == "getStatus":
            return {"status": 1, "data": {"last_code": "111111"}}
        raise AssertionError(action)

    monkeypatch.setattr(client, "_request", fake_request)

    code = asyncio.run(client.poll_code("mail-1", timeout=1, interval=0, final_checks=0, ignore_code="111111"))

    assert code == "222222"
    assert calls == {"getCode": 2, "getStatus": 1}


def test_prepare_next_code_reuses_existing_wait_next_state(monkeypatch):
    client = SmsbowerMailClient(api_key="test")
    calls = {"getStatus": 0, "setStatus": 0}

    async def fake_request(action, **params):
        calls[action] += 1
        if action == "getStatus":
            return {"status": 1, "data": {"status": 5, "status_description": "Wait for next code", "last_code": "111111"}}
        if action == "setStatus":
            raise AssertionError("already waiting; setStatus must not be called")
        raise AssertionError(action)

    monkeypatch.setattr(client, "_request", fake_request)

    data = asyncio.run(client.prepare_next_code("mail-1"))

    assert data["status"] == 5
    assert calls == {"getStatus": 1, "setStatus": 0}


def test_prepare_next_code_sets_status_5_when_old_code_needs_accepting(monkeypatch):
    client = SmsbowerMailClient(api_key="test")
    calls = []

    async def fake_request(action, **params):
        calls.append((action, params))
        if action == "getStatus":
            return {
                "status": 1,
                "data": {
                    "status": 6,
                    "status_description": "Wait for accepting code",
                    "available_to_get_next_code": True,
                    "last_code": "111111",
                },
            }
        if action == "setStatus":
            assert params == {"id": "mail-1", "status": "5"}
            return {"status": 1}
        raise AssertionError(action)

    monkeypatch.setattr(client, "_request", fake_request)

    data = asyncio.run(client.prepare_next_code("mail-1"))

    assert data["status"] == 6
    assert calls[0][0] == "getStatus"
    assert calls[1] == ("setStatus", {"id": "mail-1", "status": "5"})


def test_prepare_next_code_rejects_canceled_activation(monkeypatch):
    client = SmsbowerMailClient(api_key="test")

    async def fake_request(action, **params):
        assert action == "getStatus"
        return {
            "status": 1,
            "data": {
                "status": 2,
                "status_description": "Activation is canceled",
                "available_to_get_next_code": False,
                "last_code": None,
            },
        }

    monkeypatch.setattr(client, "_request", fake_request)

    try:
        asyncio.run(client.prepare_next_code("mail-1"))
    except SmsbowerMailError as exc:
        assert "不可复用" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SmsbowerMailError")
