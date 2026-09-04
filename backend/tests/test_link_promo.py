import unittest
from dataclasses import replace
from types import SimpleNamespace

from app.services.payment_link_extractor.application import extract_payment_link
from app.services.payment_link_extractor.checkout import fetch_promo_campaign, resolve_promo_campaign
from app.services.payment_link_extractor.config import billing_for_country
from app.services.payment_link_extractor.errors import ConfigurationError
from app.services.payment_link_extractor.models import ExtractionConfig


def _catalog_payload(campaign_id: str | None) -> dict:
    plus = {"id": campaign_id, "title": "1 month free"} if campaign_id else {}
    return {
        "accounts": {
            "default": {
                "eligible_promo_campaigns": {"plus": plus} if plus else {},
                "entitlement": {"trial": None},
            }
        }
    }


def _session_with(payload: dict, status_code: int = 200):
    def request(method, url, **kwargs):
        return SimpleNamespace(status_code=status_code, text=str(payload), json=lambda: payload)

    return SimpleNamespace(request=request)


def _config() -> ExtractionConfig:
    return ExtractionConfig(access_token="at", checkout_proxy="http://p", update_proxy="http://p")


class PromoCampaignCatalogTests(unittest.TestCase):
    def test_reads_account_specific_campaign_id(self):
        session = _session_with(_catalog_payload("plus-1-month-free-id-sept"))
        self.assertEqual(fetch_promo_campaign(_config(), session, None), "plus-1-month-free-id-sept")

    def test_empty_catalog_returns_empty(self):
        session = _session_with(_catalog_payload(None))
        self.assertEqual(fetch_promo_campaign(_config(), session, None), "")

    def test_http_error_returns_empty_not_raise(self):
        session = _session_with({}, status_code=403)
        self.assertEqual(fetch_promo_campaign(_config(), session, None), "")

    def test_nested_account_campaign_fallback(self):
        payload = {
            "accounts": {
                "abc": {"eligible_promo_campaigns": {}},
                "def": {"eligible_promo_campaigns": {"plus": {"campaign_id": "camp-42"}}},
            }
        }
        session = _session_with(payload)
        self.assertEqual(fetch_promo_campaign(_config(), session, None), "camp-42")

    def test_explicit_campaign_id_skips_catalog(self):
        config = replace(_config(), promo_campaign_id="camp-explicit")
        self.assertEqual(resolve_promo_campaign(config, _session_with({}), None), "camp-explicit")


class RequireZeroFastFailTests(unittest.TestCase):
    def test_extract_fails_fast_without_campaign(self):
        catalog = _session_with(_catalog_payload(None))

        class _Chatgpt:
            proxies: dict = {}

            def request(self, method, url, **kwargs):
                return catalog.request(method, url, **kwargs)

            def close(self):
                pass

        class _Factory:
            def chatgpt(self, config, proxy):
                return _Chatgpt()

            def stripe(self, config):
                raise AssertionError("0 元快速失败不应触达 Stripe 段")

        config = ExtractionConfig(
            access_token="at",
            checkout_proxy="http://p",
            update_proxy="http://p",
            country="ID",
            payment_method="gopay",
            require_zero_amount=True,
        )
        with self.assertRaises(ConfigurationError) as ctx:
            extract_payment_link(config, transport_factory=_Factory())
        self.assertIn("没有可用的 plus 优惠活动", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
