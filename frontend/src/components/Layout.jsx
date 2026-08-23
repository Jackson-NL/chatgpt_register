import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard, Users, UserPlus, Network, Mail, KeyRound,
  Settings, Search, RefreshCw, Bell, Menu, X, ChevronDown, User, LogOut, ShieldCheck, CornerDownLeft, RotateCcw, Moon, Sun, Link2,
} from "lucide-react";
import { useApp } from "../context/AppContext";
import { ToastHost } from "./ui";
import { fmtAgo } from "../mock/data";

const NAV = [
  { to: "/", label: "仪表盘", icon: LayoutDashboard, end: true },
  { to: "/register", label: "注册工作台", icon: UserPlus },
  { to: "/codex-oauth", label: "Codex OAuth", icon: KeyRound },
  { to: "/sub2api-relogin", label: "Sub2API 重登", icon: RotateCcw },
  { to: "/link-extraction", label: "提链工作台", icon: Link2 },
  { to: "/accounts", label: "账号管理", icon: Users },
  { to: "/proxies", label: "代理池", icon: Network },
  { to: "/mail-config", label: "邮箱配置", icon: Mail },
  { to: "/settings", label: "系统设置", icon: Settings },
  { to: "/admin", label: "管理员入口", icon: ShieldCheck },
];

const LEVEL_DOT = { danger: "bg-red-500", warning: "bg-amber-500", success: "bg-emerald-500", info: "bg-blue-500" };

export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const { notifyOpen, setNotifyOpen, notifications, markAllRead, updatedAt, updatedLabel, autoRefresh, setAutoRefresh, refreshNow, searchOpen, setSearchOpen, searchQ, setSearchQ, searchResults, gotoWithFilter, theme, toggleTheme, t, adminEnabled } = useApp();
  const navigate = useNavigate();
  const unread = notifications.filter((n) => !n.read).length;
  const navItems = adminEnabled ? NAV : NAV.filter((item) => item.to !== "/admin");

  const goSearch = (r) => {
    setSearchOpen(false);
    setSearchQ("");
    gotoWithFilter(r.path, r.filter || {});
  };

  return (
    <div className="flex h-full">
      {/* 移动端遮罩 */}
      {sidebarOpen && <div className="fixed inset-0 z-30 bg-slate-900/40 lg:hidden" onClick={() => setSidebarOpen(false)} />}

      {/* 左侧导航 */}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r border-slate-200 bg-white transition-transform lg:static lg:translate-x-0 ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center gap-2.5 border-b border-slate-200 px-4 py-3.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-blue-600 text-white">
            <ShieldCheck size={17} />
          </div>
          <div>
            <div className="text-[14px] font-semibold leading-tight text-slate-800">AccountOps</div>
            <div className="text-[11px] text-slate-400">账号运维控制台</div>
          </div>
          <button className="ml-auto text-slate-400 hover:text-slate-600 lg:hidden" onClick={() => setSidebarOpen(false)}><X size={16} /></button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-3">
          {navItems.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} onClick={() => setSidebarOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors ${
                  isActive ? "bg-blue-50 font-medium text-blue-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`
              }>
              <n.icon size={15.5} strokeWidth={1.8} />
              {t(n.label)}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-200 px-4 py-3 text-[11px] text-slate-400">
          <div className="flex items-center gap-1.5"><ShieldCheck size={12} /> AccountOps v0.2.0</div>
        </div>
      </aside>

      {/* 主区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 顶部工具栏 */}
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-4">
          <button className="text-slate-500 hover:text-slate-800 lg:hidden" onClick={() => setSidebarOpen(true)}><Menu size={18} /></button>
          <button onClick={() => setSearchOpen(true)} title={t("全局搜索")}
            className="flex h-8 w-56 items-center gap-2 rounded-md border border-slate-200 px-2.5 text-[13px] text-slate-400 transition-colors hover:border-slate-300 hover:text-slate-500 sm:w-64">
            <Search size={14} />全局搜索
            <kbd className="ml-auto rounded border border-slate-200 bg-slate-50 px-1 text-[10px] text-slate-400">/</kbd>
          </button>

          <div className="ml-auto flex items-center gap-2">
            {/* 数据更新时间 + 自动刷新 */}
            <div className="hidden items-center gap-1.5 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 sm:flex">
              <span className={`h-1.5 w-1.5 rounded-full ${autoRefresh ? "animate-pulse bg-emerald-500" : "bg-slate-300"}`} />
            <span className="tnum">{t("更新于")} {updatedLabel}</span>
            </div>
            <button title={t(autoRefresh ? "关闭自动刷新" : "开启自动刷新")}
              onClick={() => setAutoRefresh(!autoRefresh)}
              className={`inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors ${autoRefresh ? "text-blue-600 hover:bg-blue-50" : "text-slate-400 hover:bg-slate-100"}`}>
              <RefreshCw size={15} />
            </button>
            <button title={t("立即刷新")} onClick={refreshNow}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800">
              <RefreshCw size={15} className="rotate-90" />
            </button>

            <button
              title={t(theme === "dark" ? "切换浅色主题" : "切换暗色主题")}
              aria-label={t(theme === "dark" ? "切换浅色主题" : "切换暗色主题")}
              onClick={toggleTheme}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800"
            >
              {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            </button>

            {/* 通知中心 */}
            <div className="relative">
              <button onClick={() => setNotifyOpen(!notifyOpen)}
                className="relative inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-800">
                <Bell size={15} />
                {unread > 0 && <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-red-500 ring-2 ring-white" />}
              </button>
              {notifyOpen && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setNotifyOpen(false)} />
                  <div className="absolute right-0 z-30 mt-1.5 w-80 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg">
                    <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
                      <div className="text-[13px] font-semibold text-slate-700">{t("通知中心")} <span className="ml-1 text-xs font-normal text-slate-400">({unread} {t("未读")})</span></div>
                      <button className="text-xs text-blue-600 hover:text-blue-700" onClick={markAllRead}>{t("全部已读")}</button>
                    </div>
                    <div className="max-h-72 overflow-y-auto">
                      {notifications.map((n) => (
                        <div key={n.id} className={`flex gap-2.5 border-b border-slate-50 px-3 py-2.5 ${n.read ? "opacity-60" : ""}`}>
                          <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${LEVEL_DOT[n.level]}`} />
                          <div className="min-w-0">
                            <div className="text-[12.5px] text-slate-700">{n.title}</div>
                            <div className="mt-0.5 text-[11px] text-slate-400">{fmtAgo(n.time)}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* 用户菜单 */}
            <div className="relative">
              <button onClick={() => setUserOpen(!userOpen)}
                className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-slate-100">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-700 text-[11px] font-medium text-white">admin</span>
                <span className="hidden text-[13px] text-slate-700 sm:block">admin</span>
                <ChevronDown size={13} className="text-slate-400" />
              </button>
              {userOpen && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setUserOpen(false)} />
                  <div className="absolute right-0 z-30 mt-1.5 w-44 overflow-hidden rounded-md border border-slate-200 bg-white py-1 shadow-lg">
                    <div className="border-b border-slate-100 px-3 py-2">
                      <div className="text-[13px] font-medium text-slate-700">admin</div>
                      <div className="text-[11px] text-slate-400">ops@example.com</div>
                    </div>
                    {[
                      { icon: User, label: "个人资料" },
                      { icon: Settings, label: "偏好设置" },
                      { icon: LogOut, label: "退出登录", danger: true },
                    ].map((m) => (
                      <button key={m.label} onClick={() => setUserOpen(false)}
                        className={`flex w-full items-center gap-2 px-3 py-1.5 text-[13px] hover:bg-slate-50 ${m.danger ? "text-red-600" : "text-slate-700"}`}>
                        <m.icon size={14} />{t(m.label)}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4">
          <Outlet />
        </main>
      </div>

      {/* 全局搜索弹层 */}
      {searchOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh]">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setSearchOpen(false)} />
          <div className="relative z-10 w-full max-w-lg overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-center gap-2 border-b border-slate-200 px-3 py-2.5">
              <Search size={15} className="text-slate-400" />
              <input autoFocus value={searchQ} onChange={(e) => setSearchQ(e.target.value)}
                placeholder={t("搜索账号 / 任务 ID / 代理 / 日志关键词…")} className="flex-1 text-[14px] outline-none" />
              <kbd className="rounded border border-slate-200 bg-slate-50 px-1 text-[10px] text-slate-400">ESC</kbd>
            </div>
            <div className="max-h-80 overflow-y-auto py-1">
              {!searchQ && <div className="px-3 py-6 text-center text-xs text-slate-400">{t("输入关键词开始搜索")}</div>}
              {searchQ && searchResults.length === 0 && <div className="px-3 py-6 text-center text-xs text-slate-400">{t("未找到与 “")}{searchQ}{t("相关的结果")}</div>}
              {searchResults.map((r, i) => (
                <button key={i} onClick={() => goSearch(r)}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-slate-50">
                  <span className="w-12 shrink-0 text-[11px] text-slate-400">{r.type}</span>
                  <span className="mono text-slate-700">{r.id}</span>
                  <span className="truncate text-[12px] text-slate-400">{r.title}</span>
                  <CornerDownLeft size={12} className="ml-auto text-slate-300" />
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      <ToastHost />
    </div>
  );
}
