import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Account
from app.schemas import Sub2APIReloginCreate
from app.services import sub2api_relogin as relogin_module
from app.services.sub2api import (
    Sub2APIClient,
    Sub2APIError,
    is_sub2api_error_account,
    normalize_sub2api_accounts,
)
from app.services.sub2api_relogin import Sub2APIReloginService
from app.db import Base


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class Sub2APIClientReloginTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_account_payload_and_error_signal(self):
        accounts = normalize_sub2api_accounts(
            {
                "data": {
                    "items": [
                        {
                            "id": 17,
                            "name": "owner@example.com|stored",
                            "credentials": {"email": "Owner@Example.com", "totp_secret": "LOCAL-SECRET"},
                            "group_ids": [2, 3, 2],
                            "status": "token_expired",
                            "last_error": {"message": "refresh failed"},
                        }
                    ]
                }
            }
        )

        self.assertEqual(accounts[0]["remote_id"], "17")
        self.assertEqual(accounts[0]["email"], "owner@example.com")
        self.assertEqual(accounts[0]["group_ids"], [2, 3])
        self.assertEqual(accounts[0]["totp_secret"], "LOCAL-SECRET")
        self.assertTrue(is_sub2api_error_account(accounts[0]))

    async def test_request_reauth_url_falls_back_to_available_endpoint(self):
        calls = []

        async def request(method, url, headers, json=None):
            calls.append((method, url, json))
            if len(calls) < 3:
                return FakeResponse(404, {})
            return FakeResponse(200, {"data": {"auth_url": "https://auth.example/?state=s1", "session_id": "session-1"}})

        client = Sub2APIClient(base_url="https://sub2api.example", admin_api_key="key", request=request)
        result = await client.request_reauth_url("remote/1", "http://localhost:1455/auth/callback")

        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["state"], "")
        self.assertEqual(calls[2][1], "https://sub2api.example/api/v1/admin/openai/accounts/remote%2F1/generate-auth-url")

    async def test_apply_credentials_falls_back_without_echoing_credentials(self):
        calls = []

        async def request(method, url, headers, json=None):
            calls.append((method, url, json))
            if len(calls) < 3:
                return FakeResponse(404, {})
            return FakeResponse(200, {"data": {"ok": True}})

        client = Sub2APIClient(base_url="https://sub2api.example", jwt="jwt", request=request)
        credentials = {"access_token": "AT_TEST", "refresh_token": "RT_TEST"}
        result = await client.apply_reauth_credentials("17", credentials)

        self.assertTrue(result["ok"])
        self.assertEqual(calls[-1][0], "POST")
        self.assertTrue(calls[-1][1].endswith("/api/v1/admin/accounts/17/oauth/callback"))
        self.assertIn("AT_TEST", str(calls[-1][2]))
        self.assertEqual(len([call for call in calls if call[0] in ("PATCH", "PUT")]), 0)
        with self.assertRaises(Sub2APIError):
            await Sub2APIClient(base_url="", jwt="jwt").clear_error("17")

    async def test_apply_credentials_raises_when_all_oauth_endpoints_unavailable(self):
        calls = []

        async def request(method, url, headers, json=None):
            calls.append((method, url, json))
            return FakeResponse(404, {})

        client = Sub2APIClient(base_url="https://sub2api.example", jwt="jwt", request=request)
        with self.assertRaises(Sub2APIError):
            await client.apply_reauth_credentials("17", {"access_token": "AT_TEST"})
        self.assertEqual(len(calls), 3)

    async def test_update_account_settings_uses_put_only(self):
        calls = []

        async def request(method, url, headers, json=None):
            calls.append((method, url, json))
            return FakeResponse(200, {"data": {"ok": True}})

        client = Sub2APIClient(base_url="https://sub2api.example", jwt="jwt", request=request)
        result = await client.update_account_settings("17", {"concurrency": 8, "load_factor": 8})

        self.assertTrue(result["ok"])
        self.assertEqual(calls[-1][0], "PUT")
        self.assertTrue(calls[-1][1].endswith("/api/v1/admin/accounts/17"))
        self.assertEqual(calls[-1][2], {"concurrency": 8, "load_factor": 8})
        self.assertEqual(len([call for call in calls if call[0] == "PATCH"]), 0, "Sub2API 没有 PATCH 路由，不应发起 PATCH 请求")

    async def test_update_account_settings_raises_on_put_failure(self):
        calls = []

        async def request(method, url, headers, json=None):
            calls.append((method, url, json))
            return FakeResponse(404, {})

        client = Sub2APIClient(base_url="https://sub2api.example", jwt="jwt", request=request)
        with self.assertRaises(Sub2APIError):
            await client.update_account_settings("17", {"concurrency": 8, "load_factor": 8})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "PUT")


class Sub2APIReloginServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.original_session_local = relogin_module.SessionLocal
        self.original_profiles_dir = relogin_module.settings.profiles_dir
        relogin_module.SessionLocal = self.session_factory
        db = self.session_factory()
        db.add_all(
            [
                Account(id=1, phone="local-1", email="owner@example.com", password="pw", totp_secret="JBSWY3DPEHPK3PXP", profile_path="profile-1"),
                Account(id=2, phone="local-2", email="missing-totp@example.com", password="pw", totp_secret="", profile_path="profile-2"),
                Account(id=3, phone="local-3", email="missing-profile@example.com", password="pw", totp_secret="JBSWY3DPEHPK3PXP", profile_path=""),
            ]
        )
        db.commit()
        db.close()

    def tearDown(self):
        relogin_module.SessionLocal = self.original_session_local
        relogin_module.settings.profiles_dir = self.original_profiles_dir
        self.engine.dispose()

    def _remote_accounts(self):
        return [
            {"remote_id": "r-1", "email": "OWNER@example.com", "name": "owner", "group_ids": [42], "status": "error", "error_text": "token expired"},
            {"remote_id": "r-2", "email": "missing-totp@example.com", "name": "totp", "group_ids": [42], "status": "error", "error_text": "invalid"},
            {"remote_id": "r-3", "email": "missing-profile@example.com", "name": "profile", "group_ids": [42], "status": "error", "error_text": "failed"},
            {"remote_id": "r-4", "email": "nobody@example.com", "name": "missing", "group_ids": [42], "status": "error", "error_text": "failed"},
            {"remote_id": "r-5", "email": "owner@example.com", "name": "healthy", "group_ids": [42], "status": "active", "error_text": ""},
        ]

    async def test_preview_matches_local_and_records_skip_reasons(self):
        class FakeClient:
            async def list_accounts(self, group_ids):
                return self_accounts

        self_accounts = self._remote_accounts()
        service = Sub2APIReloginService(client_factory=lambda: FakeClient())
        result = await service.preview([42], only_error=True)
        by_remote = {item["remote_id"]: item for item in result["items"]}

        self.assertEqual(result["remote_total"], 4)
        self.assertEqual(result["error_total"], 4)
        self.assertEqual(result["matched_local"], 3)
        self.assertEqual(result["missing_local"], 1)
        self.assertEqual(result["runnable"], 1)
        self.assertEqual(by_remote["r-1"]["local_account_id"], 1)
        self.assertEqual(by_remote["r-2"]["reason"], "missing_totp")
        self.assertEqual(by_remote["r-3"]["reason"], "missing_profile")
        self.assertEqual(by_remote["r-4"]["reason"], "missing_local")
        self.assertNotIn("r-5", by_remote)

    async def test_create_job_persists_only_error_scan_items(self):
        class FakeClient:
            async def list_accounts(self, group_ids):
                return self_accounts

        self_accounts = self._remote_accounts()
        service = Sub2APIReloginService(client_factory=lambda: FakeClient())
        db = self.session_factory()
        job = await service.create_job(Sub2APIReloginCreate(group_ids=[42], only_error=True), db)
        items = db.query(relogin_module.Sub2APIReloginItem).filter_by(job_id=job.id).all()
        db.close()

        self.assertEqual(job.total, 4)
        self.assertEqual(len(items), 4)
        self.assertNotIn("r-5", {item.remote_account_id for item in items})



    async def test_create_job_reuses_preview_items_without_rescanning_remote(self):
        class NoRescanClient:
            async def list_accounts(self, group_ids):
                raise AssertionError("create_job should reuse preview_items instead of rescanning")

        preview_items = [
            {
                "remote_id": "r-1",
                "email": "owner@example.com",
                "status": "error",
                "error_text": "token expired",
                "local_account_id": 1,
                "action": "ready",
                "reason": "",
                "is_error": True,
            },
            {
                "remote_id": "r-non-error",
                "email": "owner@example.com",
                "status": "active",
                "error_text": "",
                "local_account_id": 1,
                "action": "skip",
                "reason": "not_error",
                "is_error": False,
            },
        ]
        service = Sub2APIReloginService(client_factory=lambda: NoRescanClient())
        db = self.session_factory()
        job = await service.create_job(Sub2APIReloginCreate(group_ids=[42], only_error=True, preview_items=preview_items), db)
        items = db.query(relogin_module.Sub2APIReloginItem).filter_by(job_id=job.id).all()
        db.close()

        self.assertEqual(job.total, 1)
        self.assertEqual(job.pending, 1)
        self.assertEqual([item.remote_account_id for item in items], ["r-1"])

    async def test_create_job_does_not_rescan_when_preview_has_no_error_items(self):
        class NoRescanClient:
            async def list_accounts(self, group_ids):
                raise AssertionError("create_job should reuse empty filtered preview instead of rescanning")

        service = Sub2APIReloginService(client_factory=lambda: NoRescanClient())
        db = self.session_factory()
        job = await service.create_job(
            Sub2APIReloginCreate(
                group_ids=[42],
                only_error=True,
                preview_items=[
                    {
                        "remote_id": "r-non-error",
                        "email": "owner@example.com",
                        "status": "active",
                        "error_text": "",
                        "local_account_id": 1,
                        "action": "skip",
                        "reason": "not_error",
                        "is_error": False,
                    }
                ],
            ),
            db,
        )
        items = db.query(relogin_module.Sub2APIReloginItem).filter_by(job_id=job.id).all()
        db.close()

        self.assertEqual(job.total, 0)
        self.assertEqual(items, [])

    async def test_relogin_profile_copy_is_committed_only_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile_dir = tmp_path / "profile-1"
            profile_dir.mkdir()
            (profile_dir / "session.txt").write_text("original", encoding="utf-8")
            relogin_module.settings.profiles_dir = str(tmp_path / "profiles")

            db = self.session_factory()
            account = db.get(Account, 1)
            account.profile_path = str(profile_dir)
            db.commit()
            db.close()

            class FakeClient:
                def __init__(self):
                    self.applied = []

                async def list_accounts(self, group_ids):
                    return [
                        {"remote_id": "r-ok", "email": "owner@example.com", "name": "ok", "group_ids": [42], "status": "error", "error_text": "expired"},
                        {"remote_id": "r-fail", "email": "owner@example.com", "name": "fail", "group_ids": [42], "status": "error", "error_text": "expired"},
                    ]

                async def request_reauth_url(self, account_id, redirect_uri, proxy_id=None):
                    return {"auth_url": f"https://auth.example/?state={account_id}", "session_id": account_id, "state": account_id, "endpoint": "reauth"}

                async def exchange_reauth_code(self, session_id, code, state, proxy_id=None):
                    if session_id == "r-fail":
                        raise Sub2APIError("exchange failed")
                    return {"access_token": "AT_TEST", "refresh_token": "RT_TEST"}

                async def apply_reauth_credentials(self, account_id, credentials, extra=None, proxy_id=None):
                    self.applied.append(account_id)
                    return {"endpoint": "apply"}

                async def clear_error(self, account_id):
                    return {}

                async def set_schedulable(self, account_id, schedulable=True):
                    return {}

                async def batch_refresh(self, account_ids):
                    return {}

            captured_profiles = []

            async def fake_browser_capture(**kwargs):
                profile_path = Path(kwargs["profile_path"])
                captured_profiles.append(profile_path)
                self.assertNotEqual(profile_path, profile_dir)
                (profile_path / f"{kwargs['expected_state']}.txt").write_text("changed", encoding="utf-8")
                return {"callback_url": f"http://localhost:1455/auth/callback?code=c1&state={kwargs['expected_state']}", "code": "c1", "state": kwargs["expected_state"], "elapsed_s": 0.1}

            service = Sub2APIReloginService(client_factory=FakeClient, browser_capture=fake_browser_capture)
            db = self.session_factory()
            job = await service.create_job(Sub2APIReloginCreate(group_ids=[42], concurrency=1, retry_reauth_url=1), db)
            db.close()
            await service.run_job(job.id)

            self.assertTrue((profile_dir / "r-ok.txt").exists())
            self.assertFalse((profile_dir / "r-fail.txt").exists())
            self.assertTrue(captured_profiles)
            self.assertTrue(all(not path.exists() for path in captured_profiles))

    async def test_one_item_failure_does_not_stop_other_items(self):
        remote = [self._remote_accounts()[0]]
        remote.append({"remote_id": "r-bad", "email": "owner@example.com", "name": "bad", "group_ids": [42], "status": "error", "error_text": "failed"})

        class FakeClient:
            async def list_accounts(self, group_ids):
                return remote

            async def request_reauth_url(self, account_id, redirect_uri, proxy_id=None):
                if account_id == "r-bad":
                    raise Sub2APIError("temporary endpoint failure")
                return {"auth_url": "https://auth.example/?state=s1", "session_id": "session-1", "state": "s1", "endpoint": "reauth"}

            async def exchange_reauth_code(self, session_id, code, state, proxy_id=None):
                return {"access_token": "AT_TEST", "refresh_token": "RT_TEST"}

            async def apply_reauth_credentials(self, account_id, credentials, extra=None, proxy_id=None):
                return {"endpoint": "apply"}

            async def clear_error(self, account_id):
                return {}

            async def set_schedulable(self, account_id, schedulable=True):
                return {}

            async def batch_refresh(self, account_ids):
                return {}

        async def fake_browser_capture(**kwargs):
            return {"callback_url": "http://localhost:1455/auth/callback?code=c1&state=s1", "code": "c1", "state": "s1", "elapsed_s": 0.1}

        service = Sub2APIReloginService(client_factory=lambda: FakeClient(), browser_capture=fake_browser_capture)
        db = self.session_factory()
        job = await service.create_job(Sub2APIReloginCreate(group_ids=[42], concurrency=2), db)
        db.close()
        await service.run_job(job.id)

        check = self.session_factory()
        current = check.get(type(job), job.id)
        items = check.query(relogin_module.Sub2APIReloginItem).filter_by(job_id=job.id).all()
        self.assertEqual(current.status, "completed")
        self.assertEqual(current.success, 1)
        self.assertEqual(current.failed, 1)
        self.assertEqual({item.status for item in items}, {"success", "failed"})
        check.close()


if __name__ == "__main__":
    unittest.main()
