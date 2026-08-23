import test from "node:test";
import assert from "node:assert/strict";
import { buildSub2APIUploadRequest, buildSub2APIUploadStatusQuery, classifySub2APIUploadSelection, normalizeSub2APIConcurrency, normalizeSub2APIGroupIds, normalizeSub2APIGroups, normalizeSub2APIUploadJob, normalizeSub2APIUploadSummary, selectSub2APIUploadableIds, sub2apiUploadBadge } from "../src/pages/sub2apiUtils.js";

test("normalizeSub2APIUploadJob clamps progress and preserves current account", () => {
  assert.deepEqual(normalizeSub2APIUploadJob({
    status: "running",
    total: 4,
    processed: 2,
    success: 1,
    failed: 1,
    current_account_id: 8,
    current_email: "person@example.com",
  }), {
    status: "running",
    total: 4,
    processed: 2,
    success: 1,
    failed: 1,
    progress: 50,
    current_account_id: 8,
    current_email: "person@example.com",
    error: "",
    result: null,
  });
});

test("normalizeSub2APIGroups keeps active OpenAI groups and supports response envelopes", () => {
  const groups = normalizeSub2APIGroups({
    data: [
      { id: 2, name: "Claude", platform: "anthropic", status: "active" },
      { id: 42, name: "Codex", platform: "openai", status: "active" },
      { id: 43, name: "Disabled", platform: "openai", status: "disabled" },
    ],
  });

  assert.deepEqual(groups, [{ id: 42, name: "Codex", platform: "openai", status: "active" }]);
});

test("buildSub2APIUploadRequest sends ids, group and normalized concurrency", () => {
  const request = buildSub2APIUploadRequest([7, 8], ["42", "108"]);

  assert.deepEqual(request, { ids: [7, 8], group_ids: [42, 108], concurrency: 3 });
  assert.equal(Object.hasOwn(request, "password"), false);
  assert.equal(Object.hasOwn(request, "totp_secret"), false);
});

test("normalizeSub2APIGroupIds deduplicates comma-separated target groups", () => {
  assert.deepEqual(normalizeSub2APIGroupIds("42, 108，42 invalid"), [42, 108]);
});

test("buildSub2APIUploadRequest preserves all explicitly selected target groups", () => {
  const request = buildSub2APIUploadRequest([9], [42, 108]);

  assert.deepEqual(request.group_ids, [42, 108]);
  assert.deepEqual(request.ids, [9]);
});

test("normalizeSub2APIConcurrency returns valid values as-is", () => {
  assert.equal(normalizeSub2APIConcurrency(1), 1);
  assert.equal(normalizeSub2APIConcurrency(20), 20);
  assert.equal(normalizeSub2APIConcurrency("8"), 8);
});

test("normalizeSub2APIConcurrency falls back to 3 for out-of-range values", () => {
  assert.equal(normalizeSub2APIConcurrency(0), 3);
  assert.equal(normalizeSub2APIConcurrency(21), 3);
  assert.equal(normalizeSub2APIConcurrency(-5), 3);
});

test("normalizeSub2APIConcurrency falls back to 3 for invalid values", () => {
  assert.equal(normalizeSub2APIConcurrency(undefined), 3);
  assert.equal(normalizeSub2APIConcurrency(null), 3);
  assert.equal(normalizeSub2APIConcurrency("abc"), 3);
  assert.equal(normalizeSub2APIConcurrency(1.5), 3);
  assert.equal(normalizeSub2APIConcurrency(NaN), 3);
  assert.equal(normalizeSub2APIConcurrency(undefined, 7), 7);
});

test("buildSub2APIUploadRequest includes normalized concurrency", () => {
  const request = buildSub2APIUploadRequest([7, 8], ["42", "108"], "8");

  assert.deepEqual(request, { ids: [7, 8], group_ids: [42, 108], concurrency: 8 });
});

test("buildSub2APIUploadRequest defaults concurrency to 3 when omitted", () => {
  const request = buildSub2APIUploadRequest([7], [42]);

  assert.deepEqual(request, { ids: [7], group_ids: [42], concurrency: 3 });
});


test("selectSub2APIUploadableIds keeps selected accounts that have complete OAuth tokens", () => {
  const accounts = [
    { id: 7, has_access_token: true, has_refresh_token: true, has_id_token: true },
    { id: 8, has_access_token: true, has_refresh_token: false, has_id_token: true },
    { id: 9, has_access_token: true, has_refresh_token: true, has_id_token: true },
  ];

  assert.deepEqual(selectSub2APIUploadableIds(accounts, [9, 8, 7, 404]), {
    ids: [9, 7],
    skipped: [8, 404],
  });
});

test("buildSub2APIUploadRequest carries only_not_uploaded / overwrite_existing / include_token_error options", () => {
  const request = buildSub2APIUploadRequest([7, 8], [42, 108], 5, {
    onlyNotUploaded: true,
    overwriteExisting: false,
    includeTokenError: true,
  });

  assert.deepEqual(request, {
    ids: [7, 8],
    group_ids: [42, 108],
    concurrency: 5,
    only_not_uploaded: true,
    overwrite_existing: false,
    include_token_error: true,
  });
});

test("buildSub2APIUploadRequest omits upload options when not configured", () => {
  const request = buildSub2APIUploadRequest([7], [42]);
  assert.deepEqual(request, { ids: [7], group_ids: [42], concurrency: 3 });
  assert.equal(Object.hasOwn(request, "only_not_uploaded"), false);
  assert.equal(Object.hasOwn(request, "overwrite_existing"), false);
  assert.equal(Object.hasOwn(request, "include_token_error"), false);
});

test("normalizeSub2APIUploadSummary falls back to not_uploaded for missing data", () => {
  assert.deepEqual(normalizeSub2APIUploadSummary(null), {
    uploaded_group_ids: [],
    error_group_ids: [],
    not_uploaded_group_ids: [],
    status: "not_uploaded",
    remote_ids: [],
    last_error: "",
  });
});

test("normalizeSub2APIUploadSummary copies arrays and status from backend payload", () => {
  const summary = normalizeSub2APIUploadSummary({
    uploaded_group_ids: [42, 108],
    error_group_ids: [7],
    not_uploaded_group_ids: [],
    status: "partial",
    remote_ids: ["r1"],
    last_error: "",
  });
  assert.equal(summary.status, "partial");
  assert.deepEqual(summary.uploaded_group_ids, [42, 108]);
  assert.deepEqual(summary.remote_ids, ["r1"]);
});

test("sub2apiUploadBadge renders uploaded badge with group count", () => {
  const badge = sub2apiUploadBadge({ uploaded_group_ids: [42, 108], error_group_ids: [], status: "uploaded" });
  assert.equal(badge.label, "已上传 2组");
  assert.equal(badge.color, "success");
  assert.equal(badge.status, "uploaded");
});

test("sub2apiUploadBadge renders partial badge in yellow", () => {
  const badge = sub2apiUploadBadge({ uploaded_group_ids: [42], error_group_ids: [7], status: "partial" });
  assert.equal(badge.label, "部分上传 1组");
  assert.equal(badge.color, "warning");
});

test("sub2apiUploadBadge renders No access token for token error summary", () => {
  const badge = sub2apiUploadBadge({ uploaded_group_ids: [], error_group_ids: [42], status: "error", last_error: "No access token available" });
  assert.equal(badge.label, "No access token");
  assert.equal(badge.color, "danger");
  assert.equal(badge.status, "token_error");
});

test("sub2apiUploadBadge renders not_uploaded in neutral", () => {
  const badge = sub2apiUploadBadge(null);
  assert.equal(badge.label, "未上传");
  assert.equal(badge.color, "neutral");
});

test("buildSub2APIUploadStatusQuery joins groups, status, q, pagination", () => {
  const query = buildSub2APIUploadStatusQuery({ groupIds: [42, 108], status: "token_error", q: "alice", accountId: 7, page: 2, pageSize: 50 });
  assert.equal(query, "group_ids=42%2C108&status=token_error&q=alice&account_id=7&page=2&page_size=50");
});

test("buildSub2APIUploadStatusQuery omits empty filters and defaults pagination", () => {
  const query = buildSub2APIUploadStatusQuery({});
  assert.equal(query, "page=1&page_size=20");
});

test("classifySub2APIUploadSelection counts uploaded / not_uploaded / error / token incomplete", () => {
  const accounts = [
    { id: 1, has_access_token: true, has_refresh_token: true, has_id_token: true, sub2api_upload_summary: { status: "uploaded", uploaded_group_ids: [42] } },
    { id: 2, has_access_token: true, has_refresh_token: true, has_id_token: true, sub2api_upload_summary: { status: "not_uploaded", not_uploaded_group_ids: [42] } },
    { id: 3, has_access_token: true, has_refresh_token: true, has_id_token: true, sub2api_upload_summary: { status: "error", error_group_ids: [42], last_error: "No access token available" } },
    { id: 4, has_access_token: true, has_refresh_token: false, has_id_token: false, sub2api_upload_summary: null },
  ];
  const summaryByAccountId = Object.fromEntries(accounts.map((a) => [a.id, a.sub2api_upload_summary]));

  const counts = classifySub2APIUploadSelection(accounts, [1, 2, 3, 4], summaryByAccountId);
  assert.deepEqual(counts, { notUploaded: 1, uploaded: 1, error: 1, unknown: 0, tokenIncomplete: 1 });
});
