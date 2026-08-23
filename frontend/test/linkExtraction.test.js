import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_LINK_EXTRACTION_FORM,
  buildLinkExtractionPayload,
  linkJobActive,
  normalizeLinkForm,
} from "../src/pages/linkExtractionUtils.js";

test("buildLinkExtractionPayload deduplicates account ids and keeps extraction options", () => {
  const payload = buildLinkExtractionPayload([7, "7", 0, 8], { country: "ph", payment_method: "gopay", concurrency: "4" });
  assert.deepEqual(payload.account_ids, [7, 8]);
  assert.equal(payload.country, "PH");
  assert.equal(payload.payment_method, "gopay");
  assert.equal(payload.concurrency, 4);
  assert.equal(payload.apply_checkout_update, true);
  assert.equal(Object.hasOwn(payload, "access_token"), false);
});

test("normalizeLinkForm clamps concurrency and restores safe defaults", () => {
  const normalized = normalizeLinkForm({ concurrency: 99, payment_method: "unknown", apply_checkout_update: false });
  assert.equal(normalized.concurrency, 5);
  assert.equal(normalized.payment_method, "paypal");
  assert.equal(normalized.apply_checkout_update, false);
  assert.equal(normalized.country, DEFAULT_LINK_EXTRACTION_FORM.country);
});

test("linkJobActive only treats pending and running jobs as active", () => {
  assert.equal(linkJobActive("pending"), true);
  assert.equal(linkJobActive("running"), true);
  assert.equal(linkJobActive("succeeded"), false);
  assert.equal(linkJobActive("failed"), false);
});
