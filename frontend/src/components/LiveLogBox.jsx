import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal, Trash2, Copy, Check, ArrowDownToLine, Loader2, ChevronDown, Filter, Eye, EyeOff, ShieldAlert } from "lucide-react";
import { api } from "../api";
import { useApp } from "../context/AppContext";
import { LOG_LEVEL_META, filterLogLines, normalizeLogLevel } from "../pages/registerUtils";

const DOT = { success: "bg-emerald-500", error: "bg-red-500", warning: "bg-amber-500", info: "bg-slate-500" };
const LEVEL_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "success", label: "成功" },
  { value: "warning", label: "警告" },
  { value: "error", label: "错误" },
];
const LIVE_LOG_RENDER_LIMIT = 2000;
const INITIAL_LOG_FETCH_LIMIT = 500;
const POLL_LOG_FETCH_LIMIT = 300;
const MAX_BACKLOG_PAGES_PER_TICK = 5;

/**
 * 实时日志框：深色终端风格，轮询 registration 和 batch 两类日志增量拉取。
 * props: regId, batchId, active(bool 是否运行中), height(px), initialLines(可选预置)
 * 支持：级别标签/过滤、错误行高亮、单行点击复制、跟随最新、复制/下载/清空。
 */
export default function LiveLogBox({ regId, batchId, active, height = 320, title = "实时日志", onLogs }) {
  const { toast, t } = useApp();
  const [logs, setLogs] = useState([]);
  const [next, setNext] = useState(0);
  const [batchNext, setBatchNext] = useState(0);
  const [autoScroll, setAutoScroll] = useState(true);
  const [copied, setCopied] = useState(false);
  const [copiedLine, setCopiedLine] = useState(null);
  const [filter, setFilter] = useState("all");
  const [polling, setPolling] = useState(false);
  const [clearing, setClearing] = useState(false);
  // 日志脱敏开关：默认开启（打码）；关闭后新日志输出明文密码/TOTP/验证码（仅调试用）
  const [redact, setRedact] = useState(true);
  // 开关是否可用（依赖后端 /registrations/log-redact 接口，后端未重启时不可用）
  const [redactReady, setRedactReady] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const boxRef = useRef(null);
  const stickRef = useRef(true);
  const onLogsRef = useRef(onLogs);
  onLogsRef.current = onLogs;

  // 页面加载时读取后端当前脱敏状态；失败说明后端未重启（接口不存在）
  useEffect(() => {
    let alive = true;
    api.registrations.logRedact()
      .then((res) => {
        if (!alive) return;
        setRedactReady(true);
        if (res && typeof res.enabled === "boolean") setRedact(res.enabled);
      })
      .catch(() => {
        if (alive) setRedactReady(false);
      });
    return () => { alive = false; };
  }, []);

  const toggleRedact = async () => {
    const next = !redact;
    try {
      const res = await api.registrations.setLogRedact(next);
      setRedactReady(true);
      setRedact(res.enabled);
      toast(next ? "日志明文显示已开启（仅影响之后新产生的日志，注意屏幕安全）" : "日志脱敏已恢复", "info");
      // 切换后重新拉取当前任务日志，让后续行按新状态显示
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setRedactReady(false);
      toast(`明文开关切换失败：${e.message || "接口不可用"}（后端可能未重启，请重启后端后刷新页面）`, "warning");
    }
  };

  const apply = useCallback((res, source = "reg") => {
    const nextCursor = Number(res?.next ?? 0) || 0;
    if (source === "batch") setBatchNext((current) => Math.max(current, nextCursor));
    else setNext((current) => Math.max(current, nextCursor));
    if (res?.logs?.length) {
      const tagged = res.logs.map((line) => ({ ...line, source, _key: `${source}-${line.seq}` }));
      setLogs((prev) => {
        const seen = new Set(prev.map((line) => line._key || `${line.source || source}-${line.seq}`));
        const merged = [...prev];
        tagged.forEach((line) => {
          if (!seen.has(line._key)) {
            seen.add(line._key);
            merged.push(line);
          }
        });
        return merged;
      });
      onLogsRef.current?.(tagged);
    }
  }, []);

  useEffect(() => {
    // 任务切换到空状态时也要清掉旧任务日志，避免终态日志伪装成当前任务进度。
    setLogs([]);
    setNext(0);
    setBatchNext(0);
    setFilter("all");
    if (!regId && !batchId) return undefined;
    let alive = true;

    const fetchOnce = async (source, after, limit) => {
      try {
        const res = source === "batch"
          ? await api.batches.logs(batchId, { after, limit })
          : await api.registrations.logs(regId, { after, limit });
        if (alive) apply(res, source);
        return res;
      } catch { /* 网络抖动忽略 */ }
      return null;
    };

    const drainBacklog = async (source, after, limit, maxPages = MAX_BACKLOG_PAGES_PER_TICK) => {
      let cursor = Number(after || 0) || 0;
      for (let page = 0; alive && page < maxPages; page += 1) {
        const res = await fetchOnce(source, cursor, limit);
        if (!res?.logs?.length) break;
        cursor = Number(res.next ?? cursor) || cursor;
        if (!res.has_more || limit <= 0) break;
      }
    };

    // 首次拉取：运行中分批补齐积压；已结束任务直接拉全量，避免只显示前/后 500 行。
    if (regId) drainBacklog("reg", 0, active ? INITIAL_LOG_FETCH_LIMIT : 0, active ? MAX_BACKLOG_PAGES_PER_TICK : 1);
    if (batchId) drainBacklog("batch", 0, active ? INITIAL_LOG_FETCH_LIMIT : 0, active ? MAX_BACKLOG_PAGES_PER_TICK : 1);
    if (!active) return () => { alive = false; };

    const timer = setInterval(async () => {
      if (!alive) return;
      // 两个游标独立推进，避免 Gmail 准备阶段没有 reg_id 时丢失批量日志。
      if (regId) {
        setNext((n) => {
          drainBacklog("reg", n, POLL_LOG_FETCH_LIMIT);
          return n;
        });
      }
      if (batchId) {
        setBatchNext((n) => {
          drainBacklog("batch", n, POLL_LOG_FETCH_LIMIT);
          return n;
        });
      }
    }, 1000);
    return () => { alive = false; clearInterval(timer); };
  }, [regId, batchId, active, apply, refreshKey]);

  // 自动滚动到底部
  useEffect(() => {
    const el = boxRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [logs, filter]);

  const onScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    stickRef.current = atBottom;
    setAutoScroll(atBottom);
  };

  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { return false; }
  };

  const loadCompleteLogs = async () => {
    const complete = [];
    const requests = [];
    if (batchId) requests.push(api.batches.logs(batchId, { after: 0, limit: 0 }).then((res) => ["batch", res]));
    if (regId) requests.push(api.registrations.logs(regId, { after: 0, limit: 0 }).then((res) => ["reg", res]));
    try {
      const results = await Promise.all(requests);
      results.forEach(([source, res]) => {
        if (Array.isArray(res?.logs)) {
          complete.push(...res.logs.map((line) => ({ ...line, source, _key: `${source}-${line.seq}` })));
        }
      });
    } catch {
      return logs;
    }
    return complete.length ? complete : logs;
  };

  const formatLogText = (items) => {
    const mixedSources = new Set(items.map((line) => line.source).filter(Boolean)).size > 1;
    return items.map((l) => `${l.ts} ${mixedSources ? `[${l.source}] ` : ""}${l.msg}`).join("\n");
  };

  const copyLogs = async () => {
    const completeLogs = await loadCompleteLogs();
    const text = formatLogText(completeLogs);
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const copyLine = async (l) => {
    if (await copyText(`${l.ts} ${l.msg}`)) {
      setCopiedLine(l._key || l.seq);
      setTimeout(() => setCopiedLine(null), 1500);
    }
  };

  const downloadLogs = () => {
    loadCompleteLogs().then((completeLogs) => {
      const text = formatLogText(completeLogs);
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${batchId ? `batch_${batchId}` : `reg_${regId}`}_log_${Date.now().toString().slice(-6)}.log`;
      a.click();
      URL.revokeObjectURL(a.href);
    });
  };

  const clearLogs = async () => {
    if (clearing) return;
    setClearing(true);
    try {
      const requests = [];
      if (regId) requests.push(api.registrations.clearLogs(regId));
      if (batchId) requests.push(api.batches.clearLogs(batchId));
      await Promise.all(requests);
      setLogs([]);
      setNext(0);
      setBatchNext(0);
      setFilter("all");
      stickRef.current = true;
      setAutoScroll(true);
      toast("当前任务日志已清空", "success");
    } catch (error) {
      toast(`日志清空失败：${error.message || "请求失败"}`, "error");
    } finally {
      setClearing(false);
    }
  };

  const visibleLogs = filterLogLines(logs, filter);
  const renderedLogs = visibleLogs.slice(-LIVE_LOG_RENDER_LIMIT);
  const hiddenLogCount = Math.max(0, visibleLogs.length - renderedLogs.length);
  const levelCounts = { success: 0, warning: 0, error: 0, info: 0 };
  logs.forEach((l) => { levelCounts[normalizeLogLevel(l.msg)] += 1; });

  return (
    <div className="overflow-hidden rounded-lg border border-slate-700 bg-[#0d1117]">
      {/* 工具条 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-2">
        <Terminal size={13} className="text-slate-500" />
        <span className="text-xs font-medium text-slate-300">{title}</span>
        {active && <span className="flex items-center gap-1 text-[11px] text-emerald-500"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />LIVE</span>}
        {logs.length > 0 && (
          <span className="text-[11px] text-slate-500">
            {t(active ? `${logs.length} 行` : `已结束 · ${logs.length} 行`)}
          </span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {/* 明文显示开关（调试用，默认脱敏；后端未重启时不可用） */}
          <button
            type="button"
            disabled={!redactReady}
            title={!redactReady
              ? "明文开关不可用：后端未加载 /registrations/log-redact 接口，请重启后端后刷新页面"
              : redact
                ? "日志已脱敏（密码/TOTP/验证码打码）。点击切换为明文显示（仅调试用，影响之后新产生的日志）"
                : "日志明文显示中（可能包含密码/TOTP/验证码）。点击恢复脱敏"}
            onClick={toggleRedact}
            className={`inline-flex h-6 items-center gap-1 rounded px-1.5 text-[11px] transition-colors ${
              !redactReady
                ? "cursor-not-allowed bg-slate-800/60 text-slate-600"
                : redact
                  ? "bg-slate-800 text-slate-400 hover:text-slate-200"
                  : "bg-red-500/25 text-red-400 ring-1 ring-red-500/40"
            }`}>
            {redactReady ? (redact ? <EyeOff size={11} /> : <Eye size={11} />) : <EyeOff size={11} className="opacity-50" />}
            {!redactReady ? "不可用" : redact ? "脱敏" : "明文"}
          </button>
          {/* 级别过滤 */}
          <div className="flex items-center gap-0.5 rounded bg-slate-800/80 px-1 py-0.5">
            <Filter size={11} className="ml-0.5 text-slate-500" />
            {LEVEL_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setFilter(opt.value)}
                title={opt.value !== "all" ? `${opt.label} ${levelCounts[opt.value] || 0} 条` : "全部级别"}
                className={`rounded px-1.5 py-0.5 text-[11px] transition-colors ${
                  filter === opt.value
                    ? opt.value === "error" ? "bg-red-500/20 text-red-400"
                      : opt.value === "success" ? "bg-emerald-500/20 text-emerald-400"
                        : opt.value === "warning" ? "bg-amber-500/20 text-amber-400"
                          : "bg-slate-700 text-slate-200"
                    : "text-slate-500 hover:text-slate-300"
                }`}>
                {opt.label}
                {opt.value !== "all" && levelCounts[opt.value] > 0 && (
                  <span className="ml-0.5 tnum opacity-70">{levelCounts[opt.value]}</span>
                )}
              </button>
            ))}
          </div>
          <button
            title={autoScroll ? "自动跟随（点击暂停）" : "回到底部"}
            onClick={() => { stickRef.current = true; setAutoScroll(true); boxRef.current && (boxRef.current.scrollTop = boxRef.current.scrollHeight); }}
            className={`inline-flex h-6 items-center gap-1 rounded px-1.5 text-[11px] transition-colors ${autoScroll ? "bg-slate-800 text-slate-300" : "bg-blue-600/20 text-blue-400 hover:bg-blue-600/30"}`}>
            <ChevronDown size={11} />{autoScroll ? "跟随" : "回到底部"}
          </button>
          <button title={t("复制日志")} onClick={copyLogs} className="inline-flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            {copied ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
          </button>
          <button title={t("下载日志")} onClick={downloadLogs} className="inline-flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            <ArrowDownToLine size={12} />
          </button>
          <button title={t("清空当前任务日志")} disabled={clearing || (!regId && !batchId)} onClick={clearLogs} className="inline-flex h-6 w-6 items-center justify-center rounded text-slate-400 hover:bg-slate-800 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-40">
            {clearing ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
          </button>
        </div>
      </div>
      {/* 日志体 */}
      <div ref={boxRef} onScroll={onScroll} className="overflow-y-auto px-3 py-2" style={{ height }}>
        {!redact && (
          <div className="mb-2 flex items-start gap-1.5 rounded border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] leading-relaxed text-red-300">
            <ShieldAlert size={12} className="mt-0.5 shrink-0" />
            <span>明文日志已开启：后续日志可能包含密码 / TOTP secret / 验证码明文，请勿共享屏幕；关闭后仅影响新产生的日志。</span>
          </div>
        )}
        {logs.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-600">
            {active ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                <span className="text-[11px]">等待注册任务输出日志…</span>
              </>
            ) : (
              <span className="text-[11px]">该任务暂无日志记录</span>
            )}
          </div>
        )}
        {logs.length > 0 && visibleLogs.length === 0 && (
          <div className="flex h-full items-center justify-center text-[11px] text-slate-600">
            {t(`当前级别下暂无日志（共 ${logs.length} 行）`)}
          </div>
        )}
        {hiddenLogCount > 0 && (
          <div className="sticky top-0 z-10 mb-1 rounded border border-slate-700 bg-slate-900/95 px-2 py-1 text-[11px] text-slate-400">
            已隐藏较早 {hiddenLogCount} 行，仅渲染最近 {renderedLogs.length} 行；复制/下载会从后端拉取完整日志。
          </div>
        )}
        {renderedLogs.map((l) => {
          const lv = normalizeLogLevel(l.msg);
          const meta = LOG_LEVEL_META[lv];
          const isCopied = copiedLine === (l._key || l.seq);
          return (
            <div
              key={l._key || l.seq}
              onClick={() => copyLine(l)}
              title="点击复制该行"
              className={`group flex cursor-pointer items-start gap-2 rounded px-1 py-px font-mono text-[11.5px] leading-[1.7] transition-colors hover:bg-slate-800/60 ${meta.rowBg}`}
            >
              <span className={`mt-[6px] h-1 w-1 shrink-0 rounded-full ${DOT[lv]}`} />
              <span className={`mt-0.5 shrink-0 text-[9.5px] font-semibold tracking-wide ${meta.color}`}>{meta.label}</span>
              <span className="shrink-0 text-slate-600">{l.ts}</span>
              <span className={`min-w-0 whitespace-pre-wrap break-all ${meta.color}`}>{l.msg}</span>
              <span className={`ml-auto shrink-0 self-center opacity-0 transition-opacity group-hover:opacity-100 ${isCopied ? "text-emerald-500" : "text-slate-500"}`}>
                {isCopied ? <Check size={11} /> : <Copy size={11} />}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
