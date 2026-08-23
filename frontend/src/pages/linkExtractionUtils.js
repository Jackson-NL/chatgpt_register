export const DEFAULT_LINK_EXTRACTION_FORM = {
  checkout_proxy: "",
  update_proxy: "",
  country: "GB",
  payment_method: "paypal",
  apply_checkout_update: true,
  oaics_only: false,
  concurrency: 2,
};

export const LINK_COUNTRIES = [
  { value: "GB", label: "英国 (GBP)" },
  { value: "US", label: "美国 (USD)" },
  { value: "PH", label: "菲律宾 (PHP)" },
  { value: "ID", label: "印尼 (IDR)" },
  { value: "BR", label: "巴西 (USD)" },
  { value: "DE", label: "德国 (EUR)" },
  { value: "TH", label: "泰国 (USD)" },
  { value: "BA", label: "波黑 (USD)" },
  { value: "NL", label: "荷兰 (EUR)" },
  { value: "AE", label: "阿联酋 (AED)" },
  { value: "DK", label: "丹麦 (DKK)" },
  { value: "JP", label: "日本 (JPY)" },
  { value: "ES", label: "西班牙 (EUR)" },
  { value: "FI", label: "芬兰 (EUR)" },
  { value: "FR", label: "法国 (EUR)" },
];

export const LINK_PAYMENT_METHODS = [
  { value: "paypal", label: "PayPal" },
  { value: "gopay", label: "GoPay" },
  { value: "gcash", label: "GCash" },
];

export function normalizeLinkForm(form = {}) {
  const concurrency = Number(form.concurrency);
  return {
    ...DEFAULT_LINK_EXTRACTION_FORM,
    ...form,
    country: String(form.country || DEFAULT_LINK_EXTRACTION_FORM.country).toUpperCase(),
    payment_method: LINK_PAYMENT_METHODS.some((item) => item.value === form.payment_method) ? form.payment_method : "paypal",
    concurrency: Number.isFinite(concurrency) ? Math.max(1, Math.min(5, Math.trunc(concurrency))) : 2,
    apply_checkout_update: form.apply_checkout_update == null ? true : Boolean(form.apply_checkout_update),
    oaics_only: Boolean(form.oaics_only),
  };
}

export function buildLinkExtractionPayload(accountIds, form = {}) {
  const normalized = normalizeLinkForm(form);
  return { account_ids: [...new Set((accountIds || []).map(Number).filter((id) => Number.isInteger(id) && id > 0))], ...normalized };
}

export function linkJobActive(status) {
  return status === "pending" || status === "running";
}

export function linkStatusMeta(status) {
  if (status === "succeeded") return { label: "成功", color: "success" };
  if (status === "failed") return { label: "失败", color: "danger" };
  if (status === "canceled") return { label: "已取消", color: "warning" };
  if (status === "running") return { label: "运行中", color: "info" };
  return { label: "待执行", color: "neutral" };
}

export function itemStatusMeta(status) {
  if (status === "succeeded") return { label: "成功", color: "success" };
  if (status === "failed") return { label: "失败", color: "danger" };
  if (status === "canceled") return { label: "已取消", color: "warning" };
  if (status === "running") return { label: "提取中", color: "info" };
  return { label: "排队中", color: "neutral" };
}

export function formatAmount(item) {
  if (item?.amount_due == null || !item?.currency) return "-";
  return `${item.amount_due} ${item.currency}`;
}
