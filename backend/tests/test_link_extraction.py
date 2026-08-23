import asyncio
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Account, LinkExtractionItem
from app.services import link_extraction as module
from app.services.link_extraction import LinkExtractionService
from app.services.payment_link_extractor.errors import ExtractionCancelled
from app.services.payment_link_extractor.models import PaymentLinkResult
from app.services.payment_link_extractor.config import billing_for_country
from app.services.payment_link_extractor.transport import normalize_proxy_url


class LinkExtractionServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.original_session_local = module.SessionLocal
        module.SessionLocal = self.session_factory
        db = self.session_factory()
        db.add_all(
            [
                Account(id=1, phone="10001", email="one@example.com", access_token="AT_ONE", proxy="http://proxy"),
                Account(id=2, phone="10002", email="two@example.com", access_token="", proxy="http://proxy"),
            ]
        )
        db.commit()
        db.close()

    def tearDown(self):
        module._JOBS.clear()
        module._CANCEL_EVENTS.clear()
        module.SessionLocal = self.original_session_local
        self.engine.dispose()

    @staticmethod
    def _result(config):
        return PaymentLinkResult(
            checkout_session_id="cs_test",
            session_kind="stripe_checkout",
            payment_method=config.payment_method,
            billing_country=config.country,
            currency="GBP",
            amount_due=20.0,
            amount_due_minor=2000,
            billing=billing_for_country(config.country),
            provider_url="https://pay.example/redirect",
            provider_field="paypal_url",
            provider_value="https://paypal.example/redirect",
        )

    async def test_create_job_does_not_return_or_log_access_token(self):
        service = LinkExtractionService(extractor=self._result)
        db = self.session_factory()
        job = await service.create_job(SimpleNamespace(account_ids=[1, 2], country="GB", payment_method="paypal", concurrency=2), db)
        db.close()

        listing = service.list_accounts(self.session_factory(), q="", has_token=True, page=1, page_size=50)
        self.assertEqual([item["id"] for item in listing["items"]], [1])
        self.assertNotIn("access_token", listing["items"][0])
        self.assertEqual(job.pending, 1)
        self.assertEqual(job.failed, 1)

    def test_normalizes_socks5_to_remote_dns_variant(self):
        value = normalize_proxy_url("socks5://user:pass@example.com:443")
        self.assertEqual(value, "socks5h://user:pass@example.com:443")

    async def test_mock_task_persists_result_and_completes(self):
        def extractor(config, *, cancel_event, stage_callback, log_callback):
            stage_callback("stripe_init")
            log_callback("Stripe HTTP 200")
            return self._result(config)

        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(SimpleNamespace(account_ids=[1], country="GB", payment_method="paypal", concurrency=1), db)
        db.close()
        service.start_job(job.id)
        await module._JOBS[job.id]

        db = self.session_factory()
        row = db.get(LinkExtractionItem, 1)
        saved_job = db.get(module.LinkExtractionJob, job.id)
        self.assertEqual(saved_job.status, "succeeded")
        self.assertEqual(row.status, "succeeded")
        self.assertEqual(row.progress, 100)
        self.assertEqual(row.paypal_url, "https://paypal.example/redirect")
        self.assertNotIn("AT_ONE", saved_job.logs_json)
        db.close()

    async def test_cancel_stops_cooperative_extractor(self):
        started = threading.Event()

        def extractor(config, *, cancel_event, stage_callback, log_callback):
            started.set()
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise ExtractionCancelled("cancelled")

        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(SimpleNamespace(account_ids=[1], country="GB", payment_method="paypal", concurrency=1), db)
        db.close()
        service.start_job(job.id)
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        canceled = await service.cancel_job(job.id)
        await module._JOBS[job.id]

        self.assertEqual(canceled.status, "canceled")
        db = self.session_factory()
        row = db.get(LinkExtractionItem, 1)
        self.assertEqual(row.status, "canceled")
        self.assertEqual(db.get(module.LinkExtractionJob, job.id).status, "canceled")
        db.close()


if __name__ == "__main__":
    unittest.main()
