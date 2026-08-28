// ============================================================
// 注册工作台 · 纯函数工具
// 布局配置 / 状态归一化 / 错误识别 / 分页 / 滚动判断 / payload
// ============================================================

// 布局配置：日志优先（monitor 首屏可见，日志高度不低于 420px，左栏固定宽度）
export const REGISTER_LAYOUT = {
  primary: "monitor",
  logPosition: "monitor-top",
  logMinHeight: 420,
  leftWidth: 400,
  leftColClass: "lg:grid-cols-[400px_minmax(0,1fr)]",
  historyBelow: false,
  historyPosition: "left-sidebar",
  compact: true,
};

export function getRegisterLayout() {
  return { ...REGISTER_LAYOUT };
}

export const REGISTER_CONFIG_STORAGE_KEY = "accountops-register-config";

const REGISTER_CONFIG_DEFAULTS = {
  proxy: "",
  headless: true,
  bind2FA: true,
  mode: "single",
  batchTarget: 10,
  batchConcurrency: 2,
  gmailEnabled: false,
  debugMode: false,
  debugTrace: false,
};

function clampRegisterNumber(value, fallback, min, max) {
  const number = Math.floor(Number(value));
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, number));
}

export function normalizeRegisterConfig(value = {}, fallback = {}) {
  const source = value && typeof value === "object" ? value : {};
  const defaults = { ...REGISTER_CONFIG_DEFAULTS, ...fallback };
  return {
    proxy: typeof source.proxy === "string" ? source.proxy.trim() : String(defaults.proxy || "").trim(),
    headless: typeof source.headless === "boolean" ? source.headless : !!defaults.headless,
    bind2FA: typeof source.bind2FA === "boolean" ? source.bind2FA : !!defaults.bind2FA,
    mode: source.mode === "batch" ? "batch" : defaults.mode === "batch" ? "batch" : "single",
    batchTarget: clampRegisterNumber(source.batchTarget, defaults.batchTarget, 1, 100),
    batchConcurrency: clampRegisterNumber(source.batchConcurrency, defaults.batchConcurrency, 1, 5),
    gmailEnabled: typeof source.gmailEnabled === "boolean" ? source.gmailEnabled : !!defaults.gmailEnabled,
    debugMode: typeof source.debugMode === "boolean" ? source.debugMode : !!defaults.debugMode,
    debugTrace: typeof source.debugTrace === "boolean" ? source.debugTrace : !!defaults.debugTrace,
  };
}

export function readStoredRegisterConfig(storage = typeof globalThis !== "undefined" ? globalThis.localStorage : null) {
  if (!storage || typeof storage.getItem !== "function") return null;
  try {
    const raw = storage.getItem(REGISTER_CONFIG_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? normalizeRegisterConfig(parsed) : null;
  } catch {
    return null;
  }
}

export function saveStoredRegisterConfig(config, storage = typeof globalThis !== "undefined" ? globalThis.localStorage : null) {
  const normalized = normalizeRegisterConfig(config);
  if (!storage || typeof storage.setItem !== "function") return normalized;
  try {
    storage.setItem(REGISTER_CONFIG_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // Storage may be unavailable or full; the form remains usable in memory.
  }
  return normalized;
}

// 注册阶段（与后端 registrator 流程对应）
export const REGISTER_STAGES = [
  { key: "browser", label: "准备浏览器" },
  { key: "mail", label: "租/复用邮箱" },
  { key: "submit", label: "提交邮箱" },
  { key: "password", label: "设置密码" },
  { key: "wait_code", label: "等待验证码" },
  { key: "fill_code", label: "填写验证码" },
  { key: "profile", label: "完成资料" },
  { key: "session", label: "网页登录状态" },
  { key: "totp", label: "绑定 2FA" },
  { key: "save", label: "保存账号" },
  { key: "cooling", label: "冷却/验货" },
];

const REGISTER_STAGE_INDEX = Object.fromEntries(
  REGISTER_STAGES.map((stage, index) => [stage.key, index]),
);

// 无显式 [stage:*] 标记时的兼容匹配。带标记的日志必须以标记为准，不能再被正文中的
// URL、phase 或诊断词覆盖（例如 fill_code 日志中的 phase=about_you）。
const REGISTER_STAGE_PATTERNS = [
  { idx: 0, re: /\blaunch(?:ing|ed)?\b|Camoufox|指纹|浏览器启动|开始浏览器注册/i },
  { idx: 1, re: /Gmail (?:别名|会话)模式|临时邮箱|create_address|activation|租用|租号/i },
  { idx: 2, re: /\[email\]|email-verification|提交邮箱|reload 恢复/i },
  { idx: 3, re: /password|密码|Continue with password/i },
  { idx: 4, re: /\[otp\]|等待验证码|轮询|poll|验证码/i },
  { idx: 5, re: /收到验证码|填写验证码|提交验证码|Verify/i },
  { idx: 6, re: /完成(?:个人)?资料|填写(?:about-you|个人)?(?:基本)?资料|资料(?:填写|提交|完成)/i },
  { idx: 7, re: /\bsession\b|网页登录状态|登录状态|提取网页登录/i },
  { idx: 8, re: /TOTP-2FA|ACTIVATED|mfa_info|activate_enrollment/i },
  { idx: 9, re: /保存账号|入库|注册成功|\[registration:\d+\] 成功|账号已写入(?:账号中心|账号管理)/i },
  { idx: 10, re: /冷却|验货|warmup|冷却期|健康检查/i },
];

/**
 * 从注册日志解析阶段。
 *
 * 后端的 [stage:*] 是唯一可靠的阶段信号；正文里的 URL/phase 只是诊断上下文。
 * 对没有阶段标记的历史日志才使用兼容关键词匹配，返回 -1 表示无法识别。
 */
export function getRegisterStageIndex(message) {
  const text = String(message || "");
  const explicit = text.match(/\[stage:([^\]\s]+)\]/i);
  if (explicit) {
    return REGISTER_STAGE_INDEX[explicit[1].toLowerCase()] ?? -1;
  }
  let matched = -1;
  REGISTER_STAGE_PATTERNS.forEach((pattern) => {
    if (pattern.re.test(text)) matched = Math.max(matched, pattern.idx);
  });
  return matched;
}

/**
 * 将新增日志并入当前阶段。一次注册内部的重试会重新输出 [stage:browser]，
 * 该标记代表新的阶段进度窗口，不能继续沿用上一轮的最大阶段。
 */
export function advanceRegisterStage(currentIndex = -1, lines = []) {
  let current = Number.isFinite(currentIndex) ? currentIndex : -1;
  (lines || []).forEach((line) => {
    const message = String(line?.msg || "");
    if (/\[stage:browser\]/i.test(message)) current = -1;
    current = Math.max(current, getRegisterStageIndex(message));
  });
  return current;
}

// 记录/任务状态元信息
export const REGISTER_STATUS_META = {
  pending: { label: "排队中", color: "info" },
  running: { label: "运行中", color: "info" },
  success: { label: "已完成", color: "success" },
  failed: { label: "失败", color: "danger" },
  canceled: { label: "已停止", color: "neutral" },
  cooling: { label: "冷却中", color: "warning" },
  completed: { label: "已完成", color: "success" },
  idle: { label: "空闲", color: "neutral" },
  stopping: { label: "停止中", color: "warning" },
  debug_waiting: { label: "调试暂停", color: "warning" },
};

export function isRunningStatus(status) {
  return status === "pending" || status === "running" || status === "debug_waiting";
}

/**
 * 运行态 / 终态 / 空闲 归一化。
 * 优先级：批量运行 > 单次运行 > 批量终态 > 单次终态 > 空闲。
 * 返回 { key, label, color, active, taskLabel }。
 */
export function normalizeRegisterStatus(reg, batch) {
  const batchDebugReg = batch?.registrations?.find((item) => item?.status === "debug_waiting");
  if (batchDebugReg) {
    return { key: "debug_waiting", label: "调试暂停", color: "warning", active: true, taskLabel: `reg_${batchDebugReg.id}` };
  }
  if (batch?.status === "running") {
    return { key: "running", label: "运行中", color: "info", active: true, taskLabel: `batch_${batch.id}` };
  }
  if (reg && isRunningStatus(reg.status)) {
    if (reg.status === "debug_waiting") {
      return { key: "debug_waiting", label: "调试暂停", color: "warning", active: true, taskLabel: `reg_${reg.id}` };
    }
    return { key: "running", label: "运行中", color: "info", active: true, taskLabel: `reg_${reg.id}` };
  }
  if (batch && batch.status === "completed") {
    return { key: "completed", label: "已完成", color: "success", active: false, taskLabel: `batch_${batch.id}` };
  }
  if (batch && batch.status === "canceled") {
    return { key: "canceled", label: "已停止", color: "neutral", active: false, taskLabel: `batch_${batch.id}` };
  }
  if (reg && reg.status === "success") {
    return { key: "success", label: "已完成", color: "success", active: false, taskLabel: `reg_${reg.id}` };
  }
  if (reg && reg.status === "failed") {
    return { key: "failed", label: "失败", color: "danger", active: false, taskLabel: `reg_${reg.id}` };
  }
  if (reg && reg.status === "canceled") {
    return { key: "canceled", label: "已停止", color: "neutral", active: false, taskLabel: `reg_${reg.id}` };
  }
  return { key: "idle", label: "空闲", color: "neutral", active: false, taskLabel: "" };
}

/**
 * 选择注册工作台当前应展示的任务。
 * 优先级：点击聚焦的记录 > 批量运行中的当前记录 > 历史运行中记录 > 当前 active > 最近历史记录。
 */
export function pickDisplayRegister({ focusedReg = null, batchActive = null, active = null, historyRows = [] } = {}) {
  const batchIsRunning = batchActive?.status === "running";
  const focusedId = focusedReg?.id ?? null;
  const latestFocusedReg = focusedId
    ? (batchActive?.registrations?.find((r) => r.id === focusedId)
      || historyRows.find((r) => r.id === focusedId)
      || (active?.id === focusedId ? active : null)
      || focusedReg)
    : null;
  const historyRunningReg = historyRows.find((r) => isRunningStatus(r.status)) || null;
  const batchFocusedReg = batchIsRunning
    ? batchActive.registrations?.find((r) => isRunningStatus(r.status)) || batchActive.registrations?.[0] || null
    : null;
  const activeBatchReg = batchIsRunning && active?.batch_id === batchActive?.id && isRunningStatus(active.status)
    ? active
    : null;
  return latestFocusedReg || (batchIsRunning
    ? (activeBatchReg || batchFocusedReg || null)
    : (historyRunningReg || (active && isRunningStatus(active.status) ? active : null) || historyRows[0] || null));
}

/** 任务不存在错误识别：404 / not found / 不存在 / 已删除 等。 */
export function isTaskMissingError(error) {
  const msg = String((error && (error.message || error)) || "").toLowerCase();
  if (!msg) return false;
  return /404|not found|notfound|不存在|已删除|no longer exists|已被移除/i.test(msg);
}

/** 简单分页（纯函数，返回切片与页码信息）。 */
export function paginateRecords(rows, page = 1, pageSize = 10) {
  const source = rows || [];
  const size = Math.max(1, Number.isFinite(pageSize) ? pageSize : 10);
  const total = source.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const current = Math.min(Math.max(1, Number.isFinite(page) ? page : 1), pages);
  const start = (current - 1) * size;
  return { items: source.slice(start, start + size), total, page: current, pages, pageSize: size };
}

/** 日志自动滚动判断：贴底（剩余 < threshold）应跟随最新。 */
export function shouldAutoScrollLog(el, threshold = 48) {
  if (!el) return true;
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
}

/**
 * 构造注册/批量注册请求体（保持既有接口契约）。
 * mode: "single" | "batch"
 * single: { proxy, headless, bind_totp, gmail_alias?, gmail_mail_id? }
 * batch:  { proxy, headless, bind_totp, target, concurrency, gmail_mode }
 */
export function buildRegisterPayload({
  mode = "single",
  proxy = "",
  headless = true,
  bind2FA = true,
  target = 10,
  concurrency = 2,
  gmailAlias = "",
  gmailMailId = "",
  gmailMode = false,
  debugMode = false,
  debugTrace = false,
} = {}) {
  const enabledDebugMode = !!debugMode;
  const enabledTrace = !!debugTrace;
  const base = {
    proxy: String(proxy || "").trim(),
    headless: enabledDebugMode ? false : !!headless,
    bind_totp: !!bind2FA,
  };
  if (enabledDebugMode) base.debug_mode = true;
  if (enabledTrace) base.debug_trace = true;
  const safeConcurrency = Math.max(1, Math.min(5, Math.floor(Number(concurrency) || 1)));
  if (mode === "batch") {
    return {
      ...base,
      target: Math.max(1, Math.floor(Number(target) || 1)),
      concurrency: safeConcurrency,
      gmail_mode: !!gmailMode,
    };
  }
  const body = { ...base };
  if (gmailAlias) {
    body.gmail_alias = String(gmailAlias);
    body.gmail_mail_id = String(gmailMailId || "");
  }
  return body;
}

/** 注册记录筛选：status + 关键词（reg_id / email / phone / account_id）。 */
export function filterRegisterRecords(rows, { status = "all", q = "" } = {}) {
  const keyword = String(q || "").trim().toLowerCase();
  return (rows || []).filter((r) => {
    if (status !== "all" && r.status !== status) return false;
    if (!keyword) return true;
    const hay = `reg_${r.id} ${r.id} ${r.email || ""} ${r.phone || ""} ${r.account_id || ""} ${r.error || ""}`.toLowerCase();
    return hay.includes(keyword);
  });
}

/** 记录列表的副标题：失败任务优先显示错误，避免被邮箱字段遮住。 */
export function getRegistrationRecordSummary(record) {
  if (!record) return "无邮箱信息";
  if (record.status === "failed" && record.error) return record.error;
  return record.email || record.error || "无邮箱信息";
}

/** 导出记录时保留失败原因；result_json 是运行过程中的诊断上下文。 */
export function formatRegistrationCopy(record) {
  const result = parseRegisterResult(record);
  if (!result && !record?.error) return "无结果数据";
  return JSON.stringify({ ...(result || {}), ...(record?.error ? { error: record.error } : {}) }, null, 2);
}

/** 耗时（秒）：created_at / finished_at 均为 ISO 字符串。 */
export function formatRegDuration(createdAt, finishedAt) {
  if (!createdAt || !finishedAt) return null;
  const start = new Date(createdAt).getTime();
  const end = new Date(finishedAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function formatDurationLabel(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}分${String(s).padStart(2, "0")}秒`;
}

/** 从注册记录的 result_json 中解析结果（与页面一致）。 */
export function parseRegisterResult(record) {
  if (!record || !record.result_json) return null;
  try {
    return JSON.parse(record.result_json);
  } catch {
    return null;
  }
}

// ============================================================
// 实时日志：级别归一化 / 过滤
// ============================================================

export const LOG_LEVEL_META = {
  info: { label: "INFO", color: "text-slate-400", rowBg: "" },
  success: { label: "OK", color: "text-emerald-400", rowBg: "bg-emerald-500/5" },
  warning: { label: "WARN", color: "text-amber-400", rowBg: "bg-amber-500/5" },
  error: { label: "ERR", color: "text-red-400", rowBg: "bg-red-500/5" },
};

/** 日志级别归一化：error / success / warning / info（不吞原始错误文本）。 */
export function normalizeLogLevel(msg) {
  const text = String(msg || "");
  if (/失败|Error|异常|拦截|卡住|超时|拒绝|不可用|无效|error/i.test(text)) return "error";
  if (/成功|ACTIVATED|绑定|完成/.test(text)) return "success";
  if (/等待|重试|限流|\[worker\]|告警|warn/i.test(text)) return "warning";
  return "info";
}

/** 按级别过滤日志行；"all" 返回原列表。 */
export function filterLogLines(lines, level = "all") {
  if (!level || level === "all") return lines || [];
  return (lines || []).filter((l) => normalizeLogLevel(l?.msg) === level);
}
