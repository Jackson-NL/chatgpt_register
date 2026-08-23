"""Codex OAuth 资格策略与后端强制拦截测试。

约束：任何测试输出/断言不得包含邮箱 JWT、密码或 token 明文。
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base
from app.models import Account, Registration
from app.services.oauth_policy import (
    BLOCK_HAS_REFRESH_TOKEN,
    BLOCK_NO_PROFILE,
    BLOCK_NOT_GMAIL,
    oauth_block_reason,
    oauth_eligibility,
)


def _db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _account(**overrides) -> Account:
    values = {
        "phone": "mail_reg_1",
        "email": "someone@example.com",
        "profile_path": "D:/profiles/reg_1",
        "refresh_token": "",
        "mail_provider": "gmail",
    }
    values.update(overrides)
    return Account(**values)


class OAuthPolicyTests(unittest.TestCase):
    def test_gmail_with_profile_and_no_refresh_token_is_allowed(self):
        self.assertEqual(oauth_block_reason(_account()), "")
        self.assertTrue(oauth_eligibility(_account())["oauth_eligible"])

    def test_cf_temp_email_is_rejected(self):
        reason = oauth_block_reason(_account(mail_provider="cf_temp_email"))
        self.assertEqual(reason, BLOCK_NOT_GMAIL)

    def test_outlook_is_rejected(self):
        reason = oauth_block_reason(_account(mail_provider="outlook"))
        self.assertEqual(reason, BLOCK_NOT_GMAIL)

    def test_unknown_is_rejected(self):
        for provider in ("unknown", "", None):
            account = _account(mail_provider=provider or "unknown")
            self.assertNotEqual(oauth_block_reason(account), "")

    def test_missing_profile_is_rejected(self):
        self.assertEqual(oauth_block_reason(_account(profile_path="")), BLOCK_NO_PROFILE)

    def test_existing_refresh_token_is_rejected(self):
        self.assertEqual(oauth_block_reason(_account(refresh_token="rt-value")), BLOCK_HAS_REFRESH_TOKEN)

    def test_gmail_domain_alone_does_not_allow_unknown_source(self):
        account = _account(email="victim@gmail.com", mail_provider="unknown")
        self.assertFalse(oauth_eligibility(account)["oauth_eligible"])


class OAuthEndpointEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_gmail_and_cf_job_is_rejected_without_creating_job(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        gmail = _account(phone="mail_reg_1")
        cf = _account(phone="mail_reg_2", mail_provider="cf_temp_email")
        db.add_all([gmail, cf])
        db.commit()
        original_session_local = accounts_api.SessionLocal
        accounts_api.SessionLocal = sessionmaker(bind=db.get_bind())
        jobs_before = set(accounts_api._OAUTH_JOBS)
        active_before = accounts_api._ACTIVE_OAUTH_JOB_ID
        try:
            payload = accounts_api.CodexOAuthJobBody(account_ids=[gmail.id, cf.id])
            with self.assertRaises(HTTPException) as ctx:
                await accounts_api.create_codex_oauth_job(payload)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertIn(f"acc_{cf.id}", str(ctx.exception.detail))
            self.assertIn("Gmail", str(ctx.exception.detail))
            # 整体拒绝：不允许产生部分 job。
            self.assertEqual(set(accounts_api._OAUTH_JOBS), jobs_before)
            self.assertEqual(accounts_api._ACTIVE_OAUTH_JOB_ID, active_before)
        finally:
            accounts_api.SessionLocal = original_session_local
            db.close()

    async def test_job_with_unknown_source_account_is_rejected(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        legacy = _account(phone="mail_reg_9", mail_provider="unknown")
        db.add(legacy)
        db.commit()
        original_session_local = accounts_api.SessionLocal
        accounts_api.SessionLocal = sessionmaker(bind=db.get_bind())
        try:
            payload = accounts_api.CodexOAuthJobBody(account_ids=[legacy.id])
            with self.assertRaises(HTTPException) as ctx:
                await accounts_api.create_codex_oauth_job(payload)
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            accounts_api.SessionLocal = original_session_local
            db.close()

    async def test_single_refresh_endpoint_rejects_non_gmail_and_missing_account(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        cf = _account(phone="mail_reg_2", mail_provider="outlook")
        db.add(cf)
        db.commit()
        try:
            payload = accounts_api.OAuthRefreshBody(headless=False)
            with self.assertRaises(HTTPException) as blocked:
                await accounts_api.refresh_oauth_from_profile(cf.id, payload, db)
            self.assertEqual(blocked.exception.status_code, 403)
            with self.assertRaises(HTTPException) as missing:
                await accounts_api.refresh_oauth_from_profile(999999, payload, db)
            self.assertEqual(missing.exception.status_code, 404)
        finally:
            db.close()

    async def test_auto_phone_endpoint_rejects_non_gmail(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        cf = _account(phone="mail_reg_3", mail_provider="cf_temp_email")
        db.add(cf)
        db.commit()
        try:
            payload = accounts_api.OAuthAutoPhoneBody(headless=True)
            with self.assertRaises(HTTPException) as ctx:
                await accounts_api.auto_oauth_phone_from_profile(cf.id, payload, db)
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            db.close()

    async def test_complete_phone_endpoint_rejects_non_gmail(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        cf = _account(phone="mail_reg_4", mail_provider="cf_temp_email")
        db.add(cf)
        db.commit()
        try:
            payload = accounts_api.OAuthPhoneCompleteBody(
                headless=True, activation_id="act", phone="+15550001111",
                country_iso="US", dialing_code="1",
            )
            with self.assertRaises(HTTPException) as ctx:
                await accounts_api.complete_oauth_phone_from_profile(cf.id, payload, db)
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            db.close()

    async def test_dry_run_endpoint_rejects_non_gmail(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        cf = _account(phone="mail_reg_5", mail_provider="cf_temp_email")
        db.add(cf)
        db.commit()
        try:
            payload = accounts_api.OAuthPhoneDryRunBody(headless=True)
            with self.assertRaises(HTTPException) as ctx:
                await accounts_api.dry_run_oauth_phone_from_profile(cf.id, payload, db)
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            db.close()

    async def test_run_codex_oauth_target_revalidates_policy(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        cf = _account(phone="mail_reg_6", mail_provider="cf_temp_email")
        db.add(cf)
        db.commit()
        try:
            job = {"cancel_event": None}
            payload = accounts_api.CodexOAuthJobBody(account_ids=[cf.id])
            with self.assertRaises(accounts_api.RegisterError) as ctx:
                await accounts_api._run_codex_oauth_target(job, cf.id, payload, db)
            self.assertIn("Gmail", str(ctx.exception))
        finally:
            db.close()

    async def test_list_accounts_reports_mail_provider_and_eligibility(self):
        from app.api import accounts as accounts_api

        db = _db_session()
        gmail = _account(phone="mail_reg_1")
        cf = _account(phone="mail_reg_2", mail_provider="cf_temp_email")
        db.add_all([gmail, cf])
        db.commit()
        try:
            items = accounts_api.list_accounts(db=db)
            by_id = {item.id: item for item in items}
            self.assertEqual(by_id[gmail.id].mail_provider, "gmail")
            self.assertTrue(by_id[gmail.id].oauth_eligible)
            self.assertEqual(by_id[gmail.id].oauth_block_reason, "")
            self.assertEqual(by_id[cf.id].mail_provider, "cf_temp_email")
            self.assertFalse(by_id[cf.id].oauth_eligible)
            self.assertTrue(by_id[cf.id].oauth_block_reason)
        finally:
            db.close()


class MailProviderMigrationTests(unittest.TestCase):
    def _legacy_engine(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE accounts ("
                " id INTEGER PRIMARY KEY,"
                " phone VARCHAR(32),"
                " email VARCHAR(128) DEFAULT '',"
                " status VARCHAR(16) DEFAULT 'active')"
            ))
            conn.execute(text(
                "CREATE TABLE registrations ("
                " id INTEGER PRIMARY KEY,"
                " status VARCHAR(16) DEFAULT 'pending',"
                " account_id INTEGER,"
                " gmail_alias VARCHAR(128) DEFAULT '',"
                " gmail_mail_id VARCHAR(64) DEFAULT '')"
            ))
            conn.execute(text(
                "INSERT INTO accounts (id, phone, email) VALUES "
                "(1, 'mail_reg_1', 'a@example.com'),"
                "(2, 'mail_reg_2', 'b@example.com'),"
                "(3, 'sms_legacy', 'c@example.com')"
            ))
            conn.execute(text(
                "INSERT INTO registrations (id, status, account_id, gmail_alias, gmail_mail_id) VALUES "
                "(1, 'success', 1, 'alias1@ gmail-base', '10001'),"
                "(2, 'failed', 2, '', ''),"
                "(3, 'canceled', NULL, 'alias3@gmail-base', '')"
            ))
        return engine

    def test_migration_adds_columns_and_backfills_only_reliable_gmail_rows(self):
        from app.db import _migrate_legacy_tables

        engine = self._legacy_engine()
        _migrate_legacy_tables(engine)
        inspector = inspect(engine)
        account_cols = {c["name"] for c in inspector.get_columns("accounts")}
        reg_cols = {c["name"] for c in inspector.get_columns("registrations")}
        self.assertIn("mail_provider", account_cols)
        self.assertIn("mail_provider", reg_cols)

        with engine.connect() as conn:
            reg_providers = dict(conn.execute(text("SELECT id, mail_provider FROM registrations")).all())
            acc_providers = dict(conn.execute(text("SELECT id, mail_provider FROM accounts")).all())

        # gmail_alias + gmail_mail_id 同时存在才回填 gmail。
        self.assertEqual(reg_providers[1], "gmail")
        # 普通旧记录保持 unknown，禁止进入 OAuth。
        self.assertEqual(reg_providers[2], "unknown")
        # 只有 alias 没有 mail_id 的记录不可靠，保持 unknown。
        self.assertEqual(reg_providers[3], "unknown")
        self.assertEqual(acc_providers[1], "gmail")
        self.assertEqual(acc_providers[2], "unknown")
        self.assertEqual(acc_providers[3], "unknown")

    def test_backfilled_registration_maps_to_policy_allowed(self):
        from app.db import _migrate_legacy_tables
        from app.services.oauth_policy import normalized_mail_provider
        from sqlalchemy import select

        engine = self._legacy_engine()
        _migrate_legacy_tables(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            # 旧表结构精简，避免整行实体加载，只取回填列验证。
            gmail_value = db.execute(
                select(Registration.mail_provider).where(Registration.id == 1)
            ).scalar()
            stale_value = db.execute(
                select(Registration.mail_provider).where(Registration.id == 2)
            ).scalar()
            self.assertEqual(normalized_mail_provider(type("R", (), {"mail_provider": gmail_value})()), "gmail")
            self.assertEqual(normalized_mail_provider(type("R", (), {"mail_provider": stale_value})()), "unknown")
            # unknown 来源账号在策略下永远不允许进入 OAuth。
            self.assertNotEqual(oauth_block_reason(_account(mail_provider="unknown")), "")
        finally:
            db.close()


class RegistrationMailProviderTests(unittest.TestCase):
    def test_submit_persists_resolved_provider(self):
        from app.services import registrations as registration_service

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)

        class FakeRegistrator:
            def __init__(self, sms_client=None):
                pass

            async def register_by_email(self, **kwargs):
                return {"email": "submit@example.com"}

        with (
            patch.object(registration_service, "SessionLocal", Session),
            patch.object(registration_service, "Registrator", FakeRegistrator),
            patch.object(registration_service, "SmsbowerClient", lambda: object()),
            patch.object(registration_service, "make_profile_path", lambda name: f"D:/tmp/{name}"),
        ):
            service = registration_service.RegistrationService()

            async def exercise():
                gmail_reg_id = await service.submit(gmail_alias="alias-x", gmail_mail_id="20001")
                cf_reg_id = await service.submit()
                for task in list(registration_service._JOBS.values()):
                    task.cancel()
                if registration_service._JOBS:
                    await asyncio.gather(*registration_service._JOBS.values(), return_exceptions=True)
                registration_service._JOBS.clear()
                return gmail_reg_id, cf_reg_id

            gmail_reg_id, cf_reg_id = asyncio.run(exercise())

        db = Session()
        try:
            gmail_reg = db.get(Registration, gmail_reg_id)
            cf_reg = db.get(Registration, cf_reg_id)
            self.assertEqual(gmail_reg.mail_provider, "gmail")
            # 非 Gmail 订单使用当前生效 Provider（测试环境默认配置为非 gmail）。
            self.assertIn(cf_reg.mail_provider, {"cf_temp_email", "outlook"})
            self.assertNotEqual(cf_reg.mail_provider, "gmail")
        finally:
            db.close()

    def test_successful_registration_copies_mail_provider_to_account(self):
        from app.services import registrations as registration_service

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        reg = Registration(
            status="pending",
            proxy="http://127.0.0.1:7890",
            gmail_alias="alias-copy",
            gmail_mail_id="30001",
            mail_provider="gmail",
        )
        db.add(reg)
        db.commit()
        reg_id = reg.id

        class FakeRegistrator:
            def __init__(self, sms_client=None):
                pass

            async def register_by_email(self, **kwargs):
                return {
                    "email": "newreg@example.com",
                    "access_token": "",
                    "refresh_token": "",
                    "id_token": "",
                    "plan_type": "free",
                    "totp_secret": "",
                }

        original = (
            registration_service.Registrator,
            registration_service.SmsbowerClient,
            registration_service.make_profile_path,
            registration_service.SessionLocal,
        )
        registration_service.Registrator = FakeRegistrator
        registration_service.SmsbowerClient = lambda: object()
        registration_service.make_profile_path = lambda name: f"D:/tmp/{name}"
        registration_service.SessionLocal = Session
        try:
            asyncio.run(registration_service.RegistrationService()._run(reg_id))
        finally:
            (
                registration_service.Registrator,
                registration_service.SmsbowerClient,
                registration_service.make_profile_path,
                registration_service.SessionLocal,
            ) = original
        try:
            db.expire_all()  # _run 通过独立 session 提交，需刷新外层会话缓存
            reg = db.get(Registration, reg_id)
            account = db.get(Account, reg.account_id)
            self.assertEqual(reg.status, "success")
            self.assertEqual(account.mail_provider, "gmail")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
