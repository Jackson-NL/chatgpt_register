import test from "node:test";
import assert from "node:assert/strict";
import {
  REGISTER_STAGES,
  REGISTER_CONFIG_STORAGE_KEY,
  buildRegisterPayload,
  filterLogLines,
  filterRegisterRecords,
  formatRegistrationCopy,
  formatDurationLabel,
  formatRegDuration,
  advanceRegisterStage,
  getRegisterLayout,
  getRegisterStageIndex,
  getRegistrationRecordSummary,
  isRunningStatus,
  isTaskMissingError,
  normalizeLogLevel,
  normalizeRegisterStatus,
  paginateRecords,
  parseRegisterResult,
  normalizeRegisterConfig,
  pickDisplayRegister,
  readStoredRegisterConfig,
  saveStoredRegisterConfig,
  shouldAutoScrollLog,
} from "../src/pages/registerUtils.js";
import { fmtTime } from "../src/mock/data.js";

test("register config persists the last panel values and normalizes invalid stored values", () => {
  const storage = new Map();
  const fakeStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  };
  const config = normalizeRegisterConfig({
    proxy: "  http://127.0.0.1:7890  ",
    headless: false,
    bind2FA: false,
    mode: "batch",
    batchTarget: 120,
    batchConcurrency: 0,
    gmailEnabled: true,
    debugMode: true,
  });

  saveStoredRegisterConfig(config, fakeStorage);

  assert.equal(storage.has(REGISTER_CONFIG_STORAGE_KEY), true);
  assert.deepEqual(readStoredRegisterConfig(fakeStorage), {
    proxy: "http://127.0.0.1:7890",
    headless: false,
    bind2FA: false,
    mode: "batch",
    batchTarget: 100,
    batchConcurrency: 1,
    gmailEnabled: true,
    debugMode: true,
    debugTrace: false,
  });
});

test("readStoredRegisterConfig returns null for missing or malformed storage", () => {
  const emptyStorage = { getItem: () => null };
  const malformedStorage = { getItem: () => "not-json" };

  assert.equal(readStoredRegisterConfig(emptyStorage), null);
  assert.equal(readStoredRegisterConfig(malformedStorage), null);
});


test("REGISTER_STAGES keeps registration separate from Codex OAuth", () => {
  const labels = REGISTER_STAGES.map((stage) => stage.label).join(" /");
  const keys = REGISTER_STAGES.map((stage) => stage.key);
  assert.equal(keys.includes("oauth"), false);
  assert.match(labels, /网页登录状态/);
  assert.doesNotMatch(labels, /OAuth|授权码|Token|令牌|refresh_token|id_token/i);
});

test("getRegisterStageIndex trusts an explicit stage marker over status text", () => {
  assert.equal(
    getRegisterStageIndex("[stage:fill_code] 验证码提交后 phase=about_you url=https://auth.openai.com/about-you"),
    5,
  );
  assert.equal(getRegisterStageIndex("[stage:profile] 填写 about-you 基本资料"), 6);
  assert.equal(getRegisterStageIndex("[system] 账号已写入账号管理"), 9);
  assert.equal(getRegisterStageIndex("[gmail] 开始检查并切换代理出口"), -1);
  assert.equal(getRegisterStageIndex("[stage:oauth] 授权页已打开"), -1);
});

test("advanceRegisterStage starts a fresh progress window after a retry", () => {
  const stage = advanceRegisterStage(6, [
    { msg: "[worker] about-you Finish 等待超时，准备重试" },
    { msg: "[stage:browser] 启动 Camoufox" },
    { msg: "[stage:fill_code] 填写并提交邮箱验证码" },
  ]);
  assert.equal(stage, 5);
});

test("getRegisterLayout returns log-first layout config", () => {
  const layout = getRegisterLayout();
  assert.equal(layout.primary, "monitor");
  assert.equal(layout.logPosition, "monitor-top");
  assert.ok(layout.logMinHeight >= 420, "日志区默认高度不低于 420px");
  assert.equal(layout.leftWidth, 400);
  assert.ok(layout.leftColClass.includes("400px"));
  assert.equal(layout.historyBelow, false);
  assert.equal(layout.historyPosition, "left-sidebar");
});

test("normalizeRegisterStatus: batch running dominates", () => {
  const state = normalizeRegisterStatus({ id: 5, status: "running" }, { id: 9, status: "running" });
  assert.equal(state.key, "running");
  assert.equal(state.taskLabel, "batch_9");
  assert.equal(state.active, true);
});

test("normalizeRegisterStatus: single running when no batch", () => {
  const state = normalizeRegisterStatus({ id: 5, status: "pending" }, null);
  assert.equal(state.key, "running");
  assert.equal(state.taskLabel, "reg_5");
  assert.equal(state.active, true);
});

test("normalizeRegisterStatus: terminal states are not active", () => {
  assert.deepEqual(normalizeRegisterStatus({ id: 1, status: "success" }, null), { key: "success", label: "已完成", color: "success", active: false, taskLabel: "reg_1" });
  const failed = normalizeRegisterStatus({ id: 2, status: "failed" }, null);
  assert.equal(failed.key, "failed");
  assert.equal(failed.active, false);
  const canceled = normalizeRegisterStatus({ id: 3, status: "canceled" }, null);
  assert.equal(canceled.key, "canceled");
  assert.equal(canceled.active, false);
  // 批量终态优先于单次终态展示
  const batchDone = normalizeRegisterStatus({ id: 1, status: "success" }, { id: 7, status: "completed" });
  assert.equal(batchDone.key, "completed");
  assert.equal(batchDone.active, false);
  // 单次仍在运行而旧批量已完成 → 仍显示运行中
  const stillRunning = normalizeRegisterStatus({ id: 5, status: "running" }, { id: 7, status: "completed" });
  assert.equal(stillRunning.key, "running");
  assert.equal(stillRunning.active, true);
});

test("normalizeRegisterStatus: idle when nothing present", () => {
  const state = normalizeRegisterStatus(null, null);
  assert.equal(state.key, "idle");
  assert.equal(state.label, "空闲");
  assert.equal(state.active, false);
  assert.equal(state.taskLabel, "");
});

test("pickDisplayRegister refreshes a stale focused record from history or batch state", () => {
  const focusedReg = { id: 7, status: "running", batch_id: 2 };
  const historyRows = [{ id: 7, status: "success", batch_id: 2 }];
  assert.equal(pickDisplayRegister({ focusedReg, historyRows })?.status, "success");

  const batchActive = {
    id: 2,
    status: "running",
    registrations: [{ id: 7, status: "success", batch_id: 2 }],
  };
  assert.equal(pickDisplayRegister({ focusedReg, batchActive, historyRows })?.status, "success");
});

test("isTaskMissingError detects 404 / not found / 不存在", () => {
  assert.equal(isTaskMissingError({ message: "HTTP 404" }), true);
  assert.equal(isTaskMissingError("404 Not Found"), true);
  assert.equal(isTaskMissingError("任务不存在，已删除"), true);
  assert.equal(isTaskMissingError({ message: "Network Error" }), false);
  assert.equal(isTaskMissingError({ message: "HTTP 500" }), false);
  assert.equal(isTaskMissingError(""), false);
});

test("paginateRecords slices by page and reports total/pages", () => {
  const rows = Array.from({ length: 25 }, (_, i) => ({ id: i + 1 }));
  const page1 = paginateRecords(rows, 1, 10);
  assert.equal(page1.total, 25);
  assert.equal(page1.pages, 3);
  assert.equal(page1.page, 1);
  assert.deepEqual(page1.items.map((r) => r.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  const page3 = paginateRecords(rows, 3, 10);
  assert.deepEqual(page3.items.map((r) => r.id), [21, 22, 23, 24, 25]);
  const over = paginateRecords(rows, 99, 10);
  assert.equal(over.page, 3);
  assert.equal(paginateRecords([], 1, 10).pages, 1);
});

test("shouldAutoScrollLog follows only near bottom", () => {
  assert.equal(shouldAutoScrollLog(null), true);
  const el = { scrollHeight: 1000, scrollTop: 990, clientHeight: 100 };
  assert.equal(shouldAutoScrollLog(el), true);
  const up = { scrollHeight: 1000, scrollTop: 300, clientHeight: 100 };
  assert.equal(shouldAutoScrollLog(up), false);
});

test("buildRegisterPayload: single mode keeps interface contract", () => {
  const body = buildRegisterPayload({ mode: "single", proxy: "  http://127.0.0.1:7890  ", headless: true, bind2FA: true });
  assert.deepEqual(body, { proxy: "http://127.0.0.1:7890", headless: true, bind_totp: true });
});

test("debug mode forces headed browser and sends debug_mode", () => {
  const single = buildRegisterPayload({ mode: "single", headless: true, debugMode: true });
  assert.deepEqual(single, { proxy: "", headless: false, bind_totp: true, debug_mode: true });

  const batch = buildRegisterPayload({ mode: "batch", headless: true, debugMode: true, target: 2, concurrency: 2 });
  assert.equal(batch.headless, false);
  assert.equal(batch.debug_mode, true);
});

test("buildRegisterPayload: single mode includes gmail alias fields", () => {
  const body = buildRegisterPayload({ mode: "single", proxy: "p", gmailAlias: "reg_abc", gmailMailId: "42" });
  assert.deepEqual(body, { proxy: "p", headless: true, bind_totp: true, gmail_alias: "reg_abc", gmail_mail_id: "42" });
});

test("buildRegisterPayload: batch mode adds target/concurrency clamped", () => {
  const body = buildRegisterPayload({ mode: "batch", proxy: "p", target: 20, concurrency: 8 });
  assert.deepEqual(body, { proxy: "p", headless: true, bind_totp: true, target: 20, concurrency: 5, gmail_mode: false });
  const low = buildRegisterPayload({ mode: "batch", proxy: "p", target: 0, concurrency: 0 });
  assert.deepEqual(low, { proxy: "p", headless: true, bind_totp: true, target: 1, concurrency: 1, gmail_mode: false });
});

test("buildRegisterPayload: batch mode passes gmail_mode through", () => {
  const body = buildRegisterPayload({ mode: "batch", proxy: "p", target: 5, concurrency: 2, gmailMode: true });
  assert.equal(body.gmail_mode, true);
  assert.equal(body.target, 5);
  const off = buildRegisterPayload({ mode: "batch", proxy: "p", gmailMode: false });
  assert.equal(off.gmail_mode, false);
});

test("filterRegisterRecords filters by status and keyword", () => {
  const rows = [
    { id: 1, status: "success", email: "alice@example.com", account_id: "acc_1" },
    { id: 2, status: "failed", email: "bob@example.com", account_id: "" },
    { id: 12, status: "running", email: "carol@example.com", account_id: "" },
  ];
  assert.deepEqual(filterRegisterRecords(rows, { status: "failed" }).map((r) => r.id), [2]);
  assert.deepEqual(filterRegisterRecords(rows, { q: "bob" }).map((r) => r.id), [2]);
  assert.deepEqual(filterRegisterRecords(rows, { q: "reg_12" }).map((r) => r.id), [12]);
  assert.deepEqual(filterRegisterRecords(rows, { q: "acc_1" }).map((r) => r.id), [1]);
  assert.equal(filterRegisterRecords(rows, { status: "all", q: "" }).length, 3);
});

test("failed registration records keep the saved error visible, searchable, and copyable", () => {
  const record = {
    id: 254,
    status: "failed",
    email: "user@example.com",
    error: "email: 页面卡住: 邮箱提交后未跳转验证页",
    result_json: '{"email":"user@example.com","gmail_address_kind":"base"}',
  };
  assert.equal(getRegistrationRecordSummary(record), record.error);
  assert.deepEqual(filterRegisterRecords([record], { q: "未跳转验证页" }).map((row) => row.id), [254]);
  assert.deepEqual(JSON.parse(formatRegistrationCopy(record)), {
    email: "user@example.com",
    gmail_address_kind: "base",
    error: record.error,
  });
});

test("formatRegDuration / formatDurationLabel", () => {
  assert.equal(formatRegDuration("2026-08-18T10:00:00", "2026-08-18T10:01:30"), 90);
  assert.equal(formatRegDuration(null, "2026-08-18T10:01:30"), null);
  assert.equal(formatDurationLabel(90), "1分30秒");
  assert.equal(formatDurationLabel(45), "45s");
  assert.equal(formatDurationLabel(null), "—");
});

test("fmtTime returns a placeholder for an empty task timestamp", () => {
  assert.equal(fmtTime(undefined), "—");
  assert.equal(fmtTime("not-a-timestamp"), "—");
});

test("parseRegisterResult parses result_json safely", () => {
  assert.deepEqual(parseRegisterResult({ result_json: '{"email":"a@b.c"}' }), { email: "a@b.c" });
  assert.equal(parseRegisterResult({ result_json: "not-json" }), null);
  assert.equal(parseRegisterResult(null), null);
});

test("isRunningStatus helper", () => {
  assert.equal(isRunningStatus("running"), true);
  assert.equal(isRunningStatus("pending"), true);
  assert.equal(isRunningStatus("success"), false);
  assert.equal(isRunningStatus("debug_waiting"), true);
});

test("normalizeLogLevel classifies error / success / warning / info", () => {
  assert.equal(normalizeLogLevel("注册失败: proxy timeout"), "error");
  assert.equal(normalizeLogLevel("[Error] access denied"), "error");
  assert.equal(normalizeLogLevel("注册成功, ACTIVATED 2FA"), "success");
  assert.equal(normalizeLogLevel("保存账号完成"), "success");
  assert.equal(normalizeLogLevel("等待验证码，正在重试"), "warning");
  assert.equal(normalizeLogLevel("[worker] 限流告警"), "warning");
  assert.equal(normalizeLogLevel("打开 chatgpt 登录页"), "info");
  assert.equal(normalizeLogLevel(""), "info");
});

test("filterLogLines filters by level and keeps all raw lines for 'all'", () => {
  const lines = [
    { seq: 1, msg: "开始注册" },
    { seq: 2, msg: "注册失败: 验证码错误" },
    { seq: 3, msg: "注册成功，已保存账号" },
    { seq: 4, msg: "等待验证码重试中" },
  ];
  assert.equal(filterLogLines(lines, "all").length, 4);
  assert.deepEqual(filterLogLines(lines, "error").map((l) => l.seq), [2]);
  assert.deepEqual(filterLogLines(lines, "success").map((l) => l.seq), [3]);
  assert.deepEqual(filterLogLines(lines, "warning").map((l) => l.seq), [4]);
  assert.deepEqual(filterLogLines(lines, "info").map((l) => l.seq), [1]);
  assert.equal(filterLogLines(null, "all").length, 0);
  assert.equal(filterLogLines([{ msg: "x" }], "all").length, 1);
});
