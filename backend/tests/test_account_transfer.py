import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import accounts as accounts_api
from app.db import Base
from app.models import Account


class AccountTransferFormatTests(unittest.TestCase):
    def test_import_preserves_phone_number_for_new_account(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        try:
            result = accounts_api.import_account_records(
                session,
                [
                    {
                        "email": "person@example.com",
                        "phone": "15550001111",
                        "access_token": "access-token",
                    }
                ],
                "skip",
            )

            imported = session.scalar(select(Account).where(Account.email == "person@example.com"))
            self.assertEqual(result["success"], 1)
            self.assertIsNotNone(imported)
            self.assertEqual(imported.phone, "15550001111")
        finally:
            session.close()

    def test_import_overwrite_updates_phone_number(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        try:
            session.add(
                Account(
                    phone="15550001111",
                    email="person@example.com",
                    access_token="old-token",
                )
            )
            session.commit()

            result = accounts_api.import_account_records(
                session,
                [
                    {
                        "email": "person@example.com",
                        "phone": "15550002222",
                        "access_token": "new-token",
                    }
                ],
                "overwrite",
            )

            imported = session.scalar(select(Account).where(Account.email == "person@example.com"))
            self.assertEqual(result["success"], 1)
            self.assertEqual(imported.phone, "15550002222")
            self.assertEqual(imported.access_token, "new-token")
        finally:
            session.close()

    def test_parse_cpa_object_maps_credentials_and_account_metadata(self):
        records = accounts_api.parse_account_transfer_content(
            json.dumps(
                {
                    "id_token": "id-token",
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "account_id": "chat-account",
                    "email": "person@example.com",
                    "account_password": "password",
                    "two_factor_secret": "JBSWY3DPEHPK3PXP",
                    "phone_number": "15551234567",
                    "plan_type": "plus",
                    "type": "codex",
                    "expired": "2026-08-20T00:00:00Z",
                }
            ),
            "cpa",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["email"], "person@example.com")
        self.assertEqual(records[0]["access_token"], "access-token")
        self.assertEqual(records[0]["account_id"], "chat-account")
        self.assertEqual(records[0]["totp_secret"], "JBSWY3DPEHPK3PXP")

    def test_parse_sub2api_batch_reads_credentials(self):
        records = accounts_api.parse_account_transfer_content(
            json.dumps(
                {
                    "type": "sub2api-data",
                    "version": 1,
                    "accounts": [
                        {
                            "name": "person@example.com",
                            "platform": "openai",
                            "type": "oauth",
                            "credentials": {
                                "access_token": "access-token",
                                "refresh_token": "refresh-token",
                                "id_token": "id-token",
                                "email": "person@example.com",
                                "chatgpt_account_id": "chat-account",
                                "chatgpt_user_id": "chat-user",
                                "plan_type": "pro",
                            },
                        }
                    ],
                }
            ),
            "sub2api",
        )

        self.assertEqual(records[0]["email"], "person@example.com")
        self.assertEqual(records[0]["user_id"], "chat-user")
        self.assertEqual(records[0]["plan_type"], "pro")

    def test_export_payload_matches_cpa_and_sub2api_shapes(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        try:
            account = Account(
                phone="15550001111",
                email="person@example.com",
                password="password",
                access_token="access-token",
                refresh_token="refresh-token",
                id_token="id-token",
                account_id="chat-account",
                user_id="chat-user",
                plan_type="plus",
                totp_secret="JBSWY3DPEHPK3PXP",
                created_at=datetime(2026, 8, 17),
            )
            session.add(account)
            session.commit()
            session.refresh(account)

            cpa = accounts_api.build_account_transfer_payload([account], "cpa")
            sub2api = accounts_api.build_account_transfer_payload([account], "sub2api")

            self.assertEqual(cpa["type"], "codex")
            self.assertEqual(cpa["refresh_token"], "refresh-token")
            self.assertEqual(sub2api["type"], "sub2api-data")
            self.assertEqual(sub2api["version"], 1)
            self.assertEqual(sub2api["accounts"][0]["credentials"]["chatgpt_account_id"], "chat-account")
        finally:
            session.close()

    def test_sub2api_export_includes_oauth_client_and_token_expiry(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        try:
            account = Account(
                phone="15550001111",
                email="person@example.com",
                access_token="header.eyJleHAiOjE4MDAwMDAwMDB9.signature",
                refresh_token="refresh-token",
                created_at=datetime(2026, 8, 17),
            )
            session.add(account)
            session.commit()
            session.refresh(account)

            payload = accounts_api.build_account_transfer_payload([account], "sub2api")
            credentials = payload["accounts"][0]["credentials"]

            self.assertEqual(credentials["client_id"], "app_EMoamEEZ73f0CkXaXp7hrann")
            self.assertEqual(credentials["expires_at"], "2027-01-15T08:00:00.000Z")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
