export function normalizeSub2APIGroups(payload) {
  const source = Array.isArray(payload) ? payload : payload?.data;
  if (!Array.isArray(source)) return [];
  return source.filter((group) => (
    group && Number(group.id) > 0 && group.platform === "openai" && group.status !== "disabled"
  )).map((group) => ({
    id: Number(group.id),
    name: String(group.name || `分组 ${group.id}`),
    platform: group.platform,
    status: group.status || "active",
  }));
}

export function normalizeSub2APIGroupIds(value) {
  const source = Array.isArray(value) ? value : String(value ?? "").split(/[,，\s]+/);
  return [...new Set(source.map(Number).filter((id) => Number.isInteger(id) && id > 0))];
}

export function normalizeSub2APIConcurrency(value, fallback = 3) {
  const concurrency = Number(value);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 20) return fallback;
  return concurrency;
}

export function buildSub2APIUploadRequest(ids, groupIds, concurrency, options = {}) {
  const request = {
    ids: ids.map(Number).filter((id) => Number.isInteger(id) && id > 0),
    group_ids: normalizeSub2APIGroupIds(groupIds),
    concurrency: normalizeSub2APIConcurrency(concurrency),
  };
  if (options.onlyNotUploaded) request.only_not_uploaded = true;
  if (options.overwriteExisting === false) request.overwrite_existing = false;
  if (options.includeTokenError) request.include_token_error = true;
  return request;
}

export function normalizeSub2APIUploadJob(job) {
  const source = job && typeof job === "object" ? job : {};
  const total = Math.max(0, Number(source.total) || 0);
  const processed = Math.min(total, Math.max(0, Number(source.processed) || 0));
  return {
    status: source.status || "pending",
    total,
    processed,
    success: Math.max(0, Number(source.success) || 0),
    failed: Math.max(0, Number(source.failed) || 0),
    progress: total > 0 ? Math.round((processed / total) * 100) : source.status === "completed" ? 100 : 0,
    current_account_id: source.current_account_id ?? null,
    current_email: String(source.current_email || ""),
    error: String(source.error || ""),
    result: source.result && typeof source.result === "object" ? source.result : null,
  };
}

export function selectSub2APIUploadableIds(accounts = [], selectedIds = []) {
  const byId = new Map((accounts || []).map((account) => [Number(account.id), account]));
  const ids = [];
  const skipped = [];
  for (const rawId of selectedIds || []) {
    const id = Number(rawId);
    if (!Number.isInteger(id) || id <= 0) continue;
    const account = byId.get(id);
    if (account?.has_access_token) ids.push(id);
    else skipped.push(id);
  }
  return { ids, skipped };
}

// ============================================================
// Sub2API 上传状态（本地持久化）归一化
// ============================================================

export const SUB2API_UPLOAD_STATUS_META = {
  not_uploaded: { label: "未上传", short: "未上传", color: "neutral" },
  uploaded: { label: "已上传", short: "已上传", color: "success" },
  uploaded_error: { label: "上传异常", short: "上传异常", color: "danger" },
  token_error: { label: "No access token", short: "无 token", color: "danger" },
  remote_error: { label: "远端异常", short: "远端异常", color: "danger" },
  group_mismatch: { label: "分组不匹配", short: "分组不匹配", color: "warning" },
};

export function normalizeSub2APIUploadSummary(summary) {
  if (!summary || typeof summary !== "object") {
    return {
      uploaded_group_ids: [],
      error_group_ids: [],
      not_uploaded_group_ids: [],
      status: "not_uploaded",
      remote_ids: [],
      last_error: "",
    };
  }
  return {
    uploaded_group_ids: Array.isArray(summary.uploaded_group_ids) ? [...summary.uploaded_group_ids] : [],
    error_group_ids: Array.isArray(summary.error_group_ids) ? [...summary.error_group_ids] : [],
    not_uploaded_group_ids: Array.isArray(summary.not_uploaded_group_ids) ? [...summary.not_uploaded_group_ids] : [],
    status: summary.status || "not_uploaded",
    remote_ids: Array.isArray(summary.remote_ids) ? [...summary.remote_ids] : [],
    last_error: summary.last_error || "",
  };
}

export function sub2apiUploadBadge(summary) {
  const s = normalizeSub2APIUploadSummary(summary);
  const uploadedCount = s.uploaded_group_ids.length;
  if (s.status === "uploaded") {
    return { label: `已上传 ${uploadedCount}组`, color: "success", status: "uploaded" };
  }
  if (s.status === "partial") {
    return { label: `部分上传 ${uploadedCount}组`, color: "warning", status: "partial" };
  }
  if (s.status === "error") {
    if (s.last_error && s.last_error.includes("No access token available")) {
      return { label: "No access token", color: "danger", status: "token_error" };
    }
    return { label: "上传异常", color: "danger", status: "error" };
  }
  return { label: "未上传", color: "neutral", status: "not_uploaded" };
}

export function buildSub2APIUploadStatusQuery({ groupIds = [], status = "all", q = "", accountId = null, page = 1, pageSize = 20 } = {}) {
  const params = new URLSearchParams();
  const ids = normalizeSub2APIGroupIds(groupIds);
  if (ids.length) params.set("group_ids", ids.join(","));
  if (status && status !== "all") params.set("status", status);
  if (q) params.set("q", q);
  const normalizedAccountId = Number(accountId);
  if (Number.isInteger(normalizedAccountId) && normalizedAccountId > 0) {
    params.set("account_id", String(normalizedAccountId));
  }
  params.set("page", String(page));
  params.set("page_size", String(pageSize));
  return params.toString();
}

export function classifySub2APIUploadSelection(accounts = [], selectedIds = [], summaryByAccountId = {}) {
  const byId = new Map((accounts || []).map((account) => [Number(account.id), account]));
  const counts = { notUploaded: 0, uploaded: 0, error: 0, unknown: 0, tokenIncomplete: 0 };
  for (const rawId of selectedIds || []) {
    const id = Number(rawId);
    if (!Number.isInteger(id) || id <= 0) continue;
    const account = byId.get(id);
    if (!account) {
      counts.unknown += 1;
      continue;
    }
    if (!account.has_access_token) {
      counts.tokenIncomplete += 1;
      continue;
    }
    const summary = normalizeSub2APIUploadSummary(summaryByAccountId[Number(account.id)]);
    if (summary.status === "uploaded") counts.uploaded += 1;
    else if (summary.status === "partial" || summary.status === "error") counts.error += 1;
    else counts.notUploaded += 1;
  }
  return counts;
}
