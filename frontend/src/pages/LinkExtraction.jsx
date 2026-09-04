import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Clipboard, ExternalLink, Link2, Play, RefreshCw, Search, Square, Terminal } from "lucide-react";
import {
  Badge,
  Button,
  Checkbox,
  Empty,
  ErrorState,
  Input,
  Loading,
  PageHeader,
  Panel,
  Pagination,
  Select,
  Switch,
} from "../components/ui";
import { api } from "../api";
import { useApp } from "../context/AppContext";
import {
  DEFAULT_LINK_EXTRACTION_FORM,
  INDONESIA_ZERO_PRESET,
  LINK_COUNTRIES,
  LINK_PAYMENT_METHODS,
  buildLinkExtractionPayload,
  formatAmount,
  itemStatusMeta,
  linkJobActive,
  linkStatusMeta,
} from "./linkExtractionUtils";

function Metrics({ job }) {
  const values = [
    ["总账号", job?.total || 0, "text-slate-700"],
    ["运行中", job?.running || 0, "text-blue-600"],
    ["成功", job?.succeeded || 0, "text-emerald-600"],
    ["失败", job?.failed || 0, "text-red-600"],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {values.map(([label, value, color]) => (
        <div key={label} className="rounded-md border border-slate-200 bg-white px-3 py-2">
          <div className="text-[11px] text-slate-400">{label}</div>
          <div className={`tnum mt-1 text-xl font-semibold ${color}`}>{value}</div>
        </div>
      ))}
    </div>
  );
}

function LogBox({ logs, active }) {
  const bottomRef = useRef(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [logs.length]);
  return (
    <div className="overflow-hidden rounded-md border border-slate-700 bg-[#0d1117]">
      <div className="flex items-center gap-2 border-b border-slate-800 px-3 py-2">
        <Terminal size={13} className="text-slate-500" />
        <span className="text-xs font-medium text-slate-300">提链实时日志</span>
        {active && <span className="flex items-center gap-1 text-[11px] text-emerald-400"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />LIVE</span>}
      </div>
      <div className="max-h-[300px] min-h-[160px] overflow-y-auto px-3 py-2">
        {logs.length === 0 ? <div className="flex min-h-[140px] items-center justify-center text-[11px] text-slate-600">暂无日志</div> : logs.map((line) => (
          <div key={line.seq} className="flex gap-2 py-px font-mono text-[11px] leading-[1.65]">
            <span className="shrink-0 text-slate-600">{line.ts || "--:--:--"}</span>
            <span className="break-all whitespace-pre-wrap text-slate-300">{line.msg || ""}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function AccountPicker({ data, selected, setSelected, loading, error, onRetry, search, setSearch, page, setPage, pageSize, setPageSize }) {
  const pageIds = data.items.map((item) => item.id);
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const togglePage = (checked) => {
    setSelected((current) => {
      const next = new Set(current);
      pageIds.forEach((id) => (checked ? next.add(id) : next.delete(id)));
      return next;
    });
  };
  return (
    <Panel title="账号队列" extra={<span className="text-xs text-slate-400">已选 {selected.size} 个</span>} pad={false}>
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3">
        <div className="relative min-w-[220px] flex-1">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="搜索邮箱 / 手机号 / ID" className="input pl-8" />
        </div>
        <Button variant="secondary" size="sm" icon={<RefreshCw size={13} />} onClick={onRetry}>刷新</Button>
      </div>
      {loading ? <Loading rows={5} cols={3} /> : error ? <ErrorState message={error} onRetry={onRetry} /> : data.items.length === 0 ? <Empty title="暂无账号" /> : (
        <>
          <div className="flex items-center gap-2 border-b border-slate-100 bg-slate-50/70 px-3 py-2 text-xs text-slate-500">
            <Checkbox checked={allPageSelected} indeterminate={selected.size > 0 && !allPageSelected} onChange={togglePage} />
            <span>选择当前页</span>
            <span className="ml-auto">共 {data.total} 个带 access token 的账号</span>
          </div>
          <div className="max-h-[360px] overflow-y-auto">
            {data.items.map((account) => (
              <label key={account.id} className="flex cursor-pointer items-center gap-2 border-b border-slate-100 px-3 py-2.5 hover:bg-slate-50">
                <Checkbox checked={selected.has(account.id)} onChange={(checked) => setSelected((current) => { const next = new Set(current); checked ? next.add(account.id) : next.delete(account.id); return next; })} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] text-slate-700">{account.email || `账号 #${account.id}`}</span>
                  <span className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-400"><span>#{account.id}</span><span>{account.plan_type || "-"}</span><span>{account.has_profile ? "有 profile" : "无 profile"}</span></span>
                </span>
                <Badge color={account.status === "active" ? "success" : "neutral"}>{account.status || "unknown"}</Badge>
              </label>
            ))}
          </div>
          <Pagination page={page} pages={data.pages} total={data.total} pageSize={pageSize} onPage={setPage} onPageSize={(size) => { setPageSize(size); setPage(1); }} />
        </>
      )}
    </Panel>
  );
}

function ResultLink({ url, onCopy }) {
  if (!url) return <span className="text-slate-300">-</span>;
  return (
    <span className="inline-flex max-w-[220px] items-center gap-1">
      <a href={url} target="_blank" rel="noreferrer" title={url} className="truncate text-blue-600 hover:underline"><ExternalLink size={12} className="mr-1 inline" />打开</a>
      <button title="复制链接" onClick={() => onCopy(url)} className="text-slate-400 hover:text-blue-600"><Clipboard size={13} /></button>
    </span>
  );
}

export default function LinkExtraction() {
  const { toast } = useApp();
  const [accounts, setAccounts] = useState({ items: [], total: 0, pages: 1 });
  const [accountLoading, setAccountLoading] = useState(true);
  const [accountError, setAccountError] = useState("");
  const [accountSearch, setAccountSearch] = useState("");
  const [accountPage, setAccountPage] = useState(1);
  const [accountPageSize, setAccountPageSize] = useState(50);
  const [selected, setSelected] = useState(new Set());
  const [form, setForm] = useState({ ...DEFAULT_LINK_EXTRACTION_FORM });
  const [job, setJob] = useState(null);
  const [items, setItems] = useState([]);
  const [logs, setLogs] = useState([]);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const logCursor = useRef(0);
  const polling = useRef(false);

  const loadAccounts = useCallback(async () => {
    setAccountLoading(true);
    setAccountError("");
    try {
      const result = await api.linkExtraction.accounts({ q: accountSearch, page: accountPage, pageSize: accountPageSize, hasToken: true });
      setAccounts(result);
    } catch (error) {
      setAccountError(error?.message || "账号加载失败");
    } finally {
      setAccountLoading(false);
    }
  }, [accountPage, accountPageSize, accountSearch]);

  const loadHistory = useCallback(async () => {
    try { setHistory(await api.linkExtraction.jobs()); } catch { setHistory([]); }
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const loadJob = useCallback(async (jobId, reset = false) => {
    const [nextJob, nextItems, nextLogs] = await Promise.all([
      api.linkExtraction.job(jobId),
      api.linkExtraction.items(jobId),
      api.linkExtraction.logs(jobId, reset ? 0 : logCursor.current),
    ]);
    setJob(nextJob);
    setItems(Array.isArray(nextItems) ? nextItems : []);
    if (reset) setLogs([]);
    if (nextLogs?.logs?.length) setLogs((current) => reset ? nextLogs.logs : [...current, ...nextLogs.logs]);
    if (nextLogs?.next != null) logCursor.current = Number(nextLogs.next) || logCursor.current;
    return nextJob;
  }, []);

  const pollJob = useCallback(async () => {
    if (!job?.id || polling.current) return;
    polling.current = true;
    try {
      const next = await loadJob(job.id);
      if (!linkJobActive(next.status)) { setBusy(false); loadHistory(); }
    } catch {
      // 保留上一份状态，下一轮继续拉取。
    } finally { polling.current = false; }
  }, [job?.id, loadHistory, loadJob]);

  useEffect(() => {
    if (!job?.id || !linkJobActive(job.status)) return undefined;
    const timer = setInterval(pollJob, 1000);
    return () => clearInterval(timer);
  }, [job?.id, job?.status, pollJob]);

  const updateForm = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const applyZeroPreset = () => {
    setForm((current) => ({ ...current, ...INDONESIA_ZERO_PRESET }));
    toast("已套用印尼 0 元模板：ID 出口建单 + TH 出口优惠 + 0 元校验", "info");
  };
  const start = async () => {
    if (!selected.size) { toast("请至少选择一个账号", "warning"); return; }
    if (job && linkJobActive(job.status)) return;
    setBusy(true);
    try {
      const created = await api.linkExtraction.createJob(buildLinkExtractionPayload([...selected], form));
      setJob(created); setItems([]); setLogs([]); logCursor.current = 0;
      await loadJob(created.id, true);
      toast(`提链任务 #${created.id} 已启动`, "info");
    } catch (error) { setBusy(false); toast(error?.message || "创建提链任务失败", "error"); }
  };
  const stop = async () => {
    if (!job?.id || !linkJobActive(job.status)) return;
    try { const canceled = await api.linkExtraction.cancel(job.id); setJob(canceled); setBusy(false); await loadJob(job.id); toast("提链任务已停止", "warning"); }
    catch (error) { toast(error?.message || "停止任务失败", "error"); }
  };
  const openHistory = async (id) => {
    setLogs([]); logCursor.current = 0; setBusy(false);
    try { await loadJob(id, true); } catch (error) { toast(error?.message || "任务加载失败", "error"); }
  };
  const copy = async (value) => {
    try { await navigator.clipboard.writeText(value); toast("链接已复制", "success"); }
    catch { toast("复制失败，请手动复制", "warning"); }
  };
  const active = Boolean(job && linkJobActive(job.status));
  const status = linkStatusMeta(job?.status);
  const selectedCount = selected.size;
  const configSummary = useMemo(
    () => `${form.country} · ${form.payment_method} · 并发 ${form.concurrency}${form.require_zero_amount ? " · 0元校验" : ""}`,
    [form],
  );

  return (
    <div className="space-y-3">
      <PageHeader title="提链工作台" subtitle="从账号 access token 提取 ChatGPT Checkout 支付跳转链接" badge={<Badge color={status.color} dot>{status.label}</Badge>} extra={<><Button variant="secondary" size="sm" icon={<RefreshCw size={13} />} onClick={() => { loadAccounts(); loadHistory(); }}>刷新</Button>{active ? <Button variant="danger" size="sm" icon={<Square size={13} />} onClick={stop}>停止</Button> : <Button size="sm" icon={<Play size={13} />} disabled={busy || !selectedCount} loading={busy} onClick={start}>开始提链</Button>}</>} />

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.75fr)]">
        <AccountPicker data={accounts} selected={selected} setSelected={setSelected} loading={accountLoading} error={accountError} onRetry={loadAccounts} search={accountSearch} setSearch={setAccountSearch} page={accountPage} setPage={setAccountPage} pageSize={accountPageSize} setPageSize={setAccountPageSize} />
        <Panel
          title="提链参数"
          extra={
            <span className="flex items-center gap-2">
              <span className="text-[11px] text-slate-400">{configSummary}</span>
              <button onClick={applyZeroPreset} className="rounded border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] text-blue-600 hover:bg-blue-100">
                印尼 0 元模板
              </button>
            </span>
          }
        >
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Input label="Checkout Proxy" value={form.checkout_proxy} onChange={(event) => updateForm("checkout_proxy", event.target.value)} placeholder="留空使用账号代理" />
              <Input label="Update Proxy" value={form.update_proxy} onChange={(event) => updateForm("update_proxy", event.target.value)} placeholder="留空跟随 Checkout Proxy" />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Select label="国家" value={form.country} onChange={(value) => updateForm("country", value)} options={LINK_COUNTRIES} />
              <Select label="支付方式" value={form.payment_method} onChange={(value) => updateForm("payment_method", value)} options={LINK_PAYMENT_METHODS} />
              <Input label="并发数" type="number" min="1" max="5" value={form.concurrency} onChange={(event) => updateForm("concurrency", event.target.value)} />
            </div>
            <div className="grid gap-3 border-t border-slate-100 pt-3 sm:grid-cols-3">
              <Input label="Checkout 出口地区" value={form.checkout_region} onChange={(event) => updateForm("checkout_region", event.target.value)} placeholder="如 ID（改写 cliproxy region）" />
              <Input label="Update 出口地区" value={form.update_region} onChange={(event) => updateForm("update_region", event.target.value)} placeholder="如 TH（留空跟随 checkout）" />
              <Input label="单账号重试次数" type="number" min="1" max="20" value={form.max_attempts} onChange={(event) => updateForm("max_attempts", event.target.value)} />
              <Input label="优惠活动 ID" value={form.promo_campaign_id} onChange={(event) => updateForm("promo_campaign_id", event.target.value)} placeholder="留空自动读取账号优惠目录" />
            </div>
            <div className="space-y-2 border-t border-slate-100 pt-3">
              <Switch checked={form.rotate_proxy} onChange={(value) => updateForm("rotate_proxy", value)} label="失败后自动轮换出口（cliproxy sid）" />
              <Switch checked={form.browser_fallback} onChange={(value) => updateForm("browser_fallback", value)} label="Stripe 段被拦截时切换浏览器链路" />
              <Switch checked={form.require_zero_amount} onChange={(value) => updateForm("require_zero_amount", value)} label="0 元校验（金额非 0 判为失败）" />
              <Switch checked={form.apply_checkout_update} onChange={(value) => updateForm("apply_checkout_update", value)} label="执行 Checkout 更新（优惠资格检查 + 应用）" />
              <Switch checked={form.oaics_only} onChange={(value) => updateForm("oaics_only", value)} label="仅处理 OAICS Checkout" />
            </div>
            <div className="rounded-md bg-blue-50 px-3 py-2 text-[11px] leading-relaxed text-blue-700">已选择 {selectedCount} 个账号。提链过程只读取后端账号凭据，页面不显示 access token。</div>
          </div>
        </Panel>
      </div>

      <Metrics job={job} />
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.75fr)]">
        <Panel title="提取结果" extra={<span className="text-xs text-slate-400">{job ? `任务 #${job.id}` : "尚未运行"}</span>} pad={false}>
          {items.length === 0 ? <Empty title="暂无账号结果" desc="选择账号后点击开始提链" /> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-[12px]">
                <thead><tr className="border-b border-slate-200 bg-slate-50/70 text-left"><th className="th">账号</th><th className="th">状态</th><th className="th">阶段</th><th className="th">Checkout</th><th className="th">金额</th><th className="th">PayPal</th><th className="th">GoPay</th><th className="th">GCash</th></tr></thead>
                <tbody>{items.map((item) => { const meta = itemStatusMeta(item.status); return <tr key={item.id} className="tr-row"><td className="td max-w-[200px] truncate">{item.email || `#${item.account_id}`}</td><td className="td"><Badge color={meta.color} dot>{meta.label}</Badge></td><td className="td"><span className="text-slate-500">{item.stage} {item.progress ? `${item.progress}%` : ""}</span></td><td className="td"><span className="text-slate-500">{item.session_kind || "-"}</span></td><td className="td tnum">{formatAmount(item)}</td><td className="td"><ResultLink url={item.paypal_url || (item.payment_method === "paypal" ? item.provider_url : "")} onCopy={copy} /></td><td className="td"><ResultLink url={item.gopay_url || (item.payment_method === "gopay" ? item.provider_url : "")} onCopy={copy} /></td><td className="td"><ResultLink url={item.gcash_url || (item.payment_method === "gcash" ? item.provider_url : "")} onCopy={copy} /></td></tr>; })}</tbody>
              </table>
            </div>
          )}
        </Panel>
        <LogBox logs={logs} active={active} />
      </div>

      <Panel title="历史任务" extra={<span className="text-xs text-slate-400">点击任务查看结果和日志</span>} pad={false}>
        {history.length === 0 ? <Empty title="暂无数据" /> : <div className="divide-y divide-slate-100">{history.slice(0, 10).map((row) => { const meta = linkStatusMeta(row.status); return <button key={row.id} onClick={() => openHistory(row.id)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-50"><Link2 size={14} className="text-slate-400" /><span className="tnum w-14 text-xs text-slate-500">#{row.id}</span><span className="flex-1 text-xs text-slate-700">{row.country} · {row.payment_method} · 并发 {row.concurrency}</span><Badge color={meta.color}>{meta.label}</Badge><span className="tnum text-[11px] text-slate-400">成功 {row.succeeded} / 失败 {row.failed}</span></button>; })}</div>}
      </Panel>
    </div>
  );
}
