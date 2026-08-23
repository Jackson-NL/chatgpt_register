import asyncio
import unittest

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import sub2api as sub2api_api
from app.db import Base
from app.models import Account, AccountSub2APIUpload
from app.services.sub2api import Sub2APIClient


class FakeSub2APIClient:
    def __init__(self):
        self.uploaded_concurrency = None

    async def list_groups(self):
        return [{"id": 42, "name": "Codex", "platform": "openai", "status": "active"}]

    async def upload_accounts(self, accounts, group_id, concurrency=3, progress_callback=None):
        self.uploaded_concurrency = concurrency
        if progress_callback:
            for account in accounts:
                await progress_callback({"account_id": account.id, "email": account.email, "status": "success"})
        return {
            "count": len(accounts),
            "success": len(accounts),
            "failed": 0,
            "results": [{
                "account_id": account.id,
                "email": account.email,
                "remote_id": 9,
                "has_access_token": True,
                "has_refresh_token": True,
                "has_id_token": True,
                "concurrency": concurrency,
                "remote_concurrency": concurrency,
                "remote_load_factor": concurrency,
            } for account in accounts],
            "errors": [],
            "group_id": group_id,
            "concurrency": concurrency,
        }


class FakeSyncClient(Sub2APIClient):
    """复用真实 sync_upload_status 逻辑，只替换远端拉取。"""

    def __init__(self):
        super().__init__(base_url="https://sub2api.example", jwt="jwt")

    async def list_groups(self):
        return [
            {"id": 42, "name": "Codex", "platform": "openai", "status": "active"},
            {"id": 108, "name": "Claude", "platform": "openai", "status": "active"},
        ]

    async def list_accounts(self, group_ids):
        return [
            {"id": "r1", "name": "alice@example.com", "platform": "openai", "group_ids": [42, 108], "credentials_status": {"has_access_token": True, "has_refresh_token": True}},
            {"id": "r2", "name": "bob@example.com", "platform": "openai", "group_ids": [42, 108], "credentials_status": {"has_access_token": False, "has_refresh_token": False}},
        ]


class Sub2APIRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        self.session_factory = sessionmaker(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.session = sessionmaker(bind=engine)()
        self.session.add(
            Account(
                id=1,
                phone="15550001111",
                email="person@example.com",
                password="password",
                totp_secret="JBSWY3DPEHPK3PXP",
            )
        )
        self.session.commit()
        self.original_factory = sub2api_api.create_sub2api_client
        self.fake_client = FakeSub2APIClient()
        sub2api_api.create_sub2api_client = lambda: self.fake_client

    def tearDown(self):
        sub2api_api.create_sub2api_client = self.original_factory
        self.session.close()

    async def test_lists_groups(self):
        result = await sub2api_api.list_sub2api_groups()
        self.assertEqual(result[0]["id"], 42)

    async def test_upload_uses_selected_ids_and_group(self):
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42)

        result = await sub2api_api.upload_to_sub2api(payload, self.session)

        self.assertEqual(result["success"], 1)
        self.assertEqual(result["group_id"], 42)

    async def test_upload_job_reports_progress_until_completion(self):
        self.session.add(
            Account(
                id=2,
                phone="15550002222",
                email="second@example.com",
                password="password",
                totp_secret="JBSWY3DPEHPK3PXP",
            )
        )
        self.session.commit()
        original_session_local = sub2api_api.SessionLocal
        sub2api_api.SessionLocal = self.session_factory
        try:
            payload = sub2api_api.Sub2APIUploadBody(ids=[1, 2], group_id=42)
            created = await sub2api_api.create_sub2api_upload_job(payload, self.session)

            self.assertIn(created["status"], {"pending", "running", "completed"})
            state = created
            for _ in range(10):
                await asyncio.sleep(0)
                state = sub2api_api.get_sub2api_upload_job(created["job_id"])
                if state["status"] == "completed":
                    break

            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["total"], 2)
            self.assertEqual(state["processed"], 2)
            self.assertEqual(state["success"], 2)
            self.assertEqual(state["failed"], 0)
            self.assertEqual(state["result"]["group_ids"], [42])
        finally:
            task = sub2api_api._UPLOAD_JOBS.get(created["job_id"], {}).get("task") if "created" in locals() else None
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            sub2api_api.SessionLocal = original_session_local

    async def test_upload_job_returns_404_for_unknown_job(self):
        with self.assertRaises(HTTPException) as context:
            sub2api_api.get_sub2api_upload_job("missing-job")
        self.assertEqual(context.exception.status_code, 404)

    async def test_upload_uses_multiple_selected_groups(self):
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_ids=[42, 108, 42])

        result = await sub2api_api.upload_to_sub2api(payload, self.session)

        self.assertEqual(result["success"], 1)
        self.assertEqual(result["group_ids"], [42, 108])
        self.assertIsNone(result["group_id"])

    async def test_upload_defaults_concurrency_to_3(self):
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42)

        result = await sub2api_api.upload_to_sub2api(payload, self.session)

        self.assertEqual(self.fake_client.uploaded_concurrency, 3)
        self.assertEqual(result["concurrency"], 3)

    async def test_upload_passes_custom_concurrency_to_client(self):
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42, concurrency=8)

        result = await sub2api_api.upload_to_sub2api(payload, self.session)

        self.assertEqual(self.fake_client.uploaded_concurrency, 8)
        self.assertEqual(result["concurrency"], 8)

    async def test_upload_rejects_concurrency_below_range(self):
        with self.assertRaises(ValidationError):
            sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42, concurrency=0)

    async def test_upload_rejects_concurrency_above_range(self):
        with self.assertRaises(ValidationError):
            sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42, concurrency=21)

    async def test_upload_writes_persisted_status_rows(self):
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42)
        result = await sub2api_api.upload_to_sub2api(payload, self.session)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["results"][0]["upload_status"], "uploaded")
        self.assertEqual(result["results"][0]["group_ids"], [42])
        rows = self.session.query(AccountSub2APIUpload).filter(AccountSub2APIUpload.account_id == 1).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].group_id, 42)
        self.assertEqual(rows[0].status, "uploaded")
        self.assertEqual(rows[0].remote_id, "9")

    async def test_upload_respects_only_not_uploaded_filter(self):
        self.session.add(AccountSub2APIUpload(account_id=1, email="person@example.com", group_id=42, status="uploaded"))
        self.session.commit()
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42, only_not_uploaded=True)
        result = await sub2api_api.upload_to_sub2api(payload, self.session)
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["skipped"][0]["account_id"], 1)
        self.assertIn("只上传未上传", result["skipped"][0]["reason"])

    async def test_upload_request_accepts_upload_options(self):
        payload = sub2api_api.Sub2APIUploadBody(ids=[1], group_id=42, overwrite_existing=False, include_token_error=True)
        self.assertEqual(payload.overwrite_existing, False)
        self.assertEqual(payload.include_token_error, True)

    async def test_sync_endpoint_writes_status_and_returns_summary(self):
        self.session.add_all(
            [
                Account(id=2, phone="15550000002", email="alice@example.com", password="p", totp_secret="JBSWY3DPEHPK3PXP", access_token="at", refresh_token="rt", id_token="it"),
                Account(id=3, phone="15550000003", email="carol@example.com", password="p", totp_secret="JBSWY3DPEHPK3PXP", access_token="at", refresh_token="rt", id_token="it"),
            ]
        )
        self.session.commit()
        original = sub2api_api.create_sub2api_client
        sub2api_api.create_sub2api_client = lambda: FakeSyncClient()
        try:
            result = await sub2api_api.sync_sub2api_upload_status(
                sub2api_api.Sub2APIUploadStatusSyncBody(group_ids=[42, 108]),
                self.session,
            )
            # 幂等：重复同步不重复插入
            await sub2api_api.sync_sub2api_upload_status(
                sub2api_api.Sub2APIUploadStatusSyncBody(group_ids=[42, 108]),
                self.session,
            )
        finally:
            sub2api_api.create_sub2api_client = original
        self.assertEqual(result["total_local"], 3)  # person@example.com + alice + carol
        self.assertEqual(result["matched_remote"], 1)
        self.assertEqual(result["uploaded"], 2)  # alice × 2 分组
        self.assertEqual(result["not_uploaded"], 4)  # person + carol × 2 分组
        self.assertEqual(len(result["items"]), 6)
        self.assertEqual(self.session.query(AccountSub2APIUpload).count(), 6)

    async def test_sync_rejects_invalid_group_ids(self):
        original = sub2api_api.create_sub2api_client
        sub2api_api.create_sub2api_client = lambda: FakeSyncClient()
        try:
            with self.assertRaises(HTTPException) as context:
                await sub2api_api.sync_sub2api_upload_status(
                    sub2api_api.Sub2APIUploadStatusSyncBody(group_ids=[0]),
                    self.session,
                )
        finally:
            sub2api_api.create_sub2api_client = original
        self.assertEqual(context.exception.status_code, 400)

    async def test_list_upload_status_filters_paginates(self):
        self.session.add_all(
            [
                AccountSub2APIUpload(account_id=1, email="person@example.com", group_id=42, status="uploaded", remote_id="r1"),
                AccountSub2APIUpload(account_id=1, email="person@example.com", group_id=108, status="uploaded", remote_id="r1"),
                AccountSub2APIUpload(account_id=2, email="alice@example.com", group_id=42, status="token_error", remote_id="r2"),
                AccountSub2APIUpload(account_id=2, email="alice@example.com", group_id=108, status="token_error", remote_id="r2"),
            ]
        )
        self.session.commit()
        # group + status + q 组合筛选
        result = sub2api_api.list_sub2api_upload_status(group_ids="42", status="uploaded", q="person", page=1, page_size=10, db=self.session)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0].group_id, 42)
        self.assertEqual(result["items"][0].status, "uploaded")
        # 分页
        paged = sub2api_api.list_sub2api_upload_status(group_ids="42,108", status="all", page=1, page_size=2, db=self.session)
        self.assertEqual(paged["total"], 4)
        self.assertEqual(len(paged["items"]), 2)
        # status 筛选
        token = sub2api_api.list_sub2api_upload_status(status="token_error", page=1, page_size=10, db=self.session)
        self.assertEqual(token["total"], 2)
        # q 支持数字 account_id
        by_id = sub2api_api.list_sub2api_upload_status(q="2", page=1, page_size=10, db=self.session)
        self.assertEqual(by_id["total"], 2)
        self.assertTrue(all(item.account_id == 2 for item in by_id["items"]))
        exact_account = sub2api_api.list_sub2api_upload_status(account_id=1, q="r2", page=1, page_size=10, db=self.session)
        self.assertEqual(exact_account["total"], 0)
        # 非法 status 返回 400
        with self.assertRaises(HTTPException) as context:
            sub2api_api.list_sub2api_upload_status(status="bogus", db=self.session)
        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
