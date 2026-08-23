import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { genNotifications, fmtAgo } from "../mock/data";
import { applyTheme, readStoredTheme, THEME_STORAGE_KEY } from "../theme";
import { installDocumentTranslator, readStoredLanguage, saveStoredLanguage, translate } from "../i18n";
import { api } from "../api";

const AppCtx = createContext(null);

let toastSeq = 0;

export function AppProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [notifications, setNotifications] = useState(genNotifications());
  const [notifyOpen, setNotifyOpen] = useState(false);
  const [updatedAt, setUpdatedAt] = useState(Date.now());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [theme, setTheme] = useState(() => readStoredTheme());
  const [language, setLanguageState] = useState(() => readStoredLanguage());
  const [adminChecking, setAdminChecking] = useState(true);
  const [adminEnabled, setAdminEnabled] = useState(true);
  const [adminAuthenticated, setAdminAuthenticated] = useState(false);
  const [adminOverview, setAdminOverview] = useState(null);
  const navigate = useNavigate();
  const timerRef = useRef(null);

  const refreshAdminSession = useCallback(async () => {
    setAdminChecking(true);
    try {
      const status = await api.admin.status();
      const enabled = status?.enabled !== false;
      setAdminEnabled(enabled);
      if (!enabled) {
        setAdminOverview(null);
        setAdminAuthenticated(false);
        return null;
      }
      const overview = await api.admin.overview();
      setAdminOverview(overview);
      setAdminAuthenticated(true);
      return overview;
    } catch {
      setAdminOverview(null);
      setAdminAuthenticated(false);
      setAdminEnabled(true);
      return null;
    } finally {
      setAdminChecking(false);
    }
  }, []);

  useEffect(() => {
    refreshAdminSession();
  }, [refreshAdminSession]);

  const adminLogin = useCallback(async (key) => {
    await api.admin.login(key);
    const overview = await refreshAdminSession();
    if (!overview) throw new Error("管理员会话校验失败，请重试");
    return overview;
  }, [refreshAdminSession]);

  const adminLogout = useCallback(async () => {
    try {
      await api.admin.logout();
    } finally {
      setAdminOverview(null);
      setAdminAuthenticated(false);
    }
  }, []);

  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Storage may be unavailable in private or restricted browser contexts.
    }
  }, [theme]);

  useEffect(() => {
    saveStoredLanguage(language);
    document.documentElement.lang = language;
    document.title = language === "en-US" ? "AccountOps · Account operations console" : "AccountOps · 账号运维控制台";
    return installDocumentTranslator(language);
  }, [language]);

  // Use the saved UI preference when opening the console in a new browser profile.
  useEffect(() => {
    let alive = true;
    fetch("/api/settings/ui")
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        const saved = payload?.general?.language;
        if (alive && (saved === "en-US" || saved === "zh-CN")) setLanguageState(saved);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // 自动刷新：每 5s 更新"最近更新于 xx 秒前"
  useEffect(() => {
    if (!autoRefresh) return undefined;
    const t = setInterval(() => setUpdatedAt(Date.now()), 5000);
    return () => clearInterval(t);
  }, [autoRefresh]);

  // 全局搜索（模拟）：在账号/任务/代理/日志中检索
  useEffect(() => {
    if (!searchQ.trim()) {
      setSearchResults([]);
      return;
    }
    const q = searchQ.trim().toLowerCase();
    const res = [];
    for (let i = 1; i <= 48; i++) {
      const id = `acc_${String(i).padStart(4, "0")}`;
      if (id.includes(q) || id.includes(q)) res.push({ type: "账号", id, title: `${id} · ${"user"}`, path: "/accounts", filter: { q: id } });
    }
    for (let i = 1000; i <= 1013; i++) {
      const id = `task_${i}`;
      if (id.includes(q)) res.push({ type: "任务", id, title: `${id} · 批量导入`, path: "/register", filter: {} });
    }
    for (let i = 1; i <= 12; i++) {
      const id = `prx_${String(i).padStart(3, "0")}`;
      if (id.includes(q)) res.push({ type: "代理", id, title: `${id} · proxy-sg-0${i}`, path: "/proxies", filter: { q: id } });
    }
    setSearchResults(res.slice(0, 8));
  }, [searchQ]);

  const toast = useCallback((message, type = "info", opts = {}) => {
    const id = ++toastSeq;
    setToasts((prev) => [...prev, { id, message, type, ...opts }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), opts.duration || 3600);
  }, []);

  const pushNotify = useCallback((n) => setNotifications((prev) => [n, ...prev]), []);

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  const refreshNow = useCallback(() => setUpdatedAt(Date.now()), []);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  const setLanguage = useCallback((value) => {
    setLanguageState(value === "en-US" ? "en-US" : "zh-CN");
  }, []);
  const t = useCallback((value) => translate(value, language), [language]);

  const gotoWithFilter = useCallback(
    (path, filter) => {
      navigate(path, { state: { filter } });
    },
    [navigate],
  );

  const value = useMemo(
    () => ({
      toasts,
      toast,
      notifications,
      pushNotify,
      notifyOpen,
      setNotifyOpen,
      markAllRead,
      updatedAt,
      autoRefresh,
      setAutoRefresh,
      refreshNow,
      searchOpen,
      setSearchOpen,
      searchQ,
      setSearchQ,
      searchResults,
      gotoWithFilter,
      theme,
      setTheme,
      toggleTheme,
      language,
      setLanguage,
      t,
      adminChecking,
      adminEnabled,
      adminAuthenticated,
      adminOverview,
      adminLogin,
      adminLogout,
      updatedLabel: fmtAgo(updatedAt),
    }),
    [toasts, toast, notifications, pushNotify, notifyOpen, markAllRead, updatedAt, autoRefresh, refreshNow, searchOpen, searchQ, searchResults, gotoWithFilter, theme, toggleTheme, language, setLanguage, t, adminChecking, adminEnabled, adminAuthenticated, adminOverview, adminLogin, adminLogout],
  );

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp() {
  return useContext(AppCtx);
}
