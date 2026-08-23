import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_RELOGIN_PARAMS,
  buildReloginJobPayload,
  buildReloginPreviewParams,
  normalizeReloginGroupIds,
  paginateReloginRows,
  projectReloginItem,
  reloginJobActive,
  reloginStatusMeta,
} from "../src/pages/sub2apiReloginUtils.js";

test("relogin group ids are normalized and deduplicated", () => {
  assert.deepEqual(normalizeReloginGroupIds("1, 2，1 invalid"), [1, 2]);
  assert.deepEqual(buildReloginPreviewParams([1, "2", 1], false), { group_ids: [1, 2], only_error: false });
});

test("relogin job payload keeps the requested defaults and caps concurrency", () => {
  assert.deepEqual(buildReloginJobPayload({ group_ids: [7], concurrency: 9, timeout_s: "180" }), {
    group_ids: [7],
    ...DEFAULT_RELOGIN_PARAMS,
    concurrency: 5,
    timeout_s: 180,
  });
});

test("relogin item projection omits sensitive or raw fields", () => {
  const result = projectReloginItem({
    remote_account_id: "r-1",
    local_account_id: 3,
    email: "user@example.com",
    status: "failed",
    reason: "reauth_failed",
    access_token: "full-access-token",
    refresh_token: "full-refresh-token",
    password: "password",
    totp_secret: "secret",
    raw: { credentials: { password: "password" } },
  });
  assert.deepEqual(result, {
    id: undefined,
    remote_account_id: "r-1",
    local_account_id: 3,
    email: "user@example.com",
    remote_status: "",
    remote_error: "",
    status: "failed",
    reason: "reauth_failed",
    error: "",
  });
  assert.equal("access_token" in result, false);
  assert.equal("raw" in result, false);
});

test("relogin statuses distinguish active and terminal jobs", () => {
  assert.equal(reloginJobActive("running"), true);
  assert.equal(reloginJobActive("completed"), false);
  assert.equal(reloginStatusMeta("failed").color, "danger");
  assert.equal(reloginStatusMeta("canceled").label, "已停止");
});


test("relogin pagination clamps page and slices visible rows", () => {
  const rows = Array.from({ length: 55 }, (_, index) => ({ id: index + 1 }));

  assert.deepEqual(paginateReloginRows(rows, 2, 20), {
    rows: rows.slice(20, 40),
    page: 2,
    pageSize: 20,
    pages: 3,
    total: 55,
    from: 21,
    to: 40,
  });
  assert.equal(paginateReloginRows(rows, 99, 20).page, 3);
  assert.equal(paginateReloginRows(rows, -1, 20).page, 1);
});


test("relogin job payload can carry existing preview items to avoid rescanning", () => {
  const payload = buildReloginJobPayload({
    group_ids: [42],
    preview_items: [
      { remote_id: "r-1", email: "user@example.com", access_token: "hidden" },
    ],
  });

  assert.deepEqual(payload.preview_items, [{ remote_id: "r-1", email: "user@example.com" }]);
  assert.equal("access_token" in payload.preview_items[0], false);
});
