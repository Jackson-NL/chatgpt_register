export const DEFAULT_RELOGIN_PARAMS = Object.freeze({
  only_error: true,
  headless: true,
  concurrency: 3,
  timeout_s: 160,
});

export function normalizeReloginGroupIds(value) {
  const source = Array.isArray(value) ? value : String(value ?? "").split(/[,，\s]+/);
  return [...new Set(source.map(Number).filter((id) => Number.isInteger(id) && id > 0))];
}

export function buildReloginPreviewParams(groupIds, onlyError = true) {
  return {
    group_ids: normalizeReloginGroupIds(groupIds),
    only_error: Boolean(onlyError),
  };
}

const SAFE_PREVIEW_ITEM_KEYS = [
  "remote_id",
  "remote_account_id",
  "email",
  "name",
  "group_ids",
  "status",
  "remote_status",
  "error_text",
  "remote_error",
  "local_account_id",
  "action",
  "reason",
  "is_error",
];

export function sanitizeReloginPreviewItems(items = []) {
  if (!Array.isArray(items)) return [];
  return items.map((item) => {
    const safe = {};
    SAFE_PREVIEW_ITEM_KEYS.forEach((key) => {
      if (item && item[key] !== undefined && item[key] !== null && item[key] !== "") {
        safe[key] = item[key];
      }
    });
    return safe;
  }).filter((item) => Object.keys(item).length > 0);
}

export function buildReloginJobPayload(values = {}) {
  const concurrency = Number(values.concurrency);
  const timeout = Number(values.timeout_s);
  const payload = {
    group_ids: normalizeReloginGroupIds(values.group_ids),
    only_error: values.only_error ?? DEFAULT_RELOGIN_PARAMS.only_error,
    headless: values.headless ?? DEFAULT_RELOGIN_PARAMS.headless,
    concurrency: Number.isInteger(concurrency) && concurrency > 0 ? Math.min(5, concurrency) : DEFAULT_RELOGIN_PARAMS.concurrency,
    timeout_s: Number.isFinite(timeout) && timeout > 0 ? Math.trunc(timeout) : DEFAULT_RELOGIN_PARAMS.timeout_s,
  };
  const previewItems = sanitizeReloginPreviewItems(values.preview_items);
  if (previewItems.length) {
    payload.preview_items = previewItems;
  }
  return payload;
}

export function reloginJobActive(status) {
  return status === "pending" || status === "running";
}

export function reloginStatusMeta(status) {
  if (status === "success" || status === "completed") return { color: "success", label: "成功" };
  if (status === "failed") return { color: "danger", label: "失败" };
  if (status === "canceled") return { color: "warning", label: "已停止" };
  if (status === "skipped") return { color: "warning", label: "跳过" };
  if (status === "running") return { color: "info", label: "运行中" };
  return { color: "neutral", label: "待执行" };
}

export function projectReloginItem(item = {}) {
  const rawPreview = Boolean(item.remote_id);
  return {
    id: item.id,
    remote_account_id: item.remote_account_id || item.remote_id || "",
    local_account_id: item.local_account_id ?? null,
    email: item.email || "",
    remote_status: item.remote_status || (rawPreview ? item.status : "") || "",
    remote_error: item.remote_error || item.error_text || "",
    status: item.action || (rawPreview ? "pending" : item.status) || "pending",
    reason: item.reason || "",
    error: item.error || "",
  };
}


export function paginateReloginRows(rows = [], page = 1, pageSize = 20) {
  const source = Array.isArray(rows) ? rows : [];
  const size = Number.isInteger(Number(pageSize)) && Number(pageSize) > 0 ? Number(pageSize) : 20;
  const total = source.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const current = Math.min(Math.max(1, Number.isInteger(Number(page)) ? Number(page) : 1), pages);
  const start = (current - 1) * size;
  const visible = source.slice(start, start + size);
  return {
    rows: visible,
    page: current,
    pageSize: size,
    pages,
    total,
    from: total ? start + 1 : 0,
    to: start + visible.length,
  };
}
