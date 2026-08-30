import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_OAUTH_PARAMS,
  MAX_PERSISTED_OAUTH_LOGS,
  MAX_RENDERED_OAUTH_LOGS,
  OAUTH_COUNTRY_OPTIONS,
  OAUTH_LOG_POLL_HIDDEN_MS,
  OAUTH_LOG_POLL_VISIBLE_MS,
  OAUTH_RUNTIME_STORAGE_KEY,
  buildOAuthPayload,
  createOAuthRuntimeStore,
  filterOAuthAccounts,
  filterOAuthCountryOptions,
  findBlockedOAuthTargets,
  formatOAuthErrorMessage,
  getOAuthPendingCount,
  isOAuthCandidate,
  loadSavedOAuthForm,
  oauthBlockMessage,
  oauthMailProvider,
  oauthRowStatusLabel,
  oauthStageIndex,
  oauthStagesForMode,
  paginateOAuthAccounts,
  saveOAuthForm,
  summarizeOAuthAccounts,
  toggleOAuthCountry,
  trimOAuthLogs,
  getOAuthTargets,
  visibleOAuthLogs,
  projectOAuthResult,
  projectOAuthBackendLog,
  oauthBackendLogLevel,
  shouldFallbackToAutoPhone,
  shouldAutoScrollOAuthLogs,
  shouldFollowOAuthLogTail,
  shouldPollOAuthBackendLogs,
  shouldPollOAuthBackendLogsNow,
  oauthBackendLogPollDelay,
  oauthLogScrollSignal,
  codexOAuthConsoleLayout,
  isOAuthJobRunning,
  isOAuthJobMissingError,
  oauthRunStatusFromJob,
  projectOAuthJob,
  staleOAuthJobPatch,
} from "../src/pages/codexOAuthUtils.js";


test("codexOAuthConsoleLayout promotes logs and status before the account queue", () => {
  const layout = codexOAuthConsoleLayout();

  assert.deepEqual(layout.primary, ["command", "monitor", "settings", "accounts", "results"]);
  assert.equal(layout.logPlacement, "above-fold");
  assert.equal(layout.accountsDefaultCollapsed, true);
  assert.equal(layout.defaultAccountPageSize, 10);
});

test("buildOAuthPayload keeps the default Codex OAuth contract", () => {
  assert.deepEqual(buildOAuthPayload({}), DEFAULT_OAUTH_PARAMS);
  assert.equal(DEFAULT_OAUTH_PARAMS.sms_poll_timeout, 60);
  assert.equal(DEFAULT_OAUTH_PARAMS.low_price_first, false);
});

test("oauthBackendLogLevel classifies proxy network failures as warnings", () => {
  assert.equal(
    oauthBackendLogLevel("[acc_40] 代理/网络异常：Page.goto: NS_ERROR_NET_RESET"),
    "warning",
  );
  assert.equal(
    oauthBackendLogLevel("[acc_40] 代理/网络异常：已达到重试上限"),
    "warning",
  );
});

test("buildOAuthPayload normalizes OAuth concurrency", () => {
  assert.equal(buildOAuthPayload({ concurrency: "4" }).concurrency, 4);
  assert.equal(buildOAuthPayload({ concurrency: "0" }).concurrency, 1);
  assert.equal(buildOAuthPayload({ concurrency: "99" }).concurrency, 10);
});

test("buildOAuthPayload normalizes country order and numeric fields", () => {
  assert.deepEqual(
    buildOAuthPayload({
      headless: false,
      countries: "ph, ID, gb",
      max_price: "0.04",
      max_phone_attempts: "2",
      sms_poll_timeout: "90",
      sms_poll_interval: "3",
      low_price_first: true,
      concurrency: 3,
    }),
    {
      headless: false,
      countries: ["PH", "ID", "GB"],
      max_price: 0.04,
      max_phone_attempts: 2,
      sms_poll_timeout: 90,
      sms_poll_interval: 3,
      low_price_first: true,
      concurrency: 3,
    },
  );
});

test("getOAuthTargets prefers checked rows and otherwise uses the typed account id", () => {
  const accounts = [
    { id: 7, oauth_eligible: true },
    { id: 8, oauth_eligible: true },
    { id: 9, oauth_eligible: false },
  ];
  assert.deepEqual(getOAuthTargets(accounts, [8, 7], "9"), [8, 7]);
  // 手动输入的账号存在但不是 OAuth eligible（非 Gmail）时不生成目标。
  assert.deepEqual(getOAuthTargets(accounts, [], "9"), []);
  // 非法输入回退到自动候选列表。
  assert.deepEqual(getOAuthTargets(accounts, [], "missing"), [7, 8]);
});

test("getOAuthTargets auto-selects OAuth candidates when no rows or id are selected", () => {
  const accounts = [
    { id: 7, profile_path: "C:/profiles/7", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: true },
    { id: 8, profile_path: "C:/profiles/8", has_refresh_token: true, mail_provider: "gmail", oauth_eligible: false },
    { id: 9, profile_path: "", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: false },
    { id: 10, profile_path: "C:/profiles/10", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: true },
  ];

  assert.deepEqual(getOAuthTargets(accounts, [], ""), [7, 10]);
});

test("getOAuthPendingCount keeps failed accounts pending and only removes successes", () => {
  assert.equal(getOAuthPendingCount(3, [{ id: 7, status: "failed" }]), 3);
  assert.equal(getOAuthPendingCount(3, [
    { id: 7, status: "success" },
    { id: 8, status: "failed" },
  ]), 2);
  assert.equal(getOAuthPendingCount(3, [
    { id: 7, status: "success" },
    { id: 8, status: "success" },
    { id: 9, status: "success" },
  ]), 0);
});

test("projectOAuthJob keeps a stable target count while account candidates change", () => {
  const projected = projectOAuthJob({
    job_id: "job123",
    status: "running",
    account_ids: [1, 2, 3, 4, 5],
    results: [
      { id: 1, status: "success" },
      { id: 2, status: "failed" },
    ],
  });

  assert.equal(projected.targetCount, 5);
  assert.equal(getOAuthPendingCount(projected.targetCount, projected.results), 4);
});

test("projectOAuthResult exposes only the OAuth status fields", () => {
  const result = projectOAuthResult({
    id: 7,
    email: "user@example.com",
    phone: "+15550001111",
    has_access_token: true,
    has_refresh_token: true,
    token_expires_at: "2026-08-18T00:00:00Z",
    completed_at: "2026-08-20T01:51:51+00:00",
    plan_type: "plus",
    profile_path: "C:/profiles/7",
    access_token: "full-access-token",
    refresh_token: "full-refresh-token",
    id_token: "full-id-token",
    password: "password",
    totp_secret: "secret",
  });

  assert.deepEqual(result, {
    id: 7,
    email: "user@example.com",
    phone: "+15550001111",
    has_access_token: true,
    has_refresh_token: true,
    token_expires_at: "2026-08-18T00:00:00Z",
    completed_at: "2026-08-20T01:51:51+00:00",
    plan_type: "plus",
    profile_path: "C:/profiles/7",
    error_type: "",
  });
  assert.equal("access_token" in result, false);
  assert.equal("refresh_token" in result, false);
  assert.equal("id_token" in result, false);
  assert.equal("password" in result, false);
  assert.equal("totp_secret" in result, false);
});

test("projectOAuthResult keeps the non-sensitive OAuth error category", () => {
  const result = projectOAuthResult({ id: 40, error_type: "proxy_network" });

  assert.equal(result.error_type, "proxy_network");
  assert.equal("access_token" in result, false);
});

test("shouldFallbackToAutoPhone detects add-phone refresh failures", () => {
  assert.equal(
    shouldFallbackToAutoPhone(new Error("OAuth 进入 add-phone 手机验证页，需要先完成手机验证")),
    true,
  );
  assert.equal(
    shouldFallbackToAutoPhone(new Error("请改用 POST /accounts/{id}/oauth/auto-phone-from-profile")),
    true,
  );
  assert.equal(shouldFallbackToAutoPhone(new Error("令牌交换失败")), false);
});


test("toggleOAuthCountry keeps country priority unique and capped at three", () => {
  assert.deepEqual(toggleOAuthCountry(["PH", "ID"], "GB"), ["PH", "ID", "GB"]);
  assert.deepEqual(toggleOAuthCountry(["PH", "ID", "GB"], "SA"), ["ID", "GB", "SA"]);
  assert.deepEqual(toggleOAuthCountry(["PH", "ID", "GB"], "ID"), ["PH", "GB"]);
  assert.deepEqual(toggleOAuthCountry(["ph", "PH", "unknown"], "id"), ["PH", "ID"]);
});

test("saved OAuth form round-trips through storage and normalizes countries", () => {
  const store = new Map();
  const storage = {
    getItem: (key) => store.has(key) ? store.get(key) : null,
    setItem: (key, value) => store.set(key, value),
  };

  const saved = saveOAuthForm({
    headless: false,
    countries: ["GB", "SA", "ID", "PH"],
    max_price: "0.02",
    max_phone_attempts: "5",
    sms_poll_timeout: "90",
    sms_poll_interval: "2",
    low_price_first: true,
    concurrency: "3",
  }, storage);

  assert.deepEqual(saved.countries, ["GB", "SA", "ID"]);
  assert.deepEqual(loadSavedOAuthForm(storage), {
    headless: false,
    countries: ["GB", "SA", "ID"],
    max_price: "0.02",
    max_phone_attempts: "5",
    sms_poll_timeout: "90",
    sms_poll_interval: "2",
    low_price_first: true,
    concurrency: "3",
  });
});

test("OAuth country options expose supported countries for the selector", () => {
  const values = OAUTH_COUNTRY_OPTIONS.map((option) => option.value);
  assert.ok(values.includes("PH"));
  assert.ok(values.includes("ID"));
  assert.ok(values.includes("GB"));
  assert.ok(values.includes("SA"));
  assert.equal(new Set(values).size, values.length);
});


test("OAuth country helpers accept dynamic SMSBower country ids", () => {
  const options = [{ value: "smsbower:31" }, { value: "smsbower:22" }, { value: "PH" }];
  assert.deepEqual(toggleOAuthCountry(["PH"], "smsbower:31", options), ["PH", "smsbower:31"]);
  assert.deepEqual(buildOAuthPayload({ countries: ["smsbower:31", "22", "PH", "ID"] }).countries, ["smsbower:31", "smsbower:22", "PH"]);
});

test("OAuth stages keep direct and phone flows visually consistent", () => {
  assert.deepEqual(oauthStagesForMode("direct").map((stage) => stage.key), [
    "profile",
    "open",
    "select",
    "exchange",
    "write",
    "done",
  ]);
  assert.deepEqual(oauthStagesForMode("phone").map((stage) => stage.key), [
    "profile",
    "open",
    "select",
    "add-phone",
    "auto-phone",
    "write",
    "done",
  ]);
  assert.equal(oauthStageIndex("direct", "exchange"), 3);
  assert.equal(oauthStageIndex("phone", "auto-phone"), 4);
});

test("OAuth error log formatter keeps raw error details for debugging", () => {
  const message = formatOAuthErrorMessage(new Error("access_token=abc refresh_token=def password=secret"));

  assert.equal(message, "access_token=abc refresh_token=def password=secret");
  assert.equal(message.includes("[已隐藏]"), false);
});

test("Codex OAuth runtime store keeps logs across page remounts", () => {
  const store = createOAuthRuntimeStore();
  const snapshots = [];
  const unsubscribe = store.subscribe((snapshot) => snapshots.push(snapshot));

  store.patch({ running: true, currentTarget: 62, runStatus: "running" });
  store.appendLog({ id: "1", time: "03:05:57", message: "[acc_62] 打开 OAuth", level: "info" });
  unsubscribe();
  store.appendLog({ id: "2", time: "03:05:58", message: "[acc_62] token exchange", level: "info" });

  assert.equal(snapshots.at(-1).logs.length, 1);
  assert.deepEqual(store.getSnapshot().logs.map((log) => log.message), [
    "[acc_62] 打开 OAuth",
    "[acc_62] token exchange",
  ]);
  assert.equal(store.getSnapshot().running, true);
});

test("Codex OAuth runtime caps retained logs at the configured ceiling", () => {
  const store = createOAuthRuntimeStore({}, {
    getItem: () => null,
    setItem: () => {},
  });

  for (let index = 0; index < MAX_PERSISTED_OAUTH_LOGS + 5; index += 1) {
    store.appendLog({ id: `log-${index}`, time: "03:05:57", message: `line ${index}`, level: "info" });
  }

  const logs = store.getSnapshot().logs;
  assert.equal(MAX_PERSISTED_OAUTH_LOGS, 800);
  assert.equal(logs.length, 800);
  assert.equal(logs[0].id, "log-5");
  assert.equal(logs.at(-1).id, `log-${MAX_PERSISTED_OAUTH_LOGS + 4}`);
});

test("Codex OAuth log helpers trim state and render only the recent window", () => {
  const logs = Array.from({ length: MAX_PERSISTED_OAUTH_LOGS + 50 }, (_, index) => ({
    id: `log-${index}`,
    time: "03:05:57",
    message: `line ${index}`,
    level: "info",
  }));

  const retained = trimOAuthLogs(logs);
  const rendered = visibleOAuthLogs(retained);

  assert.equal(MAX_RENDERED_OAUTH_LOGS, 400);
  assert.equal(retained.length, MAX_PERSISTED_OAUTH_LOGS);
  assert.equal(rendered.length, MAX_RENDERED_OAUTH_LOGS);
  assert.equal(retained[0].id, "log-50");
  assert.equal(rendered[0].id, `log-${MAX_PERSISTED_OAUTH_LOGS + 50 - MAX_RENDERED_OAUTH_LOGS}`);
  assert.equal(rendered.at(-1).id, `log-${MAX_PERSISTED_OAUTH_LOGS + 49}`);
});

test("Codex OAuth runtime store persists results and restores them from storage", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const first = createOAuthRuntimeStore({}, storage);

  first.patch({
    backendJobId: "job_persisted",
    targetCount: 2,
    runStatus: "success",
    results: [{ id: 62, email: "saved@example.com", status: "success", has_refresh_token: true }],
  });

  assert.ok(values.has(OAUTH_RUNTIME_STORAGE_KEY));
  const second = createOAuthRuntimeStore({}, storage);
  assert.equal(second.getSnapshot().backendJobId, "job_persisted");
  assert.equal(second.getSnapshot().targetCount, 2);
  assert.deepEqual(second.getSnapshot().results, [
    { id: 62, email: "saved@example.com", status: "success", has_refresh_token: true },
  ]);
});

test("Codex OAuth runtime restore reclassifies persisted backend logs", () => {
  const values = new Map([[OAUTH_RUNTIME_STORAGE_KEY, JSON.stringify({
    logs: [{
      id: "backend-1",
      time: "21:36:41",
      message: "[stage:oauth] 国家下拉搜索: Indonesia",
      level: "success",
      source: "backend",
      backend_seq: 1,
    }],
  })]]);
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  const store = createOAuthRuntimeStore({}, storage);
  assert.equal(store.getSnapshot().logs[0].level, "info");
});

test("stale OAuth job cleanup preserves persisted results", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const store = createOAuthRuntimeStore({
    backendJobId: "job_missing",
    targetCount: 1,
    results: [{ id: 7, status: "success" }],
  }, storage);

  store.patch({
    running: true,
    backendJobId: "job_missing",
    targetCount: 1,
    results: [{ id: 7, status: "success" }],
  });
  store.patch(staleOAuthJobPatch());

  assert.deepEqual(store.getSnapshot().results, [{ id: 7, status: "success" }]);
  assert.equal(store.getSnapshot().targetCount, 1);
});


test("Codex OAuth logs auto-scroll while running and otherwise respect reader position", () => {
  assert.equal(shouldAutoScrollOAuthLogs({ running: true, scrollTop: 0, clientHeight: 300, scrollHeight: 2000 }), true);
  assert.equal(shouldAutoScrollOAuthLogs({ running: false, scrollTop: 1600, clientHeight: 360, scrollHeight: 2000 }), true);
  assert.equal(shouldAutoScrollOAuthLogs({ running: false, scrollTop: 200, clientHeight: 360, scrollHeight: 2000 }), false);
});

test("Codex OAuth log tail follow survives terminal log updates when the user was at bottom", () => {
  assert.equal(shouldFollowOAuthLogTail({ running: false, stickToBottom: true, scrollTop: 200, clientHeight: 360, scrollHeight: 2000 }), true);
  assert.equal(shouldFollowOAuthLogTail({ running: false, stickToBottom: false, scrollTop: 200, clientHeight: 360, scrollHeight: 2000 }), false);
  assert.equal(shouldFollowOAuthLogTail({ running: true, stickToBottom: false, scrollTop: 200, clientHeight: 360, scrollHeight: 2000 }), true);
});

test("Codex OAuth log scroll signal changes when capped logs replace the tail", () => {
  const store = createOAuthRuntimeStore({}, null);
  for (let index = 0; index < MAX_PERSISTED_OAUTH_LOGS; index += 1) {
    store.appendLog({ id: `log-${index}`, time: "03:05:57", message: `line ${index}`, level: "info" });
  }
  const before = store.getSnapshot().logs;
  store.appendLog({ id: "log-tail-new", time: "03:05:58", message: "new tail", level: "info" });
  const after = store.getSnapshot().logs;

  assert.equal(before.length, after.length);
  assert.notEqual(oauthLogScrollSignal(before), oauthLogScrollSignal(after));
});

test("Codex OAuth backend logs keep polling while the page is mounted", () => {
  assert.equal(shouldPollOAuthBackendLogs({ pageMounted: true }), true);
  assert.equal(shouldPollOAuthBackendLogs({ pageMounted: false }), false);
});

test("Codex OAuth backend log polling continues while a backend job id is retained", () => {
  assert.equal(shouldPollOAuthBackendLogsNow({ running: true, backendJobId: "" }), true);
  assert.equal(shouldPollOAuthBackendLogsNow({ running: false, backendJobId: "job_live" }), true);
  assert.equal(shouldPollOAuthBackendLogsNow({ running: false, backendJobId: "" }), false);
});

test("Codex OAuth backend log polling backs off when the page is hidden", () => {
  assert.equal(oauthBackendLogPollDelay({ hidden: false }), OAUTH_LOG_POLL_VISIBLE_MS);
  assert.equal(oauthBackendLogPollDelay({ hidden: true }), OAUTH_LOG_POLL_HIDDEN_MS);
  assert.equal(OAUTH_LOG_POLL_VISIBLE_MS, 800);
  assert.equal(OAUTH_LOG_POLL_HIDDEN_MS, 5000);
});

test("Codex OAuth backend logs are projected with stable ids and visible levels", () => {
  assert.equal(oauthBackendLogLevel("[stage:oauth] 国家下拉搜索: Indonesia"), "info");
  assert.equal(oauthBackendLogLevel("[stage:oauth] 手机号风控：OpenAI 无法给该号发短信，继续换号"), "warning");
  assert.equal(oauthBackendLogLevel("[stage:oauth] 收到手机验证码 activation_id=act-1"), "success");
  assert.notEqual(oauthBackendLogLevel("[stage:oauth] 未收到手机验证码，准备换号"), "success");
  assert.equal(oauthBackendLogLevel("[oauth:job] 实时补位任务共用节点: 日本高速01 -> 日本高速02 ip=203.10.99.12 ok=True error="), "success");
  assert.equal(oauthBackendLogLevel("[oauth:job] 实时补位任务共用节点: 日本高速09 -> 日本高速09 ip= ok=False error=Selector 没有可切换的真实节点"), "warning");
  assert.equal(oauthBackendLogLevel("[oauth:job] 节点切换 ok=False error=timeout"), "error");
  assert.equal(oauthBackendLogLevel("[oauth:auto-phone] 等待验证码 elapsed=12s"), "info");
  assert.equal(oauthBackendLogLevel("[stage:oauth] 暂无符合条件的手机号，5 秒后继续取号"), "info");
  assert.equal(oauthBackendLogLevel("[oauth:auto-phone] 租号失败 reason=NO_NUMBERS"), "info");
  assert.equal(oauthBackendLogLevel("[oauth:job] 失败：未租到满足价格上限的手机号"), "info");
  assert.equal(oauthBackendLogLevel("[oauth:auto-phone] 租号失败 reason=NO_BALANCE"), "error");
  assert.equal(
    oauthBackendLogLevel("[stage:oauth] 授权页已打开 timeout=90.0s，等待回调/中间页处理"),
    "info",
  );
  assert.equal(oauthBackendLogLevel("[oauth] 账号 7 已写回 OAuth token 成功"), "success");
  assert.equal(oauthBackendLogLevel("[oauth:auto-phone] 全部尝试结束失败 timeout"), "error");

  assert.deepEqual(projectOAuthBackendLog({
    seq: 42,
    ts: "03:05:57",
    msg: "[stage:oauth] 等待授权码回调中",
  }), {
    id: "backend-42",
    time: "03:05:57",
    message: "[stage:oauth] 等待授权码回调中",
    level: "info",
    source: "backend",
    backend_seq: 42,
  });
});

test("summarizeOAuthAccounts exposes total and OAuth candidate counts", () => {
  assert.deepEqual(
    summarizeOAuthAccounts([
      { id: 1, profile_path: "p1", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: true },
      { id: 2, profile_path: "p2", has_refresh_token: true, mail_provider: "gmail", oauth_eligible: false },
      { id: 3, profile_path: "", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: false },
    ]),
    {
      total: 3,
      eligible: 1,
      withProfile: 2,
      withRefreshToken: 1,
      withoutProfile: 1,
    },
  );
});

test("filterOAuthAccounts can show all accounts or only OAuth candidates", () => {
  const accounts = [
    { id: 10, email: "alpha@example.com", profile_path: "C:/p10", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: true },
    { id: 11, email: "beta@example.com", profile_path: "C:/p11", has_refresh_token: true, mail_provider: "gmail", oauth_eligible: false },
    { id: 12, email: "gamma@example.com", profile_path: "", has_refresh_token: false, mail_provider: "gmail", oauth_eligible: false },
  ];

  assert.deepEqual(filterOAuthAccounts(accounts, { query: "", onlyEligible: true }).map((item) => item.id), [10]);
  assert.deepEqual(filterOAuthAccounts(accounts, { query: "beta", onlyEligible: false }).map((item) => item.id), [11]);
  assert.deepEqual(filterOAuthAccounts(accounts, { query: "p1", onlyEligible: false }).map((item) => item.id), [10, 11]);
});

test("paginateOAuthAccounts slices visible account rows and clamps pages", () => {
  const accounts = Array.from({ length: 43 }, (_, index) => ({ id: index + 1 }));
  const page = paginateOAuthAccounts(accounts, 3, 20);

  assert.deepEqual(page.rows.map((account) => account.id), [41, 42, 43]);
  assert.equal(page.total, 43);
  assert.equal(page.page, 3);
  assert.equal(page.pages, 3);
  assert.equal(page.from, 41);
  assert.equal(page.to, 43);
  assert.equal(paginateOAuthAccounts(accounts, 99, 20).page, 3);
});

test("filterOAuthCountryOptions searches labels and dynamic SMSBower ids", () => {
  const options = [
    { value: "PH", label: "菲律宾 PH · +63" },
    { value: "smsbower:6", label: "Indonesia · ID · +62 · SMSBower #6" },
    { value: "GB", label: "英国 GB · +44" },
  ];

  assert.deepEqual(filterOAuthCountryOptions(options, "62").map((item) => item.value), ["smsbower:6"]);
  assert.deepEqual(filterOAuthCountryOptions(options, "phil").map((item) => item.value), ["PH"]);
  assert.deepEqual(filterOAuthCountryOptions(options, "smsbower:6").map((item) => item.value), ["smsbower:6"]);
});


test("Codex OAuth job projection restores running state for stop button", () => {
  const projected = projectOAuthJob({
    job_id: "job123",
    status: "running",
    current_account_id: 62,
    current_flow: "phone",
    current_stage: 4,
    results: [{ id: 62, email: "a@example.com", has_access_token: true, has_refresh_token: true, status: "success" }],
  });

  assert.equal(isOAuthJobRunning("running"), true);
  assert.equal(isOAuthJobRunning("stopping"), true);
  assert.equal(isOAuthJobRunning("success"), false);
  assert.equal(oauthRunStatusFromJob("stopping"), "stopped");
  assert.equal(projected.backendJobId, "job123");
  assert.equal(projected.running, true);
  assert.equal(projected.activeAction, "codex-oauth");
  assert.equal(projected.currentTarget, 62);
  assert.equal(projected.results[0].status, "success");
});

test("Codex OAuth detects stale missing backend job errors", () => {
  assert.equal(isOAuthJobMissingError(new Error("OAuth job 不存在")), true);
  assert.equal(isOAuthJobMissingError(new Error("HTTP 404")), true);
  assert.equal(isOAuthJobMissingError(new Error("网络错误")), false);
});

// ------------------------------------------------------------------
// 邮箱来源（mail_provider）与 Codex OAuth 资格 fail-closed 过滤
// ------------------------------------------------------------------

const gmailEligible = {
  id: 1,
  profile_path: "C:/profiles/1",
  has_refresh_token: false,
  mail_provider: "gmail",
  oauth_eligible: true,
  oauth_block_reason: "",
};

function accountWithProvider(provider, overrides = {}) {
  return {
    id: 2,
    profile_path: "C:/profiles/2",
    has_refresh_token: false,
    mail_provider: provider,
    oauth_eligible: false,
    oauth_block_reason: "仅 Gmail 来源账号允许进入 Codex OAuth",
    ...overrides,
  };
}

test("isOAuthCandidate keeps Gmail eligible accounts and fails closed otherwise", () => {
  assert.equal(isOAuthCandidate(gmailEligible), true);
  assert.equal(isOAuthCandidate(accountWithProvider("cf_temp_email")), false);
  assert.equal(isOAuthCandidate(accountWithProvider("outlook")), false);
  assert.equal(isOAuthCandidate(accountWithProvider("unknown")), false);
  assert.equal(isOAuthCandidate(accountWithProvider("gmail", {
    status: "cooling",
    oauth_block_reason: "账号仍在冷却期，冷却结束前不能进入 Codex OAuth",
  })), false);
  // 后端字段缺失时默认 fail closed，未知账号不进候选。
  assert.equal(isOAuthCandidate({ id: 3, profile_path: "p", has_refresh_token: false }), false);
});

test("oauthMailProvider defaults unknown sources", () => {
  assert.equal(oauthMailProvider(gmailEligible), "gmail");
  assert.equal(oauthMailProvider({}), "unknown");
  assert.equal(oauthMailProvider({ mail_provider: " CF_Temp_Email " }), "cf_temp_email");
});

test("manual typed non-Gmail account id produces no OAuth target and reports the block", () => {
  const accounts = [gmailEligible, accountWithProvider("cf_temp_email")];
  assert.deepEqual(getOAuthTargets(accounts, [], "2"), []);
  const blocked = findBlockedOAuthTargets(accounts, [], "2");
  assert.equal(blocked.length, 1);
  assert.equal(blocked[0].id, 2);
  assert.match(oauthBlockMessage(blocked[0]), /Gmail 来源/);
  // 后端字段缺失时的兜底提示同样明确。
  assert.match(oauthBlockMessage({ mail_provider: "cf_temp_email" }), /不是 Gmail 来源/);
  assert.match(oauthBlockMessage({
    mail_provider: "gmail",
    status: "cooling",
    profile_path: "C:/profiles/2",
    has_refresh_token: false,
  }), /冷却期/);
});

test("mixed selection never sends non-Gmail ids", () => {
  const accounts = [
    gmailEligible,
    accountWithProvider("cf_temp_email"),
    accountWithProvider("outlook", { id: 3 }),
    accountWithProvider("unknown", { id: 4 }),
  ];
  const targets = getOAuthTargets(accounts, [1, 2, 3, 4], "");
  assert.deepEqual(targets, [1]);
  assert.equal(
    findBlockedOAuthTargets(accounts, [2, 3, 4], "").map((account) => account.id).sort().join(","),
    "2,3,4",
  );
});

test("auto target list only contains Gmail eligible accounts", () => {
  const accounts = [
    gmailEligible,
    accountWithProvider("cf_temp_email"),
    accountWithProvider("outlook", { id: 3 }),
    accountWithProvider("unknown", { id: 4 }),
    { ...gmailEligible, id: 5 },
  ];
  assert.deepEqual(getOAuthTargets(accounts, [], ""), [1, 5]);
});

test("oauthRowStatusLabel describes blocked reasons per provider", () => {
  assert.equal(oauthRowStatusLabel(gmailEligible), "OAuth 候选");
  assert.equal(oauthRowStatusLabel(accountWithProvider("gmail", { profile_path: "" })), "缺少 profile，已跳过");
  assert.equal(oauthRowStatusLabel(accountWithProvider("gmail", { has_refresh_token: true })), "已有 refresh_token，已跳过");
  assert.equal(oauthRowStatusLabel(accountWithProvider("gmail", { status: "cooling" })), "冷却中，已跳过");
  assert.equal(oauthRowStatusLabel(accountWithProvider("cf_temp_email")), "非 Gmail，已跳过");
  assert.equal(oauthRowStatusLabel(accountWithProvider("outlook")), "非 Gmail，已跳过");
  assert.equal(oauthRowStatusLabel(accountWithProvider("unknown")), "来源未知，已跳过");
});
