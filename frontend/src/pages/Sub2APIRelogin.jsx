import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, RefreshCw, RotateCcw, Search, Square, Terminal } from "lucide-react";
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
  Switch,
} from "../components/ui";
import { api } from "../api";
import { useApp } from "../context/AppContext";
import { normalizeSub2APIGroups } from "./sub2apiUtils";
import {
  DEFAULT_RELOGIN_PARAMS,
  buildReloginJobPayload,
  buildReloginPreviewParams,
  normalizeReloginGroupIds,
  paginateReloginRows,
  projectReloginItem,
  reloginJobActive,
  reloginStatusMeta,
} from "./sub2apiReloginUtils";

function safeLog(text) {
  return String(text || "")
    .replace(/(access_token|refresh_token|id_token|password|totp_secret|totp)\s*[:=]\s*[^,\s}]+/gi, "$1=[已隐藏]")
    .replace(/(code|state|access_token|refresh_token|id_token)=([^&\s]+)/gi, "$1=[已隐藏]")
    .replace(/eyJ[A-Za-z0-9_.-]{20,}/g, "<jwt>")
    .slice(0, 600);
}

function statusBadge(status) {
  const meta = reloginStatusMeta(status);
  return <Badge color={meta.color} dot>{meta.label}</Badge>;
}

function LogBox({ logs, active }) {
  return (
    <div className="overflow-hidden rounded-lg border border-slate-700 bg-[#0d1117]">
      <div className="flex items-center gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-2">
        <Terminal size={13} className="text-slate-500" />
        <span className="text-xs font-medium text-slate-300">Sub2API 重登日志</span>
        {active && <span className="flex items-center gap-1 text-[11px] text-emerald-500"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />LIVE</span>}
        {!active && logs.length > 0 && <span className="text-[11px] text-slate-500">已结束 · {logs.length} 行</span>}
      </div>
      <div className="max-h-[360px] min-h-[180px] overflow-y-auto px-3 py-2">
        {logs.length === 0 ? (
          <div className="flex min-h-[164px] items-center justify-center text-[11px] text-slate-600">暂无重登日志</div>
        ) : logs.map((line) => (
          <div key={line.seq || line.id} className="flex items-start gap-2 py-px font-mono text-[11.5px] leading-[1.7]">
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-slate-500" />
            <span className="shrink-0 text-slate-600">{line.ts || "--:--:--"}</span>
            <span className="min-w-0 whitespace-pre-wrap break-all text-slate-300">{safeLog(line.msg || line.message)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function GroupPicker({ groups, selected, onToggle, disabled }) {
  if (!groups.length) return <div className="text-xs text-slate-400">暂无可用的 OpenAI 分组，请先检查 Sub2API 设置。</div>;
  return (
    <div className="grid max-h-48 gap-1 overflow-y-auto rounded-md border border-slate-200 p-2 sm:grid-cols-2 lg:grid-cols-3">
      {groups.map((group) => (
        <label key={group.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-2 text-xs text-slate-700 hover:bg-slate-50">
          <Checkbox checked={selected.includes(group.id)} onChange={() => { if (!disabled) onToggle(group.id); }} />
          <span className="min-w-0 truncate">{group.name}</span>
          <span className="ml-auto shrink-0 text-slate-400">#{group.id}</span>
        </label>
      ))}
    </div>
  );
}


function Pager({ data, onPage, onPageSize }) {
  if (!data || data.total <= 0) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50/60 px-3 py-2 text-xs text-slate-500">
      <div>
        共 <span className="tnum font-medium text-slate-700">{data.total}</span> 条 · 显示 <span className="tnum text-slate-700">{data.from}-{data.to}</span> · 第 <span className="tnum text-slate-700">{data.page}/{data.pages}</span> 页
      </div>
      <div className="flex items-center gap-2">
        <select
          value={data.pageSize}
          onChange={(event) => onPageSize(Number(event.target.value))}
          className="input w-24 px-2 py-1 text-xs"
          aria-label="每页条数"
        >
          {[20, 50, 100, 200].map((size) => <option key={size} value={size}>{size} 条/页</option>)}
        </select>
        <Button variant="secondary" size="sm" onClick={() => onPage(data.page - 1)} disabled={data.page <= 1}>上一页</Button>
        <Button variant="secondary" size="sm" onClick={() => onPage(data.page + 1)} disabled={data.page >= data.pages}>下一页</Button>
      </div>
    </div>
  );
}

export default function Sub2APIRelogin() {
  const { toast, t } = useApp();
  const [groups, setGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [groupsError, setGroupsError] = useState("");
  const [form, setForm] = useState({ ...DEFAULT_RELOGIN_PARAMS });
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [job, setJob] = useState(null);
  const [items, setItems] = useState([]);
  const [logs, setLogs] = useState([]);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [previewPage, setPreviewPage] = useState(1);
  const [previewPageSize, setPreviewPageSize] = useState(20);
  const [resultPage, setResultPage] = useState(1);
  const [resultPageSize, setResultPageSize] = useState(20);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize, setHistoryPageSize] = useState(20);
  const logCursorRef = useRef(0);
  const pollingRef = useRef(false);

  const loadGroups = useCallback(async () => {
    setGroupsLoading(true);
    setGroupsError("");
    try {
      const [groupPayload, settingsPayload] = await Promise.all([
        api.sub2api.groups(),
        api.settings.get().catch(() => null),
      ]);
      const nextGroups = normalizeSub2APIGroups(groupPayload);
      setGroups(nextGroups);
      const configured = normalizeReloginGroupIds(settingsPayload?.sub2api_group_ids);
      setSelectedGroups(configured.length ? configured : nextGroups.slice(0, 1).map((group) => group.id));
    } catch (error) {
      setGroupsError(error?.message || "Sub2API 分组加载失败");
    } finally {
      setGroupsLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const rows = await api.sub2apiRelogin.jobs();
      setHistory(Array.isArray(rows) ? rows : []);
    } catch {
      setHistory([]);
    }
  }, []);

  useEffect(() => {
    loadGroups();
    loadHistory();
  }, [loadGroups, loadHistory]);

  const loadJob = useCallback(async (jobId, resetLogs = false) => {
    const [nextJob, nextItems, nextLogs] = await Promise.all([
      api.sub2apiRelogin.job(jobId),
      api.sub2apiRelogin.items(jobId),
      api.sub2apiRelogin.logs(jobId, resetLogs ? 0 : logCursorRef.current),
    ]);
    setJob(nextJob);
    setItems(Array.isArray(nextItems) ? nextItems.map(projectReloginItem) : []);
    if (resetLogs) setLogs([]);
    if (Array.isArray(nextLogs?.logs) && nextLogs.logs.length) {
      setLogs((current) => resetLogs ? nextLogs.logs : [...current, ...nextLogs.logs]);
    }
    if (nextLogs?.next != null) logCursorRef.current = Number(nextLogs.next) || logCursorRef.current;
    return nextJob;
  }, []);

  const pollJob = useCallback(async (jobId) => {
    if (pollingRef.current) return;
    pollingRef.current = true;
    try {
      const nextJob = await loadJob(jobId);
      if (!reloginJobActive(nextJob?.status)) {
        setBusy(false);
        loadHistory();
      }
    } catch {
      // 网络抖动时保留上一份任务状态，下一轮继续同步。
    } finally {
      pollingRef.current = false;
    }
  }, [loadHistory, loadJob]);

  useEffect(() => {
    if (!job?.id || !reloginJobActive(job.status)) return undefined;
    const timer = setInterval(() => pollJob(job.id), 1000);
    return () => clearInterval(timer);
  }, [job?.id, job?.status, pollJob]);

  const scan = async () => {
    if (!selectedGroups.length) {
      toast("请至少选择一个 Sub2API 分组", "warning");
      return;
    }
    setPreviewLoading(true);
    setPreviewError("");
    try {
      const result = await api.sub2apiRelogin.preview(buildReloginPreviewParams(selectedGroups, true));
      setPreview(result);
      setItems(Array.isArray(result.items) ? result.items.map(projectReloginItem) : []);
      setPreviewPage(1);
      setResultPage(1);
      toast(`扫描完成：发现 ${result.error_total || 0} 个异常账号`, "success");
    } catch (error) {
      const message = error?.message || "扫描失败";
      setPreviewError(message);
      toast(`扫描失败: ${message}`, "error");
    } finally {
      setPreviewLoading(false);
    }
  };

  const start = async () => {
    if (!selectedGroups.length) {
      toast("请先选择分组", "warning");
      return;
    }
    if (job && reloginJobActive(job.status)) return;
    const selectedKey = normalizeReloginGroupIds(selectedGroups).sort((a, b) => a - b).join(",");
    const previewKey = normalizeReloginGroupIds(preview?.group_ids || []).sort((a, b) => a - b).join(",");
    if (!preview?.items?.length || selectedKey !== previewKey) {
      toast("请先扫描当前分组的 error 账号，再开始重登", "warning");
      return;
    }
    setBusy(true);
    try {
      const payload = buildReloginJobPayload({ ...form, group_ids: selectedGroups, only_error: true, preview_items: preview.items });
      const created = await api.sub2apiRelogin.createJob(payload);
      setJob(created);
      setItems([]);
      setLogs([]);
      setPreviewPage(1);
      setResultPage(1);
      logCursorRef.current = 0;
      await loadJob(created.id, true);
      toast(`重登任务 #${created.id} 已启动`, "info");
    } catch (error) {
      setBusy(false);
      toast(error?.message || "创建重登任务失败", "error");
    }
  };

  const stop = async () => {
    if (!job?.id || !reloginJobActive(job.status)) return;
    try {
      const canceled = await api.sub2apiRelogin.cancel(job.id);
      setJob(canceled);
      setBusy(false);
      await loadJob(job.id);
      toast("重登任务已停止", "warning");
    } catch (error) {
      toast(error?.message || "停止任务失败", "error");
    }
  };

  const openHistory = async (row) => {
    try {
      setJob(row);
      setLogs([]);
      setPreviewPage(1);
      setResultPage(1);
      logCursorRef.current = 0;
      await loadJob(row.id, true);
    } catch (error) {
      toast(error?.message || "任务加载失败", "error");
    }
  };

  const setField = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const runnableItems = items.filter((item) => item.status === "pending" || item.status === "running").length;
  const previewItems = useMemo(() => (job ? items : (preview?.items || items)).map(projectReloginItem), [items, job, preview]);
  const resultItems = useMemo(() => (job ? items : (preview?.items || [])).map(projectReloginItem), [items, job, preview]);
  const previewPager = useMemo(() => paginateReloginRows(previewItems, previewPage, previewPageSize), [previewItems, previewPage, previewPageSize]);
  const resultPager = useMemo(() => paginateReloginRows(resultItems, resultPage, resultPageSize), [resultItems, resultPage, resultPageSize]);
  const historyPager = useMemo(() => paginateReloginRows(history, historyPage, historyPageSize), [history, historyPage, historyPageSize]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Sub2API 账号重登"
        subtitle="扫描指定分组中的异常账号，使用本地 profile 重新授权并恢复远端调度"
        badge={<Badge color="info" dot>独立任务</Badge>}
        extra={<Button variant="secondary" size="sm" icon={<RefreshCw size={13} />} onClick={loadGroups} disabled={groupsLoading}>刷新分组</Button>}
      />

      <Panel title="分组与参数">
        <div className="space-y-4">
          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-slate-600">Sub2API 分组</span>
              <span className="text-[11px] text-slate-400">{t(`已选择 ${selectedGroups.length} 个`)}</span>
            </div>
            {groupsLoading ? <Loading rows={2} cols={3} label="加载 Sub2API 分组…" /> : <GroupPicker groups={groups} selected={selectedGroups} onToggle={(id) => setSelectedGroups((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} disabled={busy} />}
            {groupsError && <div className="mt-2"><ErrorState message={groupsError} onRetry={loadGroups} /></div>}
          </div>
          <div className="grid gap-3 border-t border-slate-100 pt-4 sm:grid-cols-2 lg:grid-cols-3">
            <Switch checked={form.headless} onChange={(value) => setField("headless", value)} disabled={busy} label="无头模式" />
            <Input label="并发数（1-5）" type="number" min="1" max="5" value={form.concurrency} onChange={(event) => setField("concurrency", event.target.value)} />
            <Input label="单账号超时（秒）" type="number" min="10" value={form.timeout_s} onChange={(event) => setField("timeout_s", event.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <Button icon={<Search size={14} />} onClick={scan} loading={previewLoading} disabled={busy || !selectedGroups.length}>扫描 error 账号</Button>
            <Button variant="success" icon={<Play size={14} />} onClick={start} loading={busy && !job} disabled={busy || !selectedGroups.length}>开始重登</Button>
            <Button variant="dangerSoft" icon={<Square size={13} />} onClick={stop} disabled={!job || !reloginJobActive(job.status)}>停止</Button>
            {preview && <span className="text-xs text-slate-500">远端 {preview.remote_total} · 异常 {preview.error_total} · 可执行 {preview.runnable}</span>}
          </div>
        </div>
      </Panel>

      {previewError && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{previewError}</div>}

      <Panel title="扫描预览" extra={<span className="text-[11px] text-slate-400">只显示远端状态和匹配结果</span>} pad={false}>
        {previewLoading ? <Loading rows={5} cols={7} label="扫描远端账号…" /> : !preview && !items.length ? <Empty title="暂无扫描结果" desc="选择分组后点击扫描 error 账号" /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px] text-left">
                <thead><tr className="border-b border-slate-200 bg-slate-50/70"><th className="th">remote id</th><th className="th">local id</th><th className="th">email</th><th className="th">远端状态</th><th className="th">远端错误</th><th className="th">动作</th><th className="th">原因</th></tr></thead>
                <tbody>{previewPager.rows.map((item, index) => (
                  <tr key={`${item.remote_account_id}-${previewPager.from + index}`} className="tr-row">
                    <td className="td mono text-slate-700">{item.remote_account_id || "—"}</td>
                    <td className="td mono text-slate-600">{item.local_account_id ?? "—"}</td>
                    <td className="td max-w-[200px] truncate text-slate-600">{item.email || "—"}</td>
                    <td className="td">{item.remote_status || "—"}</td>
                    <td className="td max-w-[250px] truncate text-xs text-red-600" title={item.remote_error}>{item.remote_error || "—"}</td>
                    <td className="td">{item.action === "ready" || item.status === "pending" ? <Badge color="info">重登</Badge> : <Badge color="neutral">跳过</Badge>}</td>
                    <td className="td text-xs text-slate-500">{item.reason || "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <Pager data={previewPager} onPage={setPreviewPage} onPageSize={(size) => { setPreviewPageSize(size); setPreviewPage(1); }} />
          </>
        )}
      </Panel>

      <Panel title="运行状态" extra={job ? statusBadge(job.status) : <Badge color="neutral">未启动</Badge>}>
        {!job ? <Empty title="暂无运行任务" desc="扫描后可直接开始重登，任务会在后台继续执行" /> : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {[["total", "总数", "slate"], ["success", "成功", "success"], ["failed", "失败", "danger"], ["skipped", "跳过", "warning"], ["pending", "待处理", "info"]].map(([key, label, color]) => (
                <div key={key} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2"><div className="text-[11px] text-slate-500">{label}</div><div className={`tnum mt-1 text-xl font-semibold ${color === "success" ? "text-emerald-600" : color === "danger" ? "text-red-600" : color === "warning" ? "text-amber-600" : color === "info" ? "text-blue-600" : "text-slate-700"}`}>{job[key] ?? 0}</div></div>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500"><span>任务 ID <span className="mono text-slate-700">#{job.id}</span></span><span>当前队列 <span className="tnum text-slate-700">{runnableItems}</span></span>{job.error && <span className="text-red-600">{job.error}</span>}</div>
          </div>
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="实时日志" pad={false}><div className="p-3"><LogBox logs={logs} active={Boolean(job && reloginJobActive(job.status))} /></div></Panel>
        <Panel title="账号结果" extra={<span className="text-[11px] text-slate-400">不显示完整凭据</span>} pad={false}>
          {resultItems.length === 0 ? <Empty title="暂无账号结果" /> : <>
            <div className="max-h-[390px] overflow-auto"><table className="w-full min-w-[720px] text-left"><thead><tr className="border-b border-slate-200 bg-slate-50/70"><th className="th">状态</th><th className="th">remote id</th><th className="th">local id</th><th className="th">email</th><th className="th">原因/错误</th></tr></thead><tbody>{resultPager.rows.map((item, index) => <tr key={`${item.remote_account_id}-${resultPager.from + index}`} className="tr-row"><td className="td">{statusBadge(item.status)}</td><td className="td mono">{item.remote_account_id || "—"}</td><td className="td mono">{item.local_account_id ?? "—"}</td><td className="td max-w-[190px] truncate">{item.email || "—"}</td><td className="td max-w-[240px] truncate text-xs text-slate-500" title={item.error || item.reason}>{item.error || item.reason || "—"}</td></tr>)}</tbody></table></div>
            <Pager data={resultPager} onPage={setResultPage} onPageSize={(size) => { setResultPageSize(size); setResultPage(1); }} />
          </>}
        </Panel>
      </div>

      <Panel title="历史任务" extra={<Button variant="ghost" size="sm" icon={<RefreshCw size={12} />} onClick={loadHistory}>刷新</Button>} pad={false}>
        {history.length === 0 ? <div className="p-4 text-xs text-slate-400">暂无历史任务</div> : <>
          <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left"><thead><tr className="border-b border-slate-200 bg-slate-50/70"><th className="th">任务</th><th className="th">状态</th><th className="th">分组</th><th className="th">成功/失败/跳过</th><th className="th" /></tr></thead><tbody>{historyPager.rows.map((row) => <tr key={row.id} className="tr-row"><td className="td mono">#{row.id}</td><td className="td">{statusBadge(row.status)}</td><td className="td mono text-xs text-slate-500">{row.group_ids}</td><td className="td tnum text-xs text-slate-600">{row.success} / {row.failed} / {row.skipped}</td><td className="td text-right"><Button variant="ghost" size="sm" icon={<RotateCcw size={12} />} onClick={() => openHistory(row)}>查看</Button></td></tr>)}</tbody></table></div>
          <Pager data={historyPager} onPage={setHistoryPage} onPageSize={(size) => { setHistoryPageSize(size); setHistoryPage(1); }} />
        </>}
      </Panel>
    </div>
  );
}
