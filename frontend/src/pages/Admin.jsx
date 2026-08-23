import { useState } from "react";
import { CheckCircle2, KeyRound, LogIn, LogOut, ShieldCheck, ShieldAlert } from "lucide-react";
import { useApp } from "../context/AppContext";
import { Badge, Button, Input, PageHeader, Panel, RiskBanner } from "../components/ui";

function formatExpiry(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function AdminLoginScreen() {
  const { adminLogin, toast } = useApp();
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    const value = key.trim();
    if (!value) {
      setError("请输入管理员密匙");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await adminLogin(value);
      setKey("");
      toast("管理员授权成功", "success");
    } catch (err) {
      setError(err?.message || "管理员授权失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-8">
      <section className="w-full max-w-xl rounded-lg border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="mb-7 flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-slate-900 text-white">
            <ShieldCheck size={20} />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">管理员入口</h1>
            <p className="mt-1 text-xs leading-5 text-slate-500">输入管理员密匙后进入账号运维控制台。</p>
          </div>
        </div>

        {error && <RiskBanner level="danger" title="授权失败" className="mb-4">{error}</RiskBanner>}

        <form onSubmit={submit} className="space-y-4">
          <Input
            label="管理员密匙"
            type="password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="请输入管理员密匙"
            autoComplete="current-password"
            autoFocus
            icon={<KeyRound size={15} />}
            disabled={submitting}
          />
          <Button type="submit" className="w-full justify-center" icon={<LogIn size={15} />} loading={submitting}>
            进入控制台
          </Button>
        </form>

        <div className="mt-5 flex items-start gap-2 border-t border-slate-100 pt-4 text-[11px] leading-5 text-slate-400">
          <ShieldAlert size={14} className="mt-0.5 shrink-0" />
          <span>未通过管理员校验前，导航和所有工作台均不可访问；密匙不会保存在浏览器本地。</span>
        </div>
      </section>
    </div>
  );
}

export default function Admin() {
  const { adminEnabled, adminOverview, adminLogout, toast } = useApp();

  if (!adminEnabled) {
    return (
      <div className="space-y-4">
        <PageHeader title="管理员入口" subtitle="管理员功能当前未启用" />
        <Panel title="功能状态">
          <div className="text-sm text-slate-600">管理员入口已关闭，当前无需输入管理员密匙。</div>
        </Panel>
      </div>
    );
  }

  const logout = async () => {
    try {
      await adminLogout();
    } finally {
      toast("已退出管理员入口", "info");
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="管理员控制台"
        subtitle="受保护的系统运维入口"
        extra={<Button variant="secondary" size="sm" icon={<LogOut size={14} />} onClick={logout}>退出管理员入口</Button>}
      />

      <div className="grid gap-4 md:grid-cols-3">
        <Panel title="会话状态">
          <div className="flex items-center gap-2 text-sm text-slate-700"><CheckCircle2 size={16} className="text-emerald-500" />管理员已授权</div>
          <div className="mt-2 text-xs text-slate-400">有效期至 {formatExpiry(adminOverview?.expires_at)}</div>
        </Panel>
        <Panel title="访问方式">
          <div className="text-sm text-slate-700">HttpOnly 签名 Cookie</div>
          <div className="mt-2 text-xs text-slate-400">密匙不进入前端存储</div>
        </Panel>
        <Panel title="保护状态">
          <Badge color="success" dot>全局访问锁定已启用</Badge>
          <div className="mt-2 text-xs text-slate-400">未授权时导航和业务页面均不可进入</div>
        </Panel>
      </div>

      <Panel title="管理员能力">
        <div className="grid gap-2 sm:grid-cols-2">
          {(adminOverview?.capabilities || []).map((item) => (
            <div key={item} className="flex items-center gap-2 border-b border-slate-100 py-2 text-[13px] text-slate-700 last:border-0">
              <CheckCircle2 size={14} className="text-emerald-500" />{item}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
