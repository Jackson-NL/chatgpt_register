
import sys
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import Account


class AccountOAuthRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_target_pool_refills_a_slot_before_slow_target_finishes(self):
        from app.api.accounts import _run_oauth_target_pool

        started = []
        finished = []
        slow_started = asyncio.Event()
        replacement_started = asyncio.Event()
        release_slow = asyncio.Event()

        async def run_target(account_id):
            started.append(account_id)
            if account_id == 1:
                slow_started.set()
                await release_slow.wait()
            elif account_id == 3:
                replacement_started.set()
            finished.append(account_id)
            return True

        task = asyncio.create_task(
            _run_oauth_target_pool([1, 2, 3], 2, run_target)
        )
        await asyncio.wait_for(slow_started.wait(), 1)
        await asyncio.wait_for(replacement_started.wait(), 1)

        self.assertEqual(started[:2], [1, 2])
        self.assertIn(3, started)
        self.assertNotIn(1, finished)

        release_slow.set()
        results = await task
        self.assertEqual({account_id for account_id, _ in results}, {1, 2, 3})
        self.assertTrue(all(ok for _, ok in results))

    async def test_callback_listener_multiplexes_concurrent_states_on_one_port(self):
        from app.services.registrator import OAuthCallbackListener

        probe = await asyncio.start_server(lambda reader, writer: None, "127.0.0.1", 0)
        port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()

        async def send_callback(state, code):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            request = f"GET /auth/callback?code={code}&state={state} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            writer.write(request.encode("ascii"))
            await writer.drain()
            await reader.read()
            writer.close()
            await writer.wait_closed()

        uri = f"http://127.0.0.1:{port}/auth/callback"
        async with OAuthCallbackListener(uri, "state-a") as first:
            async with OAuthCallbackListener(uri, "state-b") as second:
                results = await asyncio.gather(
                    first.wait(2),
                    second.wait(2),
                    send_callback("state-b", "code-b"),
                    send_callback("state-a", "code-a"),
                )
                first_code, second_code = results[:2]

        self.assertEqual(first_code, "code-a")
        self.assertEqual(second_code, "code-b")

    async def test_codex_oauth_job_body_defaults_to_three_concurrent_workers(self):
        from app.api.accounts import CodexOAuthJobBody

        payload = CodexOAuthJobBody(account_ids=[1])

        self.assertEqual(payload.concurrency, 3)

    async def test_oauth_live_logs_capture_emit_log_for_frontend_polling(self):
        from app.api import accounts as accounts_api
        from app.services.registrator import clear_oauth_logs, emit_log, get_oauth_logs

        clear_oauth_logs()
        before = get_oauth_logs(after=0)
        emit_log("[oauth:auto-phone] 等待验证码 elapsed=5s")
        direct = get_oauth_logs(after=before["latest_seq"], limit=10)
        routed = accounts_api.list_oauth_logs(after=before["latest_seq"], limit=10)

        self.assertEqual(len(direct["items"]), 1)
        self.assertEqual(direct["items"][0]["msg"], "[oauth:auto-phone] 等待验证码 elapsed=5s")
        self.assertGreater(direct["items"][0]["seq"], before["latest_seq"])
        self.assertEqual(routed["items"], direct["items"])
        self.assertEqual(routed["latest_seq"], direct["latest_seq"])
        clear_oauth_logs()

    async def test_rent_smsbower_number_accepts_requested_country_aliases(self):
        from app.api import accounts as accounts_api

        calls = []

        class FakeSms:
            async def get_prices(self, service, country):
                calls.append(("prices", service, country))
                return '{"6":{"ot":{}},"4":{"ot":{}},"16":{"ot":{}},"53":{"ot":{}}}'

        attempts = []
        with patch.object(accounts_api.settings, "smsbower_service", "ot"):
            rental = await accounts_api._rent_smsbower_number(
                FakeSms(),
                ["印尼", "菲律宾", "英国", "沙特阿拉伯", "UK"],
                0.03,
                attempts,
            )

        self.assertIsNone(rental)
        self.assertEqual(calls, [("prices", "ot", country) for country in [6, 4, 16, 53]])



    async def test_codex_oauth_cancel_marks_job_and_cancels_task(self):
        from app.api import accounts as accounts_api

        class FakeTask:
            def __init__(self):
                self.cancelled = False

            def done(self):
                return False

            def cancel(self):
                self.cancelled = True

        job_id = "testjobcancel"
        task = FakeTask()
        event = __import__("asyncio").Event()
        accounts_api._OAUTH_JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "account_ids": [1],
            "current_account_id": 1,
            "current_flow": "phone",
            "current_stage": 4,
            "results": [],
            "error": "",
            "started_at": "",
            "finished_at": "",
            "cancel_event": event,
            "task": task,
        }
        try:
            snapshot = accounts_api.cancel_codex_oauth_job(job_id)

            self.assertEqual(snapshot["status"], "stopping")
            self.assertTrue(snapshot["running"])
            self.assertTrue(event.is_set())
            self.assertTrue(task.cancelled)
        finally:
            accounts_api._OAUTH_JOBS.pop(job_id, None)

    async def test_rent_smsbower_number_can_try_lowest_price_provider_first(self):
        from app.api import accounts as accounts_api

        provider_calls = []
        price_services = []

        class FakeSms:
            async def _get(self, action, **params):
                if action == "getCountries":
                    return '{"6":{"eng":"Indonesia"}}'
                if action == "getNumber":
                    provider_calls.append(params.get("providerIds"))
                    return "ACCESS_NUMBER:act_low:628123456789"
                raise AssertionError(action)

            async def get_prices(self, service, country):
                price_services.append(service)
                return '{"6":{"ot":{"expensive":{"provider_id":"91","price":"0.030","count":"2"},"cheap":{"provider_id":"12","price":"0.014","count":"1"}}}}'

            async def set_status(self, activation_id, status):
                return "ACCESS_READY"

        attempts = []
        with patch.object(accounts_api.settings, "smsbower_service", "ot"):
            rental = await accounts_api._rent_smsbower_number(
                FakeSms(),
                ["ID"],
                0.03,
                attempts,
                low_price_first=True,
            )

        self.assertEqual(provider_calls, ["12"])
        self.assertEqual(price_services, ["ot"])
        self.assertEqual(rental["provider_id"], "12")
        self.assertEqual(rental["listed_price"], "0.014")
        self.assertEqual(attempts[0]["service"], "ot")

    async def test_rent_smsbower_number_falls_back_to_web_generic_endpoint(self):
        from app.api import accounts as accounts_api

        calls = []
        price_services = []

        class FakeSms:
            async def _get(self, action, **params):
                calls.append((action, params))
                if action == "getCountries":
                    return '{"6":{"eng":"Indonesia"}}'
                if action == "getNumber":
                    if params.get("providerIds"):
                        return "MAX_RETRIES"
                    return "ACCESS_NUMBER:act-generic:628123456789"
                raise AssertionError(action)

            async def get_prices(self, service, country):
                price_services.append(service)
                return '{"6":{"ot":{"provider":{"provider_id":"12","price":"0.014","count":"1"}}}}'

            async def set_status(self, activation_id, status):
                return "ACCESS_READY" if status == 1 else "ACCESS_CANCEL"

        attempts = []
        with patch.object(accounts_api.settings, "smsbower_service", "ot"):
            rental = await accounts_api._rent_smsbower_number(
                FakeSms(),
                ["ID"],
                0.014,
                attempts,
            )

        get_number_calls = [params for action, params in calls if action == "getNumber"]
        self.assertEqual(len(get_number_calls), 2)
        self.assertEqual(price_services, ["ot"])
        self.assertTrue(all(call["service"] == "ot" for call in get_number_calls))
        self.assertEqual(get_number_calls[0]["providerIds"], "12")
        self.assertNotIn("providerIds", get_number_calls[1])
        self.assertEqual(rental["activation_id"], "act-generic")
        self.assertEqual(rental["provider_id"], "")
        self.assertEqual(attempts[-1]["source"], "generic_fallback")

    async def test_refresh_oauth_from_profile_writes_tokens_back_to_account(self):
        from app.api import accounts as accounts_api

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            account = Account(
                phone="mail_1",
                email="profile@example.com",
                password="password-1",
                profile_path="D:/profiles/worker_reg_1",
                proxy="http://127.0.0.1:7890",
                access_token="old-web-access",
                refresh_token="",
                id_token="",
                account_id="",
                user_id="",
                plan_type="free",
                mail_provider="gmail",
            )
            db.add(account)
            db.commit()
            db.refresh(account)

            oauth_result = {
                "access_token": "new-oauth-access",
                "refresh_token": "new-oauth-refresh",
                "id_token": "new-oauth-id",
                "expires_at": 1234567890,
                "account_id": "acc_oauth",
                "user_id": "user_oauth",
                "plan_type": "plus",
                "email": "profile@example.com",
            }

            with (
                patch.object(accounts_api.Registrator, "oauth_from_profile", new=AsyncMock(return_value=oauth_result)) as oauth,
                patch.object(accounts_api.settings, "oauth_proxy", ""),  # 未配置独立实例时回退 account.proxy
            ):
                payload = accounts_api.OAuthRefreshBody(headless=False)
                item = await accounts_api.refresh_oauth_from_profile(account.id, payload, db)

            oauth.assert_awaited_once_with(
                proxy="http://127.0.0.1:7890",
                profile_path="D:/profiles/worker_reg_1",
                headless=False,
                email="profile@example.com",
                password="password-1",
                totp_secret="",
            )
            db.refresh(account)
            self.assertEqual(account.access_token, "new-oauth-access")
            self.assertEqual(account.refresh_token, "new-oauth-refresh")
            self.assertEqual(account.id_token, "new-oauth-id")
            self.assertEqual(account.account_id, "acc_oauth")
            self.assertEqual(account.user_id, "user_oauth")
            self.assertEqual(account.plan_type, "plus")
            self.assertTrue(item.has_refresh_token)
            self.assertTrue(item.has_access_token)
            self.assertIsNotNone(item.access_token_masked)
            self.assertIsNotNone(item.refresh_token_masked)
            self.assertEqual(account.oauth_refresh_status, "success")
            self.assertEqual(account.oauth_refresh_error, "")
            self.assertIsNotNone(account.oauth_refreshed_at)
        finally:
            db.close()

    def test_codex_oauth_job_allows_disabling_phone_fallback(self):
        from app.api.accounts import CodexOAuthJobBody

        self.assertTrue(CodexOAuthJobBody(account_ids=[1]).allow_phone_fallback)
        self.assertFalse(CodexOAuthJobBody(account_ids=[1], allow_phone_fallback=False).allow_phone_fallback)
