import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import { AppProvider } from "./context/AppContext";
import Dashboard from "./pages/Dashboard";
import Register from "./pages/Register";
import Accounts from "./pages/Accounts";
import Proxies from "./pages/Proxies";
import Settings from "./pages/Settings";
import MailConfig from "./pages/MailConfig";
import CodexOAuth from "./pages/CodexOAuth";
import Sub2APIRelogin from "./pages/Sub2APIRelogin";
import LinkExtraction from "./pages/LinkExtraction";
import Admin, { AdminLoginScreen } from "./pages/Admin";
import { useApp } from "./context/AppContext";

function AdminGate({ children }) {
  const { adminChecking, adminEnabled, adminAuthenticated } = useApp();

  if (adminChecking) {
    return <div className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-400">正在验证管理员会话…</div>;
  }

  if (!adminEnabled) return children;
  if (!adminAuthenticated) return <AdminLoginScreen />;
  return children;
}

export default function App() {
  return (
    <AppProvider>
      <AdminGate>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/register" element={<Register />} />
            <Route path="/codex-oauth" element={<CodexOAuth />} />
            <Route path="/sub2api-relogin" element={<Sub2APIRelogin />} />
            <Route path="/link-extraction" element={<LinkExtraction />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/proxies" element={<Proxies />} />
            <Route path="/mail-config" element={<MailConfig />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="*" element={<Dashboard />} />
          </Route>
        </Routes>
      </AdminGate>
    </AppProvider>
  );
}
