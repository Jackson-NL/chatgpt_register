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
from app.services.link_extraction import LinkExtractionService, _classify_failure
from app.services.link_proxies import (
    apply_proxy_region,
    is_cliproxy_session,
    proxy_region,
    rotate_proxy_sid,
)
from app.services.payment_link_extractor.errors import (
    ConfigurationError,
    ExtractionCancelled,
    NetworkError,
    ProtocolError,
)
from app.services.payment_link_extractor.models import PaymentLinkResult
from app.services.payment_link_extractor.config import billing_for_country
from app.services.payment_link_extractor.transport import normalize_proxy_url


class LinkProxyHelpersTests(unittest.TestCase):
    CLIPROXY = "http://qq3d1222947-region-Rand-sid-mA9UyxmG-t-5:nhedctnw@sg.cliproxy.io:443"

    def test_rotate_sid_changes_session(self):
        first = rotate_proxy_sid(self.CLIPROXY)
        second = rotate_proxy_sid(first)
        self.assertIn("@sg.cliproxy.io:443", first)
        self.assertNotEqual(first.split("-sid-")[1], second.split("-sid-")[1])
        self.assertIn("-t-5", first)

    def test_apply_region_overrides_rand(self):
        rewritten = apply_proxy_region(self.CLIPROXY, "id")
        self.assertIn("-region-ID-", rewritten)
        self.assertEqual(proxy_region(rewritten), "ID")

    def test_non_cliproxy_passthrough(self):
        plain = "http://user:pass@example.com:8080"
        self.assertEqual(rotate_proxy_sid(plain), plain)
        self.assertEqual(apply_proxy_region(plain, "ID"), plain)
        self.assertFalse(is_cliproxy_session(plain))

    def test_classify_failure_categories(self):
        self.assertEqual(_classify_failure(ProtocolError(502, "Stripe init failed: boom")), "stripe")
        self.assertEqual(_classify_failure(ProtocolError(403, "checkout create failed: unusual activity")), "risk")
        self.assertEqual(_classify_failure(ProtocolError(401, "token expired")), "fatal")
        self.assertEqual(_classify_failure(ConfigurationError("bad country")), "fatal")
        self.assertEqual(_classify_failure(ProtocolError(409, "promo eligibility rejected: state=ineligible")), "fatal")
        self.assertEqual(_classify_failure(ProtocolError(403, 'checkout/update failed: {"detail":"This promotion is not available."}')), "promo_region")
        self.assertEqual(_classify_failure(NetworkError("checkout", "timed out")), "network")


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

    async def test_retry_rotates_cliproxy_sid_then_succeeds(self):
        seen_configs = []

        def extractor(config, *, cancel_event, stage_callback, log_callback):
            seen_configs.append(config.checkout_proxy)
            if len(seen_configs) < 3:
                raise ProtocolError(403, "checkout create failed: unusual activity")
            return self._result(config)

        proxy = "http://qq3d1222947-region-Rand-sid-AAAA1111-t-5:pw@sg.cliproxy.io:443"
        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(
            SimpleNamespace(account_ids=[1], country="GB", payment_method="paypal", concurrency=1, checkout_proxy=proxy),
            db,
        )
        db.close()
        service.start_job(job.id)
        await module._JOBS[job.id]

        db = self.session_factory()
        self.assertEqual(db.get(LinkExtractionItem, 1).status, "succeeded")
        self.assertEqual(len(seen_configs), 3)
        sids = {config.split("-sid-", 1)[1].split(":")[0] for config in seen_configs}
        self.assertEqual(len(sids), 3, "每次重试都应轮换出新的 cliproxy sid")
        db.close()

    async def test_stripe_failure_switches_to_browser_factory(self):
        calls = []
        sentinel_factory = object()

        def extractor(config, *, cancel_event, stage_callback, log_callback, transport_factory=None):
            calls.append(transport_factory)
            if len(calls) == 1:
                raise ProtocolError(502, "Stripe init failed: no setup_intent")
            return self._result(config)

        with patch.object(module, "BrowserExtractionContext") as context_mock:
            context_mock.return_value.__enter__.return_value = sentinel_factory
            service = LinkExtractionService(extractor=extractor)
            db = self.session_factory()
            job = await service.create_job(
                SimpleNamespace(account_ids=[1], country="GB", payment_method="paypal", concurrency=1),
                db,
            )
            db.close()
            service.start_job(job.id)
            await module._JOBS[job.id]

        db = self.session_factory()
        self.assertEqual(db.get(LinkExtractionItem, 1).status, "succeeded")
        db.close()
        self.assertEqual(calls, [None, sentinel_factory])

    async def test_fatal_error_does_not_retry(self):
        attempts = []

        def extractor(config, *, cancel_event, stage_callback, log_callback):
            attempts.append(1)
            raise ProtocolError(409, "promo eligibility rejected: state=ineligible")

        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(
            SimpleNamespace(account_ids=[1], country="GB", payment_method="paypal", concurrency=1, max_attempts=5),
            db,
        )
        db.close()
        service.start_job(job.id)
        await module._JOBS[job.id]

        db = self.session_factory()
        row = db.get(LinkExtractionItem, 1)
        self.assertEqual(row.status, "failed")
        self.assertIn("promo eligibility", row.error)
        db.close()
        self.assertEqual(len(attempts), 1)

    async def test_require_zero_amount_fails_nonzero_result(self):
        def extractor(config, *, cancel_event, stage_callback, log_callback):
            return self._result(config)

        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(
            SimpleNamespace(account_ids=[1], country="GB", payment_method="paypal", concurrency=1, require_zero_amount=True),
            db,
        )
        db.close()
        service.start_job(job.id)
        await module._JOBS[job.id]

        db = self.session_factory()
        row = db.get(LinkExtractionItem, 1)
        self.assertEqual(row.status, "failed")
        self.assertIn("优惠后金额非 0", row.error)
        db.close()

    async def test_region_rewrite_applied_per_attempt(self):
        seen = []

        def extractor(config, *, cancel_event, stage_callback, log_callback):
            seen.append((config.checkout_proxy, config.update_proxy))
            return self._result(config)

        proxy = "http://qq3d1222947-region-Rand-sid-AAAA1111-t-5:pw@sg.cliproxy.io:443"
        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(
            SimpleNamespace(
                account_ids=[1],
                country="ID",
                payment_method="gopay",
                concurrency=1,
                checkout_proxy=proxy,
                checkout_region="ID",
                update_region="TH",
                rotate_proxy=False,
            ),
            db,
        )
        db.close()
        service.start_job(job.id)
        await module._JOBS[job.id]

        checkout_proxy, update_proxy = seen[0]
        self.assertIn("-region-ID-", checkout_proxy)
        self.assertIn("-region-TH-", update_proxy)
        self.assertNotEqual(
            checkout_proxy.split("-sid-")[1],
            update_proxy.split("-sid-")[1],
            "update 出口应从 checkout 派生并使用独立 sid",
        )

    async def test_promo_region_fallback_switches_update_region(self):
        seen = []

        def extractor(config, *, cancel_event, stage_callback, log_callback):
            seen.append(config.update_proxy)
            if len(seen) == 1:
                raise ProtocolError(403, 'checkout/update failed: {"detail":"This promotion is not available."}')
            return self._result(config)

        proxy = "http://qq3d1222947-region-Rand-sid-AAAA1111-t-5:pw@sg.cliproxy.io:443"
        service = LinkExtractionService(extractor=extractor)
        db = self.session_factory()
        job = await service.create_job(
            SimpleNamespace(
                account_ids=[1],
                country="ID",
                payment_method="gopay",
                concurrency=1,
                checkout_proxy=proxy,
                checkout_region="ID",
                update_region="TH",
                rotate_proxy=False,
            ),
            db,
        )
        db.close()
        service.start_job(job.id)
        await module._JOBS[job.id]

        db = self.session_factory()
        self.assertEqual(db.get(LinkExtractionItem, 1).status, "succeeded")
        db.close()
        self.assertEqual(len(seen), 2)
        self.assertIn("-region-TH-", seen[0], "第一次 update 走配置的 TH 出口")
        self.assertIn("-region-ID-", seen[1], "被拒后回退 checkout 同出口（ID）")


if __name__ == "__main__":
    unittest.main()
