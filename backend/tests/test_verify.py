import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, HealthCheck
from app.services import verify as verify_module


class FakePage:
    def __init__(self, payload):
        self.url = "https://chatgpt.com/"
        self.payload = payload
        self.evaluate_script = ""

    async def goto(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None

    async def evaluate(self, script, *_args):
        self.evaluate_script = script
        return dict(self.payload)


class FakeContext:
    def __init__(self, page):
        self.pages = [page]

    async def new_page(self):
        return self.pages[0]


class FakeCamoufox:
    last_options = None
    page = None

    def __init__(self, **options):
        type(self).last_options = options

    async def __aenter__(self):
        return FakeContext(type(self).page)

    async def __aexit__(self, *_args):
        return None


class VerifyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_fetch_me_uses_persistent_profile_session(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            FakeCamoufox.page = FakePage(
                {
                    "status": 200,
                    "authenticated": True,
                    "email": "owner@example.com",
                    "user_id": "user-1",
                    "plan": "plus",
                    "body": "{}",
                }
            )
            with patch("camoufox.async_api.AsyncCamoufox", FakeCamoufox):
                result = await verify_module.browser_fetch_me(str(profile), "http://proxy")

        self.assertTrue(result["authenticated"])
        self.assertEqual(result["email"], "owner@example.com")
        self.assertEqual(FakeCamoufox.last_options["user_data_dir"], str(profile))
        self.assertTrue(FakeCamoufox.last_options["persistent_context"])
        self.assertNotIn("Authorization", FakeCamoufox.page.evaluate_script)

    async def test_verify_account_records_profile_session_and_rejects_identity_mismatch(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        db = session_factory()
        profile = tempfile.TemporaryDirectory()
        db.add(
            Account(
                id=1,
                phone="15550001111",
                email="owner@example.com",
                proxy="http://account-proxy",
                profile_path=profile.name,
            )
        )
        db.commit()
        db.close()

        original_session_local = verify_module.SessionLocal
        verify_module.SessionLocal = session_factory
        try:
            with patch.object(
                verify_module,
                "browser_fetch_me",
                new=AsyncMock(
                    return_value={
                        "status": 200,
                        "authenticated": True,
                        "email": "other@example.com",
                        "user_id": "user-2",
                        "plan": "free",
                        "duration_ms": 10,
                    }
                ),
            ) as fetch:
                result = await verify_module.VerifyService().verify_account(1)

            self.assertTrue(result["ok"])
            self.assertEqual(result["result"], "fail")
            self.assertIn("账号不匹配", result["detail"])
            fetch.assert_awaited_once_with(profile.name, "http://account-proxy")

            checked = session_factory().query(HealthCheck).one()
            self.assertEqual(checked.result, "fail")
        finally:
            verify_module.SessionLocal = original_session_local
            profile.cleanup()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
