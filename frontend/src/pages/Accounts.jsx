import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  Pause, Play, Tag, RefreshCw, Columns3, Save, MoreHorizontal,
  ShieldAlert, KeyRound, FileText, ShieldCheck, Trash2, Copy, Check,
  Loader2, Download, Upload, FileJson,
} from "lucide-react";
import {
  Panel, Button, Badge, DataTable, SearchInput, Select, Input, Modal, Confirm,
  Dropdown, IconBtn, Tag as UITag, Checkbox, useAsyncData, Drawer, Tabs, SecretField,
} from "../components/ui";
import { useApp } from "../context/AppContext";
import { api } from "../api";
import {
  PLANS, fmtAgo, fmtTime,
} from "../mock/data";
import { SUB2API_UPLOAD_STATUS_META, buildSub2APIUploadRequest, classifySub2APIUploadSelection, normalizeSub2APIGroupIds, normalizeSub2APIGroups, normalizeSub2APIUploadJob, normalizeSub2APIUploadSummary, selectSub2APIUploadableIds, sub2apiUploadBadge } from "./sub2apiUtils";

const PLAN_COLOR = { free: "neutral", plus: "info", pro: "primary", team: "warning", enterprise: "danger" };

// 真实凭证状态：有 token 且未过期 → 看浏览器验货结果；无验货记录 → 未验证
const credState = (a) => {
  if (!a.has_access_token) return { label: "无凭证", color: "neutral" };
  if (a.token_expires_at && new Date(a.token_expires_at).getTime() <= Date.now()) return { label: "已过期", color: "danger" };
  if (a.verified_result === "pass") return { label: "存活", color: "success" };
  if (a.verified_result === "fail") return { label: "已失效", color: "danger" };
  return { label: "未验证", color: "warning" };
};

const ACC_STATUS = { active: ["success", "可用"], cooling: ["warning", "冷却中"], paused: ["neutral", "已暂停"], unhealthy: ["danger", "异常"], expired: ["neutral", "已过期"], deleted: ["neutral", "已删除"] };

const shortValue = (value, empty = "—") => value || empty;

// 上传结果中并发设置未同步的账号信息（result 字段不一致，或 errors 里带未同步文案）。
// load_factor 为 None/0 时 Sub2API 会回退到 concurrency，视为可接受，不判未同步。
const sub2apiConcurrencyMismatch = (result) => {
  if (!result) return null;
  const resultMismatch = (result.results || []).find((item) => {
    const remoteConcurrency = Number(item.remote_concurrency);
    const remoteLoadFactor = Number(item.remote_load_factor);
    const concurrencyMismatch = remoteConcurrency !== Number(item.concurrency);
    const loadFactorMismatch = Number.isFinite(remoteLoadFactor) && remoteLoadFactor !== 0 && remoteLoadFactor !== Number(item.concurrency);
    return concurrencyMismatch || loadFactorMismatch;
  });
  if (resultMismatch) {
    return {
      target: resultMismatch.concurrency,
      remoteConcurrency: resultMismatch.remote_concurrency,
      remoteLoadFactor: resultMismatch.remote_load_factor,
    };
  }
  const errorMismatch = (result.errors || []).find((item) => typeof item.error === "string" && item.error.includes("并发设置未同步"));
  if (errorMismatch) {
    const match = errorMismatch.error.match(/远端 concurrency=([^，,]+)[，,]\s*远端 load_factor=([^，,]+)[，,]\s*目标=([^，,]+)/);
    if (match) {
      return { target: match[3], remoteConcurrency: match[1], remoteLoadFactor: match[2] };
    }
  }
  return null;
};
const tokenExpiryText = (a) => {
  if (!a.token_expires_at) return "未记录过期时间";
  const t = new Date(a.token_expires_at).getTime();
  if (Number.isNaN(t)) return "过期时间异常";
  if (t <= Date.now()) return `已于 ${fmtAgo(t)} 过期`;
  const s = Math.max(0, Math.floor((t - Date.now()) / 1000));
  if (s < 60) return `${s} 秒后过期`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟后过期`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时后过期`;
  return `${Math.floor(s / 86400)} 天后过期`;
};
const verificationText = (a) => {
  if (a.verified_result === "pass") return "最近验货通过";
  if (a.verified_result === "fail") return "最近验货失败";
  return "尚未浏览器验货";
};
const credentialFilter = (a) => {
  const state = credState(a).label;
  if (state === "存活") return "alive";
  if (state === "未验证") return "unverified";
  if (state === "已失效" || state === "已过期") return "problem";
  return "missing";
};

const StatPill = ({ label, value, tone = "slate", onClick, active, helper }) => {
  const tones = {
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    red: "border-red-200 bg-red-50 text-red-700",
    slate: "border-slate-200 bg-white text-slate-700",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className={`min-w-[128px] rounded-lg border px-3 py-2 text-left transition hover:-translate-y-px hover:shadow-sm ${tones[tone]} ${active ? "ring-2 ring-blue-200" : ""}`}
      aria-pressed={active ? "true" : "false"}
    >
      <div className="text-[11px] font-medium opacity-75">{label}</div>
      <div className="tnum mt-1 text-xl font-semibold leading-none">{value}</div>
      {helper && <div className="mt-1 truncate text-[11px] opacity-70">{helper}</div>}
    </button>
  );
};

const CredentialLine = ({ label, masked, copyState, onCopy }) => {
  const hasValue = Boolean(masked);
  return (
    <div className={`flex min-w-0 items-center gap-2 rounded-md border px-2 py-1 ${hasValue ? "border-slate-200 bg-white" : "border-slate-100 bg-slate-50"}`}>
      <span className={`w-6 shrink-0 rounded px-1 py-0.5 text-center text-[10px] font-semibold ${hasValue ? "bg-blue-50 text-blue-600" : "bg-slate-100 text-slate-400"}`}>{label}</span>
      <span className={`mono min-w-0 flex-1 truncate ${hasValue ? "text-slate-700" : "text-slate-300"}`} title={hasValue ? `${label} 已保存（脱敏）` : `${label} 缺失`}>
        {shortValue(masked)}
      </span>
      {hasValue && <CopyBtn state={copyState} title={`复制 ${label}`} onCopy={onCopy} />}
    </div>
  );
};

// 复制按钮：busy 转圈 → done 打勾 1.6s → 恢复默认
const CopyBtn = ({ state, title, onCopy }) => (
  <button
    type="button"
    className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-colors ${state === "done" ? "bg-emerald-50 text-emerald-600" : state === "busy" ? "cursor-wait text-slate-300" : "text-slate-400 hover:bg-slate-100 hover:text-slate-700"}`}
    title={state === "done" ? "已复制" : title}
    aria-label={state === "done" ? "已复制" : title}
    disabled={state === "busy"}
    onClick={(e) => { e.stopPropagation(); onCopy(); }}
  >
    {state === "busy" ? <Loader2 size={12} className="animate-spin" /> : state === "done" ? <Check size={12} /> : <Copy size={12} />}
  </button>
);

export default function Accounts() {
  // 列定义放组件内：凭证列渲染/复制需访问组件作用域的 copyState/copyToken
  const ALL_COLUMNS = [
  { key: "email", title: "账号", width: 280, render: (a) => {
    const status = ACC_STATUS[a.status] || ["neutral", a.status || "未知"];
    return (
      <div className="min-w-0 py-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="mono truncate text-[13px] font-semibold text-slate-800" title={a.email || a.phone || `acc_${a.id}`}>
            {a.email || a.phone || "—"}
          </span>
          <Badge color={status[0]} dot className="shrink-0">{status[1]}</Badge>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
          <span className="mono">acc_{a.id}</span>
          {a.phone && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">手机</span>}
          {a.profile_path ? <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-600">Profile 已绑定</span> : <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">缺 Profile</span>}
        </div>
      </div>
    );
  }},
  { key: "plan", title: "计划", width: 86, render: (a) => <Badge color={PLAN_COLOR[a.plan_type] || "neutral"}>{a.plan_type || "—"}</Badge> },
  { key: "tag", title: "标签", width: 130, render: (a) => a.tag ? (
    <Badge color="info">{a.tag}</Badge>
  ) : <span className="text-xs text-slate-300">—</span> },
  { key: "credentialStatus", title: "健康", width: 150, sortable: true, render: (a) => {
    const meta = credState(a);
    return (
      <div className="space-y-1">
        <Badge color={meta.color} dot>{meta.label}</Badge>
        <div className="text-[11px] leading-tight text-slate-400">{verificationText(a)}</div>
      </div>
    );
  }},
  { key: "cred", title: "OAuth 凭据", width: 280, sortable: false, render: (a) => (
    <div className="space-y-1.5">
      <CredentialLine label="AT" masked={a.access_token_masked} copyState={copyState[`${a.id}:at`]} onCopy={() => copyToken(a, "at")} />
      <CredentialLine label="RT" masked={a.refresh_token_masked} copyState={copyState[`${a.id}:rt`]} onCopy={() => copyToken(a, "rt")} />
      <div className={`text-[11px] ${a.token_expires_at && new Date(a.token_expires_at).getTime() <= Date.now() ? "text-red-500" : "text-slate-400"}`}>
        {tokenExpiryText(a)}
      </div>
    </div>
  )},
  { key: "totp", title: "2FA", width: 150, sortable: false, render: (a) => a.totp_secret_masked ? (
    <div className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1">
      <KeyRound size={12} className="shrink-0 text-slate-400" />
      <span className="mono truncate text-slate-700" title="TOTP Secret 已保存（脱敏）">{a.totp_secret_masked}</span>
      <CopyBtn state={copyState[`${a.id}:totp`]} title="复制 TOTP Secret" onCopy={() => copyToken(a, "totp")} />
    </div>
  ) : <Badge color="neutral">未绑定</Badge> },
  { key: "last_check_at", title: "最近验货", width: 118, sortable: true, render: (a) => (
    <div className="text-xs text-slate-500">
      {a.last_check_at ? fmtAgo(new Date(a.last_check_at).getTime()) : <span className="text-slate-300">—</span>}
    </div>
  )},
  { key: "sub2api", title: "Sub2API", width: 150, sortable: false, render: (a) => {
    const badge = sub2apiUploadBadge(a.sub2api_upload_summary);
    return (
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); openSub2apiDetail(a); }}
          title={badge.status === "not_uploaded" ? "尚未上传到 Sub2API（点击查看明细）" : (normalizeSub2APIUploadSummary(a.sub2api_upload_summary).last_error || "点击查看各分组上传明细")}
        >
          <Badge color={badge.color}>{badge.label}</Badge>
        </button>
        <IconBtn icon={<FileJson size={13} />} title="Sub2API 上传明细" onClick={(e) => { e.stopPropagation(); openSub2apiDetail(a); }} />
      </div>
    );
  }},
  { key: "created_at", title: "创建", width: 118, sortable: true, render: (a) => <span className="text-xs text-slate-500">{fmtAgo(new Date(a.created_at).getTime())}</span> },
  ];

  const location = useLocation();
  const preset = location.state?.filter || {};
  const { toast, t } = useApp();
  const { data: raw, loading, error, reload } = useAsyncData(() => api.accounts.list());
  const { data: sub2apiSettings } = useAsyncData(() => api.settings.get().catch(() => null));

  const [q, setQ] = useState(preset.q || "");
  const [fHealth, setFHealth] = useState(preset.health || "all");
  const [fCred, setFCred] = useState("all");
  const [fPlan, setFPlan] = useState("all");
  const [fSource, setFSource] = useState("all");
  const [fProxy, setFProxy] = useState("all");
  const [fRange, setFRange] = useState("all");
  const [visibleCols, setVisibleCols] = useState(ALL_COLUMNS.map((c) => c.key));
  const [viewName, setViewName] = useState("");
  const [savedViews, setSavedViews] = useState([]);
  const [selected, setSelected] = useState([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [sort, setSort] = useState({ key: "", dir: "" });
  const [editAccount, setEditAccount] = useState(null);
  const [batchAction, setBatchAction] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null); // { kind: "single", account } | { kind: "batch", ids }
  const [tagInput, setTagInput] = useState("");
  const [savingView, setSavingView] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // 账号详情抽屉
  const [detail, setDetail] = useState(null);
  const [detailTab, setDetailTab] = useState("info");
  const [detailData, setDetailData] = useState(null);
  const [totpInput, setTotpInput] = useState("");
  const [savingTotp, setSavingTotp] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [transferModal, setTransferModal] = useState(null); // import | export
  const [transferFormat, setTransferFormat] = useState("cpa");
  const [transferFile, setTransferFile] = useState(null);
  const [transferDedup, setTransferDedup] = useState("skip");
  const [transferBusy, setTransferBusy] = useState(false);
  const transferFileRef = useRef(null);
  const [sub2apiModal, setSub2apiModal] = useState(false);
  const [sub2apiGroups, setSub2apiGroups] = useState([]);
  const [sub2apiGroupIds, setSub2apiGroupIds] = useState([]);
  const [sub2apiGroupIdsInput, setSub2apiGroupIdsInput] = useState("");
  const [sub2apiConcurrency, setSub2apiConcurrency] = useState(3);
  const [sub2apiGroupsLoading, setSub2apiGroupsLoading] = useState(false);
  const [sub2apiGroupsError, setSub2apiGroupsError] = useState("");
  const [sub2apiBusy, setSub2apiBusy] = useState(false);
  const [sub2apiUploadJob, setSub2apiUploadJob] = useState(null);
  const [sub2apiResult, setSub2apiResult] = useState(null);
  const sub2apiSelectionDirty = useRef(false);
  // Sub2API 上传状态：筛选 / 同步 / 明细 / 上传选项
  const [fSub2api, setFSub2api] = useState("all");
  const [fRt, setFRt] = useState("all");
  const [fTag, setFTag] = useState("all");
  const [sub2apiSyncModal, setSub2apiSyncModal] = useState(false);
  const [sub2apiSyncBusy, setSub2apiSyncBusy] = useState(false);
  const [sub2apiSyncResult, setSub2apiSyncResult] = useState(null);
  const [sub2apiDetailAccount, setSub2apiDetailAccount] = useState(null);
  const [sub2apiDetailRows, setSub2apiDetailRows] = useState([]);
  const [sub2apiDetailLoading, setSub2apiDetailLoading] = useState(false);
  const [sub2apiDetailError, setSub2apiDetailError] = useState("");
  const [sub2apiUploadOptions, setSub2apiUploadOptions] = useState({ onlyNotUploaded: false, overwriteExisting: true, includeTokenError: false });

  useEffect(() => {
    if (!sub2apiModal && !sub2apiSyncModal) return undefined;
    let alive = true;
    setSub2apiGroupsLoading(true);
    setSub2apiGroupsError("");
    api.sub2api.groups()
      .then((payload) => {
        if (!alive) return;
        const groups = normalizeSub2APIGroups(payload);
        setSub2apiGroups(groups);
        setSub2apiGroupIds((current) => current.length ? current : (groups[0] ? [groups[0].id] : []));
        setSub2apiGroupIdsInput((current) => current || (groups[0] ? String(groups[0].id) : ""));
      })
      .catch((error) => {
        if (alive) setSub2apiGroupsError(error?.message || "Sub2API 分组加载失败");
      })
      .finally(() => {
        if (alive) setSub2apiGroupsLoading(false);
      });
    return () => { alive = false; };
  }, [sub2apiModal, sub2apiSyncModal]);

  useEffect(() => {
    if (!sub2apiModal || !sub2apiSettings || sub2apiSelectionDirty.current) return;
    const configuredGroupIds = normalizeSub2APIGroupIds(sub2apiSettings.sub2api_group_ids);
    if (!configuredGroupIds.length) return;
    setSub2apiGroupIds(configuredGroupIds);
    setSub2apiGroupIdsInput(configuredGroupIds.join(", "));
  }, [sub2apiModal, sub2apiSettings]);

  const rows = useMemo(() => {
    if (!raw) return [];
    let list = raw.filter((a) => {
      if (fHealth !== "all" && a.status !== fHealth) return false;
      if (fPlan !== "all" && a.plan_type !== fPlan) return false;
      if (fCred !== "all" && credentialFilter(a) !== fCred) return false;
      if (fSub2api !== "all") {
        const s = normalizeSub2APIUploadSummary(a.sub2api_upload_summary);
        if (fSub2api === "uploaded" && s.status !== "uploaded") return false;
        if (fSub2api === "partial" && s.status !== "partial") return false;
        if (fSub2api === "not_uploaded" && s.status !== "not_uploaded") return false;
        if (fSub2api === "error" && s.status !== "error") return false;
        if (fSub2api === "token_error" && !(s.status === "error" && s.last_error && s.last_error.includes("No access token available"))) return false;
      }
      if (fRt === "with_rt" && !a.has_refresh_token) return false;
      if (fRt === "no_rt" && a.has_refresh_token) return false;
      if (fTag === "none" && a.tag) return false;
      if (fTag !== "all" && fTag !== "none" && (a.tag || "") !== fTag) return false;
      if (q && !`${a.id} ${a.email || ""} ${a.phone || ""} ${a.proxy || ""} ${a.tag || ""}`.toLowerCase().includes(q.toLowerCase())) return false;
      return true;
    });
    if (sort.key) {
      list = [...list].sort((x, y) => {
        const v = (x[sort.key] || "") > (y[sort.key] || "") ? 1 : (x[sort.key] || "") < (y[sort.key] || "") ? -1 : 0;
        return sort.dir === "asc" ? v : -v;
      });
    }
    return list;
  }, [raw, q, fHealth, fPlan, fCred, fSub2api, fRt, fTag, sort]);

  const tagOptions = useMemo(() => {
    const tags = new Set();
    (raw || []).forEach((a) => { if (a.tag) tags.add(a.tag); });
    return [...tags].sort();
  }, [raw]);

  const pageRows = useMemo(() => rows.slice((page - 1) * pageSize, page * pageSize), [rows, page, pageSize]);
  const stats = useMemo(() => {
    const list = raw || [];
    const count = (fn) => list.filter(fn).length;
    return {
      total: list.length,
      filtered: rows.length,
      withAt: count((a) => a.has_access_token),
      withRt: count((a) => a.has_refresh_token),
      unverified: count((a) => credentialFilter(a) === "unverified"),
      problem: count((a) => credentialFilter(a) === "problem"),
      missingProfile: count((a) => !a.profile_path),
    };
  }, [raw, rows.length]);
  useEffect(() => setPage(1), [q, fHealth, fPlan, fCred, fSub2api, fRt, fTag]);

  const toggleSort = (key) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  const doBatchPause = async (ids) => {
    try {
      const res = await api.accounts.batch({ ids, action: "pause" });
      toast(`已暂停 ${ids.length} 个账号`, "success");
      reload();
    } catch (e) {
      toast(`暂停失败: ${e.message}`, "error");
    }
  };

  const doBatchResume = async (ids) => {
    try {
      const res = await api.accounts.batch({ ids, action: "resume" });
      toast(`已恢复 ${ids.length} 个账号`, "success");
      reload();
    } catch (e) {
      toast(`恢复失败: ${e.message}`, "error");
    }
  };

  const doSingleVerify = async (id) => {
    try {
      const res = await api.accounts.verify(id);
      if (res.ok) {
        const alive = res.result === "pass";
        toast(`账号 ${id} 验货完成: ${alive ? "存活" : "已失效"}（${(res.duration_ms / 1000).toFixed(1)}s）`, alive ? "success" : "error");
      } else {
        toast(`验货失败: ${res.error}`, "error");
      }
      reload();
    } catch (e) {
      toast(`验货失败: ${e.message}`, "error");
    }
  };

  const doBatchVerify = async (ids) => {
    try {
      const res = await api.accounts.batch({ ids, action: "verify" });
      const results = res.results || [];
      const ok = results.filter((r) => r?.ok && r.result === "pass").length;
      const fail = results.length - ok;
      toast(`验货完成: ${results.length} 个 · ${ok} 存活 / ${fail} 失效`, fail === 0 ? "success" : ok > 0 ? "warning" : "error");
      reload();
    } catch (e) {
      toast(`批量验货失败: ${e.message}`, "error");
    }
  };

  const [copyState, setCopyState] = useState({}); // `${id}:at|rt` -> "busy" | "done"
  const tokenCache = useRef(new Map()); // id -> { access_token, refresh_token, totp_secret } 完整凭证缓存
  // 列表刷新（如验货后）后清掉旧缓存，避免复制到过期 token
  useEffect(() => { tokenCache.current.clear(); }, [raw]);

  const writeClipboard = async (text) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    // 非 secure context（如局域网 IP 访问）降级方案
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (!ok) throw new Error("浏览器拒绝了剪贴板写入");
  };

  const TOKEN_LABEL = { at: "Access Token", rt: "Refresh Token", totp: "TOTP Secret" };

  const copyToken = async (a, which) => {
    const key = `${a.id}:${which}`;
    if (copyState[key] === "busy") return; // 防重复点击
    setCopyState((s) => ({ ...s, [key]: "busy" }));
    try {
      let d = tokenCache.current.get(a.id);
      if (!d) {
        d = await api.accounts.detail(a.id);
        tokenCache.current.set(a.id, d);
      }
      const v = which === "at" ? d.access_token : which === "rt" ? d.refresh_token : d.totp_secret;
      if (!v) { toast(`该账号没有 ${TOKEN_LABEL[which]}`, "info"); return; }
      await writeClipboard(v);
      setCopyState((s) => ({ ...s, [key]: "done" }));
      setTimeout(() => setCopyState((s) => { const n = { ...s }; delete n[key]; return n; }), 1600);
      toast(`已复制 ${TOKEN_LABEL[which]}`, "success");
    } catch (e) {
      toast(`复制失败: ${e.message}`, "error");
    } finally {
      setCopyState((s) => { if (s[key] !== "done") { const n = { ...s }; delete n[key]; return n; } return s; });
    }
  };

  const doDelete = async (id) => {
    setDeleting(true);
    try {
      const res = await api.accounts.del(id);
      toast(`已删除账号 ${id}（${res.deleted}）`, "success");
      reload();
    } catch (e) {
      toast(`删除失败: ${e.message}`, "error");
    } finally {
      setDeleting(false);
    }
  };

  const doBatchDelete = async (ids) => {
    setDeleting(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => api.accounts.del(id)));
      const ok = results.filter((r) => r.status === "fulfilled").length;
      toast(`删除完成: 成功 ${ok} / 失败 ${results.length - ok}`, ok === results.length ? "success" : "warning");
      setSelected([]);
      reload();
    } catch (e) {
      toast(`批量删除失败: ${e.message}`, "error");
    } finally {
      setDeleting(false);
    }
  };

  // 打开账号详情抽屉：拉取完整详情
  const openDetail = async (a) => {
    setDetail(a);
    setDetailTab("info");
    setDetailData(null);
    setTotpInput("");
    setDetailBusy(true);
    try {
      const d = await api.accounts.detail(a.id);
      setDetailData(d);
    } catch (e) {
      toast(`加载详情失败: ${e.message}`, "error");
    }
    setDetailBusy(false);
  };

  // 保存备注（真实 PATCH）
  const saveNote = async () => {
    if (!editAccount) return;
    try {
      await api.accounts.patch(editAccount.id, { note: editAccount.note || "" });
      toast(`已保存备注（acc_${editAccount.id}）`, "success");
      setEditAccount(null);
      reload();
    } catch (e) {
      toast(`保存失败: ${e.message}`, "error");
    }
  };

  // 手工写入 / 清空 TOTP
  const saveTotp = async () => {
    if (!detail) return;
    setSavingTotp(true);
    try {
      const d = await api.accounts.writeTotp(detail.id, totpInput.trim());
      setDetailData(d);
      toast(totpInput.trim() ? "TOTP Secret 已写入" : "TOTP 已清空", "success");
      setTotpInput("");
      reload();
    } catch (e) {
      toast(`写入失败: ${e.message}`, "error");
    } finally {
      setSavingTotp(false);
    }
  };

  // 操作列
  const ACTION_COL = {
    key: "_actions",
    title: "操作",
    width: 210,
    render: (a) => (
      <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
        <Button variant="secondary" size="sm" icon={<ShieldCheck size={12} />} onClick={() => doSingleVerify(a.id)} title="浏览器验货">验货</Button>
        <Dropdown trigger={<IconBtn icon={<MoreHorizontal size={14} />} title="更多操作" />} items={[
          { label: "编辑元数据", icon: <FileText size={13} />, onClick: () => setEditAccount(a) },
          { label: a.status === "unhealthy" ? "恢复账号" : "暂停账号", danger: a.status !== "unhealthy",
            icon: a.status === "unhealthy" ? <Play size={13} /> : <Pause size={13} />,
            onClick: () => { setSelected([a.id]); setBatchAction(a.status === "unhealthy" ? "resume" : "pause"); } },
          { divider: true },
          { label: "删除账号", danger: true, icon: <Trash2 size={13} />, onClick: () => setConfirmDelete({ kind: "single", account: a }) },
        ]} />
      </div>
    ),
  };

  const columns = [...ALL_COLUMNS.filter((c) => visibleCols.includes(c.key)).map((c) =>
    c.sortable ? { ...c, sortDir: sort.key === c.key ? sort.dir : null, onClick: () => toggleSort(c.key) } : c,
  ), ACTION_COL];

  const doBatch = (action) => {
    setBatchAction(null);
    if (selected.length === 0) { toast("请先选择账号", "warning"); return; }
    if (action === "verify") doBatchVerify(selected);
    if (action === "pause") doBatchPause(selected);
    if (action === "resume") doBatchResume(selected);
    if (action !== "tag") setSelected([]);
  };

  const [batchTagBusy, setBatchTagBusy] = useState(false);
  const doBatchTag = async () => {
    const tag = (tagInput || "").trim();
    if (!selected.length) { toast("请先选择账号", "warning"); return; }
    setBatchTagBusy(true);
    try {
      const r = await api.accounts.bulkTag({ ids: selected, tag });
      toast(`已为 ${r.updated} 个账号设置标签「${tag || "（清除）"}」`, "success");
      setBatchAction(null);
      setTagInput("");
      reload();
    } catch (e) {
      toast(e?.message || "打标签失败", "error");
    } finally {
      setBatchTagBusy(false);
    }
  };

  const saveView = () => {
    if (!viewName.trim()) { toast("请输入视图名称", "warning"); return; }
    setSavedViews((v) => [...v, { name: viewName, filters: { health: fHealth, plan: fPlan, cred: fCred, rt: fRt }, cols: visibleCols }]);
    setViewName("");
    setSavingView(false);
    toast("视图已保存", "success");
  };

  const openTransfer = (mode) => {
    setTransferModal(mode);
    setTransferFormat("cpa");
    setTransferFile(null);
    setTransferDedup("skip");
  };

  const closeTransfer = () => {
    if (transferBusy) return;
    setTransferModal(null);
    setTransferFile(null);
  };

  const handleTransferFile = (event) => {
    setTransferFile(event.target.files?.[0] || null);
  };

  const doImport = async () => {
    if (!transferFile) {
      toast("请选择要导入的 JSON 文件", "warning");
      return;
    }
    setTransferBusy(true);
    try {
      const content = await transferFile.text();
      const result = await api.accounts.importData({ format: transferFormat, content, dedup: transferDedup });
      toast(
        `导入完成：成功 ${result.success} / 跳过 ${result.skipped} / 失败 ${result.failed}`,
        result.failed > 0 ? "warning" : "success",
      );
      setSelected([]);
      setTransferModal(null);
      setTransferFile(null);
      reload();
    } catch (error) {
      toast(`导入失败: ${error.message}`, "error");
    } finally {
      setTransferBusy(false);
    }
  };

  const doExport = async () => {
    if (selected.length === 0) {
      toast("请先选择要导出的账号", "warning");
      return;
    }
    setTransferBusy(true);
    try {
      const result = await api.accounts.exportData({ ids: selected, format: transferFormat });
      const blob = new Blob([result.content], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename || `accounts_${transferFormat}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast(`已导出 ${result.count} 个账号 · ${transferFormat} 格式`, "success");
      setTransferModal(null);
    } catch (error) {
      toast(`导出失败: ${error.message}`, "error");
    } finally {
      setTransferBusy(false);
    }
  };

  const openSub2API = () => {
    if (selected.length === 0) {
      toast("请先选择要上传的账号", "warning");
      return;
    }
    sub2apiSelectionDirty.current = false;
    const configuredGroupIds = normalizeSub2APIGroupIds(sub2apiSettings?.sub2api_group_ids);
    setSub2apiGroupIds(configuredGroupIds);
    setSub2apiGroupIdsInput(configuredGroupIds.join(", "));
    setSub2apiConcurrency(3);
    setSub2apiResult(null);
    setSub2apiGroupsError("");
    setSub2apiModal(true);
  };

  const closeSub2API = () => {
    if (sub2apiBusy) return;
    setSub2apiModal(false);
    setSub2apiResult(null);
    setSub2apiUploadJob(null);
  };

  const openSub2apiSync = () => {
    sub2apiSelectionDirty.current = false;
    const configuredGroupIds = normalizeSub2APIGroupIds(sub2apiSettings?.sub2api_group_ids);
    setSub2apiGroupIds(configuredGroupIds);
    setSub2apiGroupIdsInput(configuredGroupIds.join(", "));
    setSub2apiSyncResult(null);
    setSub2apiGroupsError("");
    setSub2apiSyncModal(true);
  };

  const closeSub2apiSync = () => {
    if (sub2apiSyncBusy) return;
    setSub2apiSyncModal(false);
    setSub2apiSyncResult(null);
  };

  const doSub2apiSync = async () => {
    const groupIds = sub2apiGroupIdsInput.trim()
      ? normalizeSub2APIGroupIds(sub2apiGroupIdsInput)
      : normalizeSub2APIGroupIds(sub2apiGroupIds);
    if (groupIds.length === 0) {
      toast("请至少指定一个有效的 Sub2API 分组 ID", "warning");
      return;
    }
    setSub2apiSyncBusy(true);
    try {
      const result = await api.sub2api.syncUploadStatus({ group_ids: groupIds });
      setSub2apiSyncResult(result);
      toast(`Sub2API 状态同步完成：上传 ${result.uploaded} / 未上传 ${result.not_uploaded} / No access token ${result.token_error} / 远端异常 ${result.remote_error}`, "success");
      reload();
    } catch (error) {
      toast(`Sub2API 状态同步失败: ${error.message}`, "error");
    } finally {
      setSub2apiSyncBusy(false);
    }
  };

  const openSub2apiDetail = async (a) => {
    setSub2apiDetailAccount(a);
    setSub2apiDetailRows([]);
    setSub2apiDetailError("");
    setSub2apiDetailLoading(true);
    try {
      const res = await api.sub2api.uploadStatus({ accountId: a.id, pageSize: 100 });
      setSub2apiDetailRows(res.items || []);
    } catch (error) {
      setSub2apiDetailError(error?.message || "加载 Sub2API 上传明细失败");
    } finally {
      setSub2apiDetailLoading(false);
    }
  };

  const doSub2APIUpload = async () => {
    const uploadSelection = selectSub2APIUploadableIds(raw || [], selected);
    if (!uploadSelection.ids.length) {
      toast("所选账号的 OAuth token 不完整，请先补 Codex OAuth 后再上传", "warning");
      return;
    }
    const groupIds = sub2apiGroupIdsInput.trim()
      ? normalizeSub2APIGroupIds(sub2apiGroupIdsInput)
      : normalizeSub2APIGroupIds(sub2apiGroupIds);
    if (groupIds.length === 0) {
      toast("请至少指定一个有效的 Sub2API 目标分组 ID", "warning");
      return;
    }
    setSub2apiBusy(true);
    setSub2apiUploadJob(null);
    try {
      const created = await api.sub2api.createUploadJob(buildSub2APIUploadRequest(uploadSelection.ids, groupIds, sub2apiConcurrency, sub2apiUploadOptions));
      let job = normalizeSub2APIUploadJob(created);
      setSub2apiUploadJob(job);
      while (job.status === "pending" || job.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 750));
        job = normalizeSub2APIUploadJob(await api.sub2api.uploadJob(created.job_id));
        setSub2apiUploadJob(job);
      }
      if (job.status !== "completed" || !job.result) {
        throw new Error(job.error || "Sub2API 上传任务失败");
      }
      const result = job.result;
      setSub2apiResult(result);
      const skippedNote = result.skipped && result.skipped.length ? ` / 过滤跳过 ${result.skipped.length}` : "";
      toast(
        `Sub2API 上传完成：成功 ${result.success} / 失败 ${result.failed}${uploadSelection.skipped.length ? ` / 跳过 token 不完整 ${uploadSelection.skipped.length}` : ""}${skippedNote}`,
        result.failed > 0 || uploadSelection.skipped.length > 0 || (result.skipped && result.skipped.length) ? "warning" : "success",
      );
      reload();
    } catch (error) {
      toast(`Sub2API 上传失败: ${error.message}`, "error");
    } finally {
      setSub2apiBusy(false);
    }
  };

  const activeSub2APIGroupIds = sub2apiGroupIdsInput.trim()
    ? normalizeSub2APIGroupIds(sub2apiGroupIdsInput)
    : normalizeSub2APIGroupIds(sub2apiGroupIds);
  const sub2apiUploadSelection = useMemo(
    () => selectSub2APIUploadableIds(raw || [], selected),
    [raw, selected],
  );
  const sub2apiSummaryByAccountId = useMemo(() => {
    const map = {};
    for (const a of raw || []) map[Number(a.id)] = a.sub2api_upload_summary;
    return map;
  }, [raw]);
  const sub2apiUploadCounts = useMemo(
    () => classifySub2APIUploadSelection(raw || [], selected, sub2apiSummaryByAccountId),
    [raw, selected, sub2apiSummaryByAccountId],
  );
  const sub2apiResultActions = useMemo(() => {
    const counts = { created: 0, updated: 0, other: 0 };
    for (const item of sub2apiResult?.results || []) {
      if (item.action === "created") counts.created += 1;
      else if (item.action === "updated") counts.updated += 1;
      else counts.other += 1;
    }
    return counts;
  }, [sub2apiResult]);

  const toggleSub2APIGroup = (groupId) => {
    sub2apiSelectionDirty.current = true;
    const current = activeSub2APIGroupIds;
    const next = current.includes(groupId) ? current.filter((id) => id !== groupId) : [...current, groupId];
    setSub2apiGroupIds(next);
    setSub2apiGroupIdsInput(next.join(", "));
  };

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-blue-600">
              <ShieldCheck size={13} /> AccountOps
            </div>
            <h1 className="mt-1 text-lg font-semibold text-slate-900">账号管理</h1>
            <p className="mt-1 text-xs text-slate-500">集中查看账号、OAuth 凭据、2FA、profile 和浏览器验货状态；列表只展示脱敏值。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatPill label="账号总数" value={stats.total} tone="blue" helper={`当前筛选 ${stats.filtered}`} onClick={() => { setFCred("all"); setFHealth("all"); }} active={fCred === "all" && fHealth === "all"} />
            <StatPill label="已保存 AT / RT" value={`${stats.withAt}/${stats.withRt}`} tone="emerald" helper="可复制完整值" />
            <StatPill label="未验证" value={stats.unverified} tone="amber" helper="点击筛选" onClick={() => setFCred("unverified")} active={fCred === "unverified"} />
            <StatPill label="异常 / 过期" value={stats.problem} tone="red" helper={`缺 Profile ${stats.missingProfile}`} onClick={() => setFCred("problem")} active={fCred === "problem"} />
          </div>
        </div>
      </div>

      {/* 工具栏 */}
      <Panel pad={false}>
        <div className="flex flex-wrap items-center gap-2 p-3">
          <SearchInput value={q} onChange={setQ} placeholder="检索 ID / 邮箱 / 手机 / 代理…" className="min-w-[260px]" />
          <Select options={[{ value: "all", label: "全部状态" }, { value: "active", label: "健康" }, { value: "cooling", label: "冷却中" }, { value: "paused", label: "已暂停" }, { value: "unhealthy", label: "异常" }]}
            value={fHealth} onChange={setFHealth} className="w-28" />
          <Select options={[{ value: "all", label: "全部凭据" }, { value: "alive", label: "存活" }, { value: "unverified", label: "未验证" }, { value: "problem", label: "异常/过期" }, { value: "missing", label: "无凭证" }]}
            value={fCred} onChange={setFCred} className="w-32" />
          <Select options={[{ value: "all", label: "全部计划" }, ...PLANS.map((p) => ({ value: p, label: p }))]}
            value={fPlan} onChange={setFPlan} className="w-28" />
          <Select options={[
            { value: "all", label: "全部 Sub2API" },
            { value: "not_uploaded", label: "未上传" },
            { value: "uploaded", label: "已上传" },
            { value: "partial", label: "部分上传" },
            { value: "error", label: "上传异常" },
            { value: "token_error", label: "No access token" },
          ]} value={fSub2api} onChange={setFSub2api} className="w-36" />
          <Select options={[{ value: "all", label: "全部 RT" }, { value: "with_rt", label: "有 RT" }, { value: "no_rt", label: "无 RT" }]}
            value={fRt} onChange={setFRt} className="w-28" />
          <Select options={[{ value: "all", label: "全部标签" }, { value: "none", label: "无标签" }, ...tagOptions.map((tg) => ({ value: tg, label: tg }))]}
            value={fTag} onChange={setFTag} className="w-36" />
          <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
            <Button variant="secondary" size="sm" icon={<RefreshCw size={13} />} onClick={openSub2apiSync} title="拉取远端 Sub2API 账号并按邮箱匹配，刷新本地上传状态">同步 Sub2API 状态</Button>
            <Button variant="secondary" size="sm" icon={<Upload size={13} />} onClick={() => openTransfer("import")}>导入</Button>
            <Button variant="secondary" size="sm" icon={<Download size={13} />} onClick={() => openTransfer("export")}>导出</Button>
            <Button variant="primary" size="sm" icon={<Upload size={13} />} onClick={openSub2API} title="将所选账号上传到 Sub2API 分组">上传到 Sub2API</Button>
            <Dropdown trigger={<Button variant="secondary" size="sm" icon={<Columns3 size={13} />}>列</Button>}
              items={ALL_COLUMNS.map((c) => ({
                label: (
                  <span className="flex items-center gap-2"><Checkbox checked={visibleCols.includes(c.key)} onChange={(v) => setVisibleCols((cols) => v ? [...cols, c.key] : cols.filter((k) => k !== c.key))} />{c.title}</span>
                ),
                onClick: () => {},
              }))} />
            <Button variant="secondary" size="sm" icon={<Save size={13} />} onClick={() => setSavingView(true)}>保存视图</Button>
            {savedViews.length > 0 && (
              <Dropdown trigger={<Button variant="secondary" size="sm">视图 ▾</Button>}
                items={savedViews.map((v) => ({ label: v.name, onClick: () => { setFHealth(v.filters.health); setFPlan(v.filters.plan); setFCred(v.filters.cred || "all"); setFRt(v.filters.rt || "all"); setVisibleCols(v.cols); toast(`已应用视图「${v.name}」`, "info"); } }))} />
            )}
          </div>
        </div>
        {selected.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 bg-blue-50/60 px-3 py-2 text-xs">
            <span className="rounded-full bg-blue-100 px-2 py-1 text-blue-700">已选 <b className="tnum">{selected.length}</b> 个账号</span>
            <Button size="sm" variant="secondary" icon={<ShieldCheck size={13} />} onClick={() => doBatch("verify")}>验货所选</Button>
            <Button size="sm" variant="secondary" icon={<Pause size={13} />} onClick={() => setBatchAction("pause")}>暂停</Button>
            <Button size="sm" variant="secondary" icon={<Play size={13} />} onClick={() => setBatchAction("resume")}>恢复</Button>
            <Button size="sm" variant="secondary" icon={<Tag size={13} />} onClick={() => setBatchAction("tag")}>打标签</Button>
            <Button size="sm" variant="primary" icon={<Upload size={13} />} onClick={openSub2API}>上传到 Sub2API</Button>
            <Button size="sm" variant="danger" icon={<Trash2 size={13} />} onClick={() => setConfirmDelete({ kind: "batch", ids: selected })}>删除所选</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected([])}>取消选择</Button>
          </div>
        )}
      </Panel>

      {/* 数据表 */}
      <Panel pad={false} className="overflow-hidden shadow-sm">
        <DataTable
          columns={columns}
          data={pageRows}
          loading={loading}
          error={error}
          onRetry={reload}
          selectable
          selected={selected}
          onSelectAll={(v) => setSelected(v ? pageRows.map((r) => r.id) : [])}
          onSelectRow={(id, v) => setSelected((s) => v ? [...s, id] : s.filter((x) => x !== id))}
          page={page} pageSize={pageSize} total={rows.length}
          onPage={setPage} onPageSize={setPageSize}
          rowClick={(a) => openDetail(a)}
          emptyTitle="还没有账号，去创建注册任务"
          toolbar={
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50/70 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span>{t(`共 ${rows.length} 个账号`)}</span>
                {fCred !== "all" && <Badge color="info">凭据筛选中</Badge>}
                {fRt !== "all" && <Badge color="info">{fRt === "with_rt" ? "有 RT" : "无 RT"}筛选中</Badge>}
                {fTag !== "all" && <Badge color="info">{fTag === "none" ? "无标签" : `标签：${fTag}`}</Badge>}
                {q && <Badge color="neutral">搜索：{q}</Badge>}
              </div>
              <div className="flex items-center gap-1.5">
                <Button variant="ghost" size="sm" icon={<RefreshCw size={13} />} onClick={reload}>刷新</Button>
              </div>
            </div>
          }
        />
      </Panel>

      <Modal
        open={!!transferModal}
        onClose={closeTransfer}
        title={transferModal === "import" ? "导入账号" : "导出账号"}
        width={540}
        footer={
          <>
            <Button variant="secondary" onClick={closeTransfer} disabled={transferBusy}>取消</Button>
            {transferModal === "import" ? (
              <Button icon={<Upload size={13} />} onClick={doImport} loading={transferBusy}>开始导入</Button>
            ) : (
              <Button icon={<Download size={13} />} onClick={doExport} loading={transferBusy}>下载导出文件</Button>
            )}
          </>
        }>
        <Tabs
          size="sm"
          active={transferFormat}
          onChange={setTransferFormat}
          tabs={[{ key: "cpa", label: "CPA" }, { key: "sub2api", label: "Sub2API" }]}
        />
        {transferModal === "import" ? (
          <div className="space-y-4 pt-4">
            <div className="rounded-md border border-blue-100 bg-blue-50/60 p-3 text-xs leading-relaxed text-blue-800">
              支持 Cockpit Tools 导出的 CPA 或 Sub2API JSON。导入内容会写入账号凭据；页面不会展示文件中的完整 token。
            </div>
            <input ref={transferFileRef} type="file" accept=".json,application/json" className="hidden" onChange={handleTransferFile} />
            <div className="flex items-center gap-3 rounded-md border border-slate-200 px-3 py-3">
              <FileJson size={20} className="shrink-0 text-slate-400" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] text-slate-700">{transferFile?.name || "尚未选择 JSON 文件"}</div>
                <div className="mt-0.5 text-[11px] text-slate-400">{transferFile ? `${(transferFile.size / 1024).toFixed(1)} KB` : "请选择 CPA 或 Sub2API 导出文件"}</div>
              </div>
              <Button variant="secondary" size="sm" icon={<Upload size={13} />} onClick={() => transferFileRef.current?.click()}>选择文件</Button>
            </div>
            <Select
              label="重复账号处理"
              value={transferDedup}
              onChange={setTransferDedup}
              options={[
                { value: "skip", label: "跳过已存在账号" },
                { value: "overwrite", label: "覆盖已有凭据" },
              ]}
            />
          </div>
        ) : (
          <div className="space-y-4 pt-4">
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-800">
              将导出所选账号的完整 OAuth 凭据、密码和 2FA 信息。文件可被其他工具直接导入，请按密码级别保管。
            </div>
            <div className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-3 text-[13px] text-slate-600">
              <span>待导出账号</span>
              <Badge color={selected.length ? "info" : "warning"} dot>{selected.length ? `${selected.length} 个` : "未选择"}</Badge>
            </div>
            {!selected.length && <div className="text-xs text-red-600">请先在账号列表勾选账号，再下载导出文件。</div>}
          </div>
        )}
      </Modal>

      <Modal
        open={sub2apiModal}
        onClose={closeSub2API}
        title="上传到 Sub2API"
        width={560}
        footer={
          <>
            <Button variant="secondary" onClick={closeSub2API} disabled={sub2apiBusy}>关闭</Button>
            {sub2apiResult && <Button variant="secondary" icon={<RefreshCw size={13} />} onClick={() => { setSub2apiResult(null); setSub2apiUploadJob(null); }} disabled={sub2apiBusy}>重新上传</Button>}
            {!sub2apiResult && <Button icon={<Upload size={13} />} onClick={doSub2APIUpload} loading={sub2apiBusy} disabled={!activeSub2APIGroupIds.length || sub2apiGroupsLoading}>开始上传</Button>}
          </>
        }>
        <div className="space-y-4">
          <div className="rounded-md border border-blue-100 bg-blue-50/60 p-3 text-xs leading-relaxed text-blue-800">
            将所选账号一次上传到多个指定的 Sub2API 分组。每个账号只创建一次，后端通过 group_ids 同时绑定多个分组；账号凭据会包含 OAuth access_token、refresh_token、id_token、邮箱、密码和 2FA，页面不会展示这些字段。
          </div>
          <div className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-3 text-[13px] text-slate-600">
            <span>待上传账号</span>
            <div className="flex flex-wrap justify-end gap-2">
              <Badge color={sub2apiUploadSelection.ids.length ? "info" : "warning"} dot>{sub2apiUploadSelection.ids.length ? `可上传 ${sub2apiUploadSelection.ids.length} 个` : "无可上传账号"}</Badge>
              {sub2apiUploadSelection.skipped.length > 0 && <Badge color="warning" dot>token 不完整跳过 {sub2apiUploadSelection.skipped.length} 个</Badge>}
            </div>
          </div>
          <div className="rounded-md border border-slate-200 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
              <span className="text-[13px] text-slate-600">已选账号上传状态（本地记录）</span>
              <div className="flex flex-wrap justify-end gap-2">
                <Badge color={sub2apiUploadCounts.uploaded ? "success" : "neutral"} dot>已上传 {sub2apiUploadCounts.uploaded}</Badge>
                <Badge color={sub2apiUploadCounts.notUploaded ? "info" : "neutral"} dot>未上传 {sub2apiUploadCounts.notUploaded}</Badge>
                <Badge color={sub2apiUploadCounts.error ? "danger" : "neutral"} dot>异常 {sub2apiUploadCounts.error}</Badge>
                {sub2apiUploadCounts.tokenIncomplete > 0 && <Badge color="warning" dot>token 不完整 {sub2apiUploadCounts.tokenIncomplete}</Badge>}
              </div>
            </div>
            <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-slate-600">
              <label className="flex cursor-pointer items-center gap-1.5">
                <Checkbox checked={sub2apiUploadOptions.onlyNotUploaded} onChange={(v) => setSub2apiUploadOptions((o) => ({ ...o, onlyNotUploaded: v }))} />
                只上传未上传
              </label>
              <label className="flex cursor-pointer items-center gap-1.5">
                <Checkbox checked={sub2apiUploadOptions.overwriteExisting} onChange={(v) => setSub2apiUploadOptions((o) => ({ ...o, overwriteExisting: v }))} />
                覆盖更新已上传账号
              </label>
              <label className="flex cursor-pointer items-center gap-1.5">
                <Checkbox checked={sub2apiUploadOptions.includeTokenError} onChange={(v) => setSub2apiUploadOptions((o) => ({ ...o, includeTokenError: v }))} />
                包含 No access token 账号重传
              </label>
            </div>
            <div className="mt-1.5 text-[11px] text-slate-400">按上次同步的本地记录过滤；勾选「只上传未上传」后，任一目标分组已上传的账号都会被跳过。</div>
          </div>
          {sub2apiGroupsLoading ? (
            <div className="text-xs text-slate-400">正在加载 Sub2API 分组...</div>
          ) : null}
          {sub2apiGroupsError && (
            <div className="rounded-md border border-red-100 bg-red-50 p-3 text-xs leading-relaxed text-red-700">{sub2apiGroupsError}。仍可直接输入目标分组 ID。</div>
          )}
          {sub2apiGroups.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-600">选择目标分组</div>
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-2">
                {sub2apiGroups.map((group) => (
                  <label key={group.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-700 hover:bg-slate-50">
                    <Checkbox
                      checked={activeSub2APIGroupIds.includes(group.id)}
                      onChange={() => toggleSub2APIGroup(group.id)}
                    />
                    <span>{group.name}</span>
                    <span className="ml-auto text-slate-400">#{group.id}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <Input
            label="指定分组 ID（可填多个）"
            type="text"
            value={sub2apiGroupIdsInput}
            onChange={(event) => {
              sub2apiSelectionDirty.current = true;
              setSub2apiGroupIdsInput(event.target.value);
            }}
            placeholder="例如 42, 43, 108"
          />
          <Input
            label="账号并发数"
            type="number"
            min={1}
            max={20}
            value={sub2apiConcurrency}
            onChange={(event) => setSub2apiConcurrency(event.target.value)}
            hint="该值会写入 Sub2API 账号 concurrency 字段（范围 1–20）。"
          />
          <div className="text-[11px] text-slate-400">默认并发数为 3，可在此临时调整。可勾选分组或输入逗号分隔的 ID；已有远端账号会复用并保留原分组，再合并本次目标分组。</div>
          {sub2apiUploadJob && !sub2apiResult && (
            <div className="min-h-[126px] rounded-md border border-blue-200 bg-blue-50/50 p-3" aria-live="polite">
              <div className="flex items-center justify-between gap-3 text-xs text-slate-600">
                <span className="font-medium">上传进度</span>
                <span className="tnum font-semibold text-blue-700">{sub2apiUploadJob.progress}%</span>
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-blue-100">
                <div className="h-full rounded-full bg-blue-600 transition-[width] duration-300" style={{ width: `${sub2apiUploadJob.progress}%` }} />
              </div>
              <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[11px] text-slate-500">
                <span>已处理 {sub2apiUploadJob.processed} / {sub2apiUploadJob.total}</span>
                <span className="text-emerald-700">成功 {sub2apiUploadJob.success}</span>
                <span className="text-red-700">失败 {sub2apiUploadJob.failed}</span>
              </div>
              <div className="mt-2 truncate text-[11px] text-slate-500" title={sub2apiUploadJob.current_email}>
                当前账号：{sub2apiUploadJob.current_email || (sub2apiUploadJob.status === "pending" ? "准备中" : "处理中")}
              </div>
              {sub2apiUploadJob.error && <div className="mt-2 text-xs text-red-700">{sub2apiUploadJob.error}</div>}
            </div>
          )}
          {sub2apiResult && (
            <div className="space-y-3 border-t border-slate-100 pt-4">
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-md bg-emerald-50 px-2 py-2"><div className="text-lg font-semibold text-emerald-700">{sub2apiResult.success}</div><div className="text-[11px] text-emerald-600">成功</div></div>
                <div className="rounded-md bg-red-50 px-2 py-2"><div className="text-lg font-semibold text-red-700">{sub2apiResult.failed}</div><div className="text-[11px] text-red-600">失败</div></div>
                <div className="rounded-md bg-slate-50 px-2 py-2"><div className="text-lg font-semibold text-slate-700">{sub2apiResult.count}</div><div className="text-[11px] text-slate-500">处理</div></div>
              </div>
              {(sub2apiResult.skipped || []).length > 0 && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  <div className="font-medium">按过滤规则跳过 {sub2apiResult.skipped.length} 个账号：</div>
                  <div className="mt-1 max-h-24 space-y-0.5 overflow-y-auto">
                    {sub2apiResult.skipped.slice(0, 20).map((item, index) => (
                      <div key={`${item.account_id}-${index}`}>{item.account_id} · {item.email || "本地账号"} · {item.reason}</div>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
                <span>新建 {sub2apiResultActions.created}</span>
                <span>更新 {sub2apiResultActions.updated}</span>
                {sub2apiResultActions.other > 0 && <span>其他成功 {sub2apiResultActions.other}</span>}
                <span>目标分组 {Array.isArray(sub2apiResult.group_ids) ? sub2apiResult.group_ids.join(", ") : "—"}</span>
              </div>
              {(sub2apiResult.results || []).length > 0 && (
                <div className="rounded-md border border-emerald-100 bg-emerald-50/60 px-3 py-2 text-xs text-emerald-800">
                  <div className="font-medium">已完成账号</div>
                  <div className="mt-1 max-h-28 space-y-0.5 overflow-y-auto">
                    {sub2apiResult.results.slice(0, 20).map((item, index) => {
                      const targetGroups = Array.isArray(item.group_ids) ? item.group_ids.join(", ") : "—";
                      const remoteGroups = Array.isArray(item.remote_group_ids) && item.remote_group_ids.length
                        ? item.remote_group_ids.join(", ")
                        : "未返回";
                      const action = item.action === "updated" ? "更新" : item.action === "created" ? "新建" : "同步";
                      return (
                        <div key={`${item.account_id}-${index}`}>
                          {item.account_id} · {item.email || "本地账号"} · {action} · 目标分组 {targetGroups} · 远端分组 {remoteGroups}
                        </div>
                      );
                    })}
                  </div>
                  {sub2apiResult.results.length > 20 && <div className="mt-1 text-[11px] text-emerald-700">其余 {sub2apiResult.results.length - 20} 个账号已省略。</div>}
                </div>
              )}
              <div className="text-[11px] text-slate-500">本次上传写入的并发数：<span className="tnum font-semibold text-slate-700">{sub2apiResult.concurrency ?? 3}</span></div>
              {sub2apiResult && (() => {
                const mismatch = sub2apiConcurrencyMismatch(sub2apiResult);
                if (!mismatch) return null;
                return (
                  <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-medium leading-relaxed text-amber-800">
                    远端并发设置未同步：目标 {mismatch.target}，远端 concurrency={mismatch.remoteConcurrency ?? "未知"}，远端 load_factor={mismatch.remoteLoadFactor ?? "未知"}，请检查 Sub2API 更新接口兼容性。
                  </div>
                );
              })()}
              {(sub2apiResult.errors || []).length > 0 && (
                <div className="space-y-1 text-xs text-red-700">
                  {sub2apiResult.errors.slice(0, 8).map((item, index) => <div key={`${item.account_id}-${index}`}>{item.account_id} · {item.email || "本地账号"} · {item.error}</div>)}
                </div>
              )}
            </div>
          )}
        </div>
      </Modal>

      {/* 同步 Sub2API 状态 */}
      <Modal
        open={sub2apiSyncModal}
        onClose={closeSub2apiSync}
        title="同步 Sub2API 状态"
        width={560}
        footer={
          <>
            <Button variant="secondary" onClick={closeSub2apiSync} disabled={sub2apiSyncBusy}>关闭</Button>
            <Button icon={sub2apiSyncBusy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} onClick={doSub2apiSync} loading={sub2apiSyncBusy} disabled={!activeSub2APIGroupIds.length || sub2apiGroupsLoading}>开始同步</Button>
          </>
        }>
        <div className="space-y-4">
          <div className="rounded-md border border-blue-100 bg-blue-50/60 p-3 text-xs leading-relaxed text-blue-800">
            拉取所选分组的远端 Sub2API 账号，按邮箱（小写）匹配本地账号，把「已上传 / 未上传 / No access token / 远端异常 / 分组不匹配」写入本地并持久化。之后无需再次同步也能看到上次上传结果；同步只是刷新远端状态。
          </div>
          {sub2apiGroupsLoading ? (
            <div className="text-xs text-slate-400">正在加载 Sub2API 分组...</div>
          ) : null}
          {sub2apiGroupsError && (
            <div className="rounded-md border border-red-100 bg-red-50 p-3 text-xs leading-relaxed text-red-700">{sub2apiGroupsError}。仍可直接输入分组 ID。</div>
          )}
          {sub2apiGroups.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-slate-600">选择要同步的分组</div>
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-slate-200 p-2">
                {sub2apiGroups.map((group) => (
                  <label key={group.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-xs text-slate-700 hover:bg-slate-50">
                    <Checkbox
                      checked={activeSub2APIGroupIds.includes(group.id)}
                      onChange={() => toggleSub2APIGroup(group.id)}
                    />
                    <span>{group.name}</span>
                    <span className="ml-auto text-slate-400">#{group.id}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <Input
            label="指定分组 ID（可填多个）"
            type="text"
            value={sub2apiGroupIdsInput}
            onChange={(event) => {
              sub2apiSelectionDirty.current = true;
              setSub2apiGroupIdsInput(event.target.value);
            }}
            placeholder="例如 42, 43, 108"
          />
          <div className="text-[11px] text-slate-400">同步会为每个本地账号 × 每个目标分组写入一条状态；重复同步走更新，不会重复插入。</div>
          {sub2apiSyncResult && (
            <div className="space-y-3 border-t border-slate-100 pt-4">
              <div className="grid grid-cols-4 gap-2 text-center">
                <div className="rounded-md bg-emerald-50 px-2 py-2"><div className="text-lg font-semibold text-emerald-700">{sub2apiSyncResult.uploaded}</div><div className="text-[11px] text-emerald-600">已上传</div></div>
                <div className="rounded-md bg-slate-50 px-2 py-2"><div className="text-lg font-semibold text-slate-700">{sub2apiSyncResult.not_uploaded}</div><div className="text-[11px] text-slate-500">未上传</div></div>
                <div className="rounded-md bg-red-50 px-2 py-2"><div className="text-lg font-semibold text-red-700">{sub2apiSyncResult.token_error}</div><div className="text-[11px] text-red-600">No access token</div></div>
                <div className="rounded-md bg-amber-50 px-2 py-2"><div className="text-lg font-semibold text-amber-700">{sub2apiSyncResult.remote_error}</div><div className="text-[11px] text-amber-600">远端异常</div></div>
              </div>
              <div className="text-[11px] text-slate-500">
                本地账号 {sub2apiSyncResult.total_local} · 匹配远端 {sub2apiSyncResult.matched_remote} · 上传异常 {sub2apiSyncResult.uploaded_error} · 分组不匹配 {sub2apiSyncResult.group_mismatch}
              </div>
            </div>
          )}
        </div>
      </Modal>

      {/* Sub2API 上传明细 */}
      <Modal
        open={!!sub2apiDetailAccount}
        onClose={() => setSub2apiDetailAccount(null)}
        title={sub2apiDetailAccount ? `Sub2API 上传明细 · ${sub2apiDetailAccount.email || sub2apiDetailAccount.phone || `acc_${sub2apiDetailAccount.id}`}` : ""}
        width={760}
        footer={<>
          <Button variant="secondary" onClick={() => setSub2apiDetailAccount(null)}>关闭</Button>
          <Button variant="secondary" icon={<RefreshCw size={13} />} onClick={() => sub2apiDetailAccount && openSub2apiDetail(sub2apiDetailAccount)}>刷新</Button>
        </>}>
        {sub2apiDetailLoading ? (
          <div className="py-8 text-center text-xs text-slate-400"><Loader2 size={16} className="mx-auto mb-2 animate-spin" />正在加载上传明细…</div>
        ) : sub2apiDetailError ? (
          <div className="rounded-md border border-red-100 bg-red-50 p-3 text-xs leading-relaxed text-red-700">{sub2apiDetailError}</div>
        ) : sub2apiDetailRows.length === 0 ? (
          <div className="py-8 text-center text-xs text-slate-400">暂无上传记录（尚未同步或从未上传）。点击「同步 Sub2API 状态」后可查看各分组明细。</div>
        ) : (
          <div className="max-h-[60vh] overflow-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-left text-slate-500">
                  <th className="px-2 py-2">分组</th>
                  <th className="px-2 py-2">状态</th>
                  <th className="px-2 py-2">远端 ID</th>
                  <th className="px-2 py-2">AT / RT</th>
                  <th className="px-2 py-2">并发 / 负载</th>
                  <th className="px-2 py-2">最近核验</th>
                  <th className="px-2 py-2">错误信息</th>
                </tr>
              </thead>
              <tbody>
                {sub2apiDetailRows.map((row) => {
                  const meta = SUB2API_UPLOAD_STATUS_META[row.status] || { label: row.status, color: "neutral" };
                  return (
                    <tr key={row.id} className="border-b border-slate-100 align-top">
                      <td className="px-2 py-2"><span className="font-medium text-slate-700">{row.group_name || `分组 ${row.group_id}`}</span><div className="text-[11px] text-slate-400">#{row.group_id}</div></td>
                      <td className="px-2 py-2"><Badge color={meta.color}>{meta.label}</Badge></td>
                      <td className="px-2 py-2"><span className="mono text-slate-600">{row.remote_id || "—"}</span></td>
                      <td className="px-2 py-2">
                        <span className={row.has_access_token ? "text-emerald-600" : "text-slate-400"}>{row.has_access_token === true ? "✓" : row.has_access_token === false ? "✗" : "—"}</span>
                        {" / "}
                        <span className={row.has_refresh_token ? "text-emerald-600" : "text-slate-400"}>{row.has_refresh_token === true ? "✓" : row.has_refresh_token === false ? "✗" : "—"}</span>
                      </td>
                      <td className="px-2 py-2 tnum text-slate-600">{row.remote_concurrency ?? "—"} / {row.remote_load_factor ?? "—"}</td>
                      <td className="px-2 py-2 text-slate-500">{row.verified_at ? fmtAgo(new Date(row.verified_at).getTime()) : "—"}</td>
                      <td className="px-2 py-2"><span className="max-w-[220px] break-all text-red-600" title={row.last_error || ""}>{row.last_error || "—"}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Modal>

      {/* 保存视图 */}
      <Modal open={savingView} onClose={() => setSavingView(false)} title="保存当前视图" width={400}
        footer={<><Button variant="secondary" onClick={() => setSavingView(false)}>取消</Button><Button onClick={saveView}>保存</Button></>}>
        <Input label="视图名称" value={viewName} onChange={(e) => setViewName(e.target.value)} placeholder="例如：待处理告警" />
        <div className="mt-3 rounded-md bg-slate-50 p-3 text-xs text-slate-500">
          将保存当前筛选（健康等级、凭据状态、计划类型）与列显示配置。
        </div>
      </Modal>

      {/* 批量确认 */}
      <Confirm open={!!batchAction && batchAction !== "tag"} onClose={() => setBatchAction(null)}
        onConfirm={() => doBatch(batchAction)} danger={batchAction === "pause"}
        title={batchAction === "pause" ? "暂停所选账号" : "恢复所选账号"}
        message={batchAction === "pause"
          ? `确定暂停已选的 ${selected.length} 个账号？暂停后这些账号将停止任务调度。`
          : `确定恢复已选的 ${selected.length} 个账号？`}
        confirmText={batchAction === "pause" ? "确认暂停" : "确认恢复"} />

      {/* 删除确认 */}
      <Confirm open={!!confirmDelete} onClose={() => setConfirmDelete(null)} danger
        title={confirmDelete?.kind === "batch" ? `删除所选 ${confirmDelete.ids.length} 个账号` : "删除账号"}
        message={confirmDelete?.kind === "batch"
          ? `确定永久删除已选的 ${confirmDelete.ids.length} 个账号？将连带清除其验货记录，且不可恢复。`
          : `确定永久删除「${confirmDelete?.account?.email || confirmDelete?.account?.phone || `#${confirmDelete?.account?.id}`}」？将连带清除其验货记录，且不可恢复。`}
        confirmText="确认删除" loading={deleting}
        onConfirm={() => {
          if (confirmDelete?.kind === "batch") doBatchDelete(confirmDelete.ids);
          else doDelete(confirmDelete?.account?.id);
          setConfirmDelete(null);
        }} />

      <Modal open={batchAction === "tag"} onClose={() => setBatchAction(null)} title="为所选账号打标签" width={420}
        footer={<>
          <Button variant="secondary" onClick={() => setBatchAction(null)}>取消</Button>
          <Button onClick={doBatchTag} disabled={batchTagBusy}>{batchTagBusy ? "保存中…" : "应用"}</Button>
        </>}>
        <Input label="标签" value={tagInput} onChange={(e) => setTagInput(e.target.value)} placeholder="例如：post-fix-20260823 / 巴西 / 主池；留空=清除标签" />
        <div className="mt-2 flex flex-wrap gap-1.5">
          {[tagOptions[0] || "pre-fix-20260823", "post-fix-20260823", "巴西", "主池", "备用"].map((t) => (
            <button key={t} onClick={() => setTagInput(t)} className="rounded border border-slate-200 px-2 py-0.5 text-xs text-slate-600 hover:border-blue-300 hover:text-blue-600">{t}</button>
          ))}
        </div>
      </Modal>

      {/* 编辑元数据 */}
      <Modal open={!!editAccount} onClose={() => setEditAccount(null)} title={`编辑元数据 · acc_${editAccount?.id}`} width={440}
        footer={<><Button variant="secondary" onClick={() => setEditAccount(null)}>取消</Button><Button onClick={saveNote}>保存</Button></>}>
        {editAccount && (
          <div className="space-y-3">
            <div className="text-xs text-slate-400">备注会随账号详情保留。</div>
            <Input label="备注" value={editAccount.note || ""} onChange={(e) => setEditAccount({ ...editAccount, note: e.target.value })} placeholder="可选备注，例如：巴西主池 / 验证过" />
          </div>
        )}
      </Modal>

      {/* 账号详情抽屉 */}
      <Drawer open={!!detail} onClose={() => setDetail(null)} width={680}
        title={detail ? `账号详情 · acc_${detail.id}` : ""}
        footer={<>
          <Button variant="secondary" icon={<ShieldCheck size={13} />} onClick={() => doSingleVerify(detail.id)}>浏览器验货</Button>
          <Button variant="dangerSoft" icon={<Trash2 size={13} />} onClick={() => { setConfirmDelete({ kind: "single", account: detail }); setDetail(null); }}>删除账号</Button>
        </>}>
        {detail && (
          <div>
            <div className="border-b border-slate-200 px-4 py-3">
              <div className="flex items-center justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="mono truncate text-[15px] font-semibold text-slate-800">{detail.email || detail.phone || `acc_${detail.id}`}</span>
                    <Badge color={(ACC_STATUS[detail.status] || ["neutral", detail.status])[0]} dot>{(ACC_STATUS[detail.status] || ["neutral", detail.status])[1]}</Badge>
                  </div>
                  <div className="mono mt-0.5 text-xs text-slate-400">acc_{detail.id} · {detail.plan_type || "free"} · 创建 {detail.created_at ? fmtAgo(new Date(detail.created_at).getTime()) : "—"}</div>
                </div>
                <Button variant="ghost" size="sm" icon={<FileText size={13} />} onClick={() => { setEditAccount(detailData || detail); }}>编辑备注</Button>
              </div>
            </div>
            <Tabs tabs={[
              { key: "info", label: "概览" },
              { key: "cred", label: "凭据" },
              { key: "totp", label: "2FA" },
            ]} active={detailTab} onChange={setDetailTab} />
            <div className="p-4">
              {detailTab === "info" && (
                <div className="space-y-0">
                  {[
                    ["账号 ID", `acc_${detail.id}`],
                    ["邮箱", detailData?.email || detail.email || "—"],
                    ["手机号", detail.phone || "—"],
                    ["邮箱来源", detailData?.mail_provider || detail.mail_provider || "unknown"],
                    ["Codex OAuth", (detailData?.oauth_eligible ?? detail.oauth_eligible) ? "允许" : `禁止${detailData?.oauth_block_reason ? `：${detailData.oauth_block_reason}` : ""}`],
                    ["计划类型", detailData?.plan_type || detail.plan_type || "—"],
                    ["状态", (ACC_STATUS[detail.status] || ["neutral", detail.status])[1]],
                    ["代理", detail.proxy || "—"],
                    ["浏览器 profile", detail.profile_path || "—"],
                    ["验货结果", detailData?.verified_result ? (detailData.verified_result === "pass" ? "存活" : detailData.verified_result === "fail" ? "已失效" : detailData.verified_result) : "未验货"],
                    ["最近验货", detailData?.verified_at ? fmtAgo(new Date(detailData.verified_at).getTime()) : "—"],
                    ["创建时间", detail.created_at ? fmtTime(new Date(detail.created_at).getTime()) : "—"],
                    ["标签", detailData?.tag || detail.tag || "—"],
                    ["备注", detailData?.note || detail.note || "—"],
                  ].map(([k, v], i) => (
                    <div key={k} className={`flex justify-between gap-4 py-2 text-[13px] ${i < 13 ? "border-b border-slate-50" : ""}`}>
                      <span className="shrink-0 text-slate-400">{k}</span>
                      <span className="mono break-all text-right text-slate-700">{v}</span>
                    </div>
                  ))}
                </div>
              )}

              {detailTab === "cred" && (
                <div className="space-y-3">
                  {detailBusy && !detailData ? (
                    <div className="py-8 text-center text-xs text-slate-400"><Loader2 size={16} className="mx-auto mb-2 animate-spin" />加载完整凭据…</div>
                  ) : (
                    [
                      { k: "password", label: "密码", kind: "password", v: detailData?.password },
                      { k: "access_token", label: "Access Token", kind: "token", v: detailData?.access_token },
                      { k: "refresh_token", label: "Refresh Token", kind: "token", v: detailData?.refresh_token },
                      { k: "id_token", label: "ID Token", kind: "token", v: detailData?.id_token },
                      { k: "totp_secret", label: "TOTP Secret", kind: "totp", v: detailData?.totp_secret },
                    ].map((f) => (
                      <div key={f.k} className="rounded-md border border-slate-100 px-3 py-2.5">
                        <div className="mb-1 flex items-center justify-between text-[11px] text-slate-400">
                          <span>{f.label}</span>
                          <span className="flex items-center gap-1"><ShieldAlert size={11} />默认脱敏</span>
                        </div>
                        <SecretField value={f.v || ""} mask="••••••••" label={f.label} kind={f.kind} />
                      </div>
                    ))
                  )}
                  <div className="rounded-md bg-slate-50 px-3 py-2 text-[11px] leading-relaxed text-slate-500">
                    完整凭据仅在账号详情中受控展示：点击眼睛显示明文，30 秒后自动隐藏。若 Refresh Token 为空，请到左侧 Codex OAuth 页面统一补授权。
                  </div>

                </div>
              )}

              {detailTab === "totp" && (
                <div className="space-y-3">
                  <div className="flex items-center justify-between rounded-md border border-slate-100 px-3 py-2.5">
                    <div>
                      <div className="text-[13px] font-medium text-slate-700">TOTP 绑定状态</div>
                      <div className="mt-0.5 text-[11px] text-slate-400">{detailData?.totp_secret ? `已绑定（${detailData.totp_secret_masked}）` : "未绑定 2FA"}</div>
                    </div>
                    {detailData?.totp_secret ? <Badge color="success" dot>已绑定</Badge> : <Badge color="neutral" dot>未绑定</Badge>}
                  </div>
                  {detailData?.totp_secret && (
                    <div className="rounded-md border border-slate-100 px-3 py-2.5">
                      <div className="mb-1 text-[11px] text-slate-400">当前 Secret（默认脱敏）</div>
                      <SecretField value={detailData.totp_secret} mask={detailData.totp_secret_masked || "••••••••"} label="TOTP Secret" kind="totp" />
                    </div>
                  )}
                  <div className="rounded-md border border-slate-200 p-3">
                    <div className="mb-1.5 text-xs font-medium text-slate-600">手工写入 / 更新 TOTP Secret</div>
                    <div className="flex gap-2">
                      <Input value={totpInput} onChange={(e) => setTotpInput(e.target.value)} placeholder="base32，如 JBSWY3DPEHPK3PXP（留空并保存 = 清空绑定）" className="mono" />
                      <Button icon={savingTotp ? <Loader2 size={13} className="animate-spin" /> : <KeyRound size={13} />} onClick={saveTotp} disabled={savingTotp} className="shrink-0">
                        {savingTotp ? "保存中…" : "保存"}
                      </Button>
                    </div>
                    <div className="mt-1.5 text-[11px] text-slate-400">Secret 仅接受 A-Z、2-7 的 base32 字符。</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </Drawer>

    </div>
  );
}
