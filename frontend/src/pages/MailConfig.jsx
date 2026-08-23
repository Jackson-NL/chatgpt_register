import { useEffect, useMemo, useState } from "react";
import {
  AtSign, CheckCircle2, Cloud, Eye, EyeOff, Inbox, KeyRound, Loader2, Mail,
  RefreshCw, RotateCcw, Save, ShieldCheck, Users, XCircle,
} from "lucide-react";
import { api } from "../api";
import { useApp } from "../context/AppContext";
import { Badge, Button, Input, Panel, Select, Switch } from "../components/ui";
import { fmtTime } from "../mock/data";

const MASK = "••••••••";

const DEFAULT_FORM = {
  provider: "cf_temp_email",
  cf_temp_email: {
    enabled: true,
    base_url: "https://temp-api.708651.xyz",
    domain: "708651.xyz",
    address_mode: "generated",
    custom_pool: "",
    custom_pool_count: 0,
    custom_pool_sample: [],
    inbox_address: "",
    inbox_jwt: "",
    has_inbox_jwt: false,
    site_password: "",
    name_prefix: "reg",
    random_length: 10,
    poll_interval: 4,
    poll_timeout: 180,
    max_retries: 3,
    rate_limit_backoff: 10,
    has_site_password: false,
  },
  outlook: {
    enabled: false,
    mode: "manual_pool",
    accounts_pool: "",
    accounts_count: 0,
    accounts_sample: [],
    poll_interval: 5,
    poll_timeout: 180,
    sender_filter: "",
    subject_filter: "",
    imap_host: "outlook.office365.com",
    imap_port: 993,
    imap_ssl: true,
    graph_tenant_id: "",
    graph_client_id: "",
    graph_client_secret: "",
    has_graph_client_secret: false,
  },
};

function normalizeConfig(data) {
  const cf = { ...DEFAULT_FORM.cf_temp_email, ...(data?.cf_temp_email || {}) };
  const outlook = { ...DEFAULT_FORM.outlook, ...(data?.outlook || {}) };
  cf.site_password = cf.has_site_password ? MASK : "";
  cf.custom_pool = "";
  cf.inbox_jwt = cf.has_inbox_jwt ? MASK : "";
  outlook.accounts_pool = "";
  outlook.graph_client_secret = outlook.has_graph_client_secret ? MASK : "";
  return {
    provider: data?.provider || DEFAULT_FORM.provider,
    cf_temp_email: cf,
    outlook,
  };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function fieldErrors(form) {
  const errors = {};
  const cf = form.cf_temp_email;
  try {
    const url = new URL(cf.base_url);
    if (!["http:", "https:"].includes(url.protocol)) errors.base_url = "必须是 http(s) URL";
  } catch {
    errors.base_url = "请输入有效的 http(s) URL";
  }
  if (!String(cf.domain || "").trim()) errors.domain = "域名不能为空";
  if (!["generated", "custom_pool"].includes(cf.address_mode)) errors.address_mode = "地址来源不正确";
  if (cf.address_mode === "custom_pool") {
    if (!String(cf.inbox_address || "").trim() || !String(cf.inbox_address).includes("@")) {
      errors.inbox_address = "固定收件邮箱格式不正确";
    }
    if (!cf.has_inbox_jwt && !String(cf.inbox_jwt || "").trim()) errors.inbox_jwt = "请配置固定收件 JWT";
    if (!cf.custom_pool_count && !String(cf.custom_pool || "").trim()) errors.custom_pool = "请添加至少一个自定义邮箱";
  }
  for (const key of ["random_length", "poll_interval", "poll_timeout", "max_retries", "rate_limit_backoff"]) {
    if (!Number.isInteger(Number(cf[key])) || Number(cf[key]) <= 0) errors[`cf_${key}`] = "必须为正整数";
  }
  const out = form.outlook;
  if (!Number.isInteger(Number(out.poll_interval)) || Number(out.poll_interval) <= 0) errors.out_poll_interval = "必须为正整数";
  if (!Number.isInteger(Number(out.poll_timeout)) || Number(out.poll_timeout) <= 0) errors.out_poll_timeout = "必须为正整数";
  if (!Number.isInteger(Number(out.imap_port)) || Number(out.imap_port) <= 0) errors.imap_port = "必须为正整数";
  return errors;
}

function FormNumber({ label, value, onChange, error, hint }) {
  return (
    <Input
      label={label}
      type="number"
      min="1"
      value={value}
      error={error}
      hint={hint}
      onChange={(event) => onChange(Number(event.target.value))}
    />
  );
}

function SecretInput({ label, value, configured, visible, onToggle, onChange, hint, error }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      <div className="relative">
        <input
          className={`input pr-9 ${error ? "border-red-400" : ""}`}
          type={visible ? "text" : "password"}
          value={value}
          placeholder={configured ? "已配置，留空不修改" : "可选"}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          title={visible ? "隐藏字段" : "显示字段"}
          onClick={onToggle}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
        >
          {visible ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
      {error ? <span className="mt-1 block text-[11px] text-red-600">{error}</span> : hint && <span className="mt-1 block text-[11px] text-slate-400">{hint}</span>}
    </label>
  );
}

function ProviderCard({ active, icon, title, description, status, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex min-h-[96px] items-start gap-3 rounded-md border p-3 text-left transition-colors ${
        active ? "border-blue-500 bg-blue-50/50 ring-1 ring-blue-500" : "border-slate-200 bg-white hover:border-slate-300"
      }`}
    >
      <span className={`mt-0.5 ${active ? "text-blue-600" : "text-slate-400"}`}>{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-[13px] font-semibold text-slate-700">
          {title}
          {active && <Badge color="primary">当前启用</Badge>}
        </span>
        <span className="mt-1 block text-xs leading-relaxed text-slate-400">{description}</span>
        <span className="mt-2 block text-[11px] text-slate-500">{status}</span>
      </span>
    </button>
  );
}

export default function MailConfig() {
  const { toast, t } = useApp();
  const [form, setForm] = useState(clone(DEFAULT_FORM));
  const [serverForm, setServerForm] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [lastTest, setLastTest] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [touched, setTouched] = useState({ sitePassword: false, customPool: false, inboxJwt: false, accountsPool: false, graphSecret: false });
  const [visible, setVisible] = useState({ sitePassword: false, inboxJwt: false, graphSecret: false });

  const load = async () => {
    setLoading(true);
    try {
      const data = await api.mailConfig.get();
      const normalized = normalizeConfig(data);
      setForm(normalized);
      setServerForm(clone(normalized));
      setUpdatedAt(data.updated_at || null);
      setLastTest(data.test_status || null);
      setTouched({ sitePassword: false, customPool: false, inboxJwt: false, accountsPool: false, graphSecret: false });
    } catch (error) {
      toast(`邮箱配置加载失败: ${error.message}`, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const patch = (section, key, value) => {
    setForm((current) => ({ ...current, [section]: { ...current[section], [key]: value } }));
    if (section === "cf_temp_email" && key === "site_password") setTouched((state) => ({ ...state, sitePassword: true }));
    if (section === "cf_temp_email" && key === "custom_pool") setTouched((state) => ({ ...state, customPool: true }));
    if (section === "cf_temp_email" && key === "inbox_jwt") setTouched((state) => ({ ...state, inboxJwt: true }));
    if (section === "outlook" && key === "accounts_pool") setTouched((state) => ({ ...state, accountsPool: true }));
    if (section === "outlook" && key === "graph_client_secret") setTouched((state) => ({ ...state, graphSecret: true }));
  };

  const errors = useMemo(() => fieldErrors(form), [form]);
  const dirty = useMemo(() => JSON.stringify(form) !== JSON.stringify(serverForm), [form, serverForm]);
  const cfConfigured = form.cf_temp_email.has_site_password;
  const graphConfigured = form.outlook.has_graph_client_secret;

  const buildPayload = () => {
    const body = {
      provider: form.provider,
      cf_temp_email: { ...form.cf_temp_email },
      outlook: { ...form.outlook },
    };
    delete body.cf_temp_email.has_site_password;
    delete body.cf_temp_email.custom_pool_count;
    delete body.cf_temp_email.custom_pool_sample;
    delete body.outlook.accounts_count;
    delete body.outlook.accounts_sample;
    delete body.outlook.has_graph_client_secret;
    if (!touched.sitePassword || !body.cf_temp_email.site_password || body.cf_temp_email.site_password === MASK) {
      delete body.cf_temp_email.site_password;
    }
    if (!touched.customPool) delete body.cf_temp_email.custom_pool;
    if (!touched.inboxJwt || !body.cf_temp_email.inbox_jwt || body.cf_temp_email.inbox_jwt === MASK) {
      delete body.cf_temp_email.inbox_jwt;
    }
    if (!touched.accountsPool) delete body.outlook.accounts_pool;
    if (!touched.graphSecret || !body.outlook.graph_client_secret || body.outlook.graph_client_secret === MASK) {
      delete body.outlook.graph_client_secret;
    }
    return body;
  };

  const validate = () => {
    if (Object.keys(errors).length) {
      toast("存在校验错误，请修正后保存", "warning");
      return false;
    }
    return true;
  };

  const save = async () => {
    if (!validate()) return;
    setSaving(true);
    try {
      const data = await api.mailConfig.save(buildPayload());
      const normalized = normalizeConfig(data);
      setForm(normalized);
      setServerForm(clone(normalized));
      setUpdatedAt(data.updated_at || null);
      setLastTest(data.test_status || lastTest);
      setTouched({ sitePassword: false, customPool: false, inboxJwt: false, accountsPool: false, graphSecret: false });
      toast("邮箱配置已保存", "success");
    } catch (error) {
      toast(`保存失败: ${error.message}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    if (!validate()) return;
    setTesting(true);
    try {
      const result = await api.mailConfig.test({ provider: form.provider, config: buildPayload() });
      setLastTest(result);
      toast(result.ok ? "邮箱连接测试成功" : `邮箱连接测试失败: ${result.message}`, result.ok ? "success" : "error");
    } catch (error) {
      const result = { ok: false, message: error.message };
      setLastTest(result);
      toast(`测试失败: ${error.message}`, "error");
    } finally {
      setTesting(false);
    }
  };

  const reset = () => {
    if (!serverForm) return;
    setForm(clone(serverForm));
    setTouched({ sitePassword: false, customPool: false, inboxJwt: false, accountsPool: false, graphSecret: false });
    toast("已撤销未保存修改", "info");
  };

  if (loading) {
    return <div className="flex items-center gap-2 py-12 text-sm text-slate-400"><Loader2 size={16} className="animate-spin" />正在加载邮箱配置…</div>;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[16px] font-semibold text-slate-800">邮箱配置</h2>
          <div className="mt-0.5 text-xs text-slate-400">注册流程邮箱 Provider · Cloudflare 临时邮箱与 Outlook 账号池</div>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <Badge color="warning">有未保存修改</Badge>}
          <Button variant="secondary" size="sm" icon={<RotateCcw size={13} />} onClick={reset} disabled={!dirty}>撤销</Button>
          <Button size="sm" icon={<Save size={13} />} onClick={save} loading={saving}>保存配置</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Panel title="当前 Provider">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
            <Mail size={16} className="text-blue-600" />
            {form.provider === "cf_temp_email" ? "Cloudflare 临时邮箱" : "Outlook 账号池"}
          </div>
          <div className="mt-1 text-xs text-slate-400">{form.provider}</div>
        </Panel>
        <Panel title="最近保存">
          <div className="text-sm font-semibold text-slate-700">{updatedAt ? fmtTime(updatedAt) : "尚未记录"}</div>
          <div className="mt-1 text-xs text-slate-400">配置写入后重启仍会保留</div>
        </Panel>
        <Panel title="最近测试">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
            {lastTest?.ok ? <CheckCircle2 size={16} className="text-emerald-500" /> : <XCircle size={16} className="text-slate-300" />}
            {lastTest ? (lastTest.ok ? "连接正常" : "连接失败") : "尚未测试"}
          </div>
          <div className="mt-1 truncate text-xs text-slate-400" title={lastTest?.message}>{lastTest?.message || "保存后可测试当前配置"}</div>
        </Panel>
      </div>

      <Panel title="选择邮箱 Provider" extra={<Badge color={form.provider === "cf_temp_email" ? "info" : "primary"}>仅支持 2 种</Badge>}>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <ProviderCard
            active={form.provider === "cf_temp_email"}
            icon={<Cloud size={18} />}
            title="Cloudflare 临时邮箱"
            description="自动创建地址并轮询解析邮件验证码。"
            status={form.cf_temp_email.enabled ? "已启用 · 自动收信" : "已停用"}
            onClick={() => setForm((current) => ({ ...current, provider: "cf_temp_email" }))}
          />
          <ProviderCard
            active={form.provider === "outlook"}
            icon={<Inbox size={18} />}
            title="Outlook 账号池"
            description="导入固定账号池；manual_pool 自动收信仍需 IMAP/Graph。"
            status={t(`${form.outlook.accounts_count} 个账号 · ${form.outlook.enabled ? "已启用" : "已停用"}`)}
            onClick={() => setForm((current) => ({ ...current, provider: "outlook" }))}
          />
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <Panel
          title={<span className="flex items-center gap-2"><Cloud size={15} className="text-blue-600" />cf_temp_email</span>}
          extra={form.provider === "cf_temp_email" && <Badge color="info" dot>当前启用</Badge>}
        >
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2">
              <div><div className="text-[13px] font-medium text-slate-700">启用 Provider</div><div className="text-[11px] text-slate-400">注册流程会使用该服务</div></div>
              <Switch checked={form.cf_temp_email.enabled} onChange={(value) => patch("cf_temp_email", "enabled", value)} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Select
                label="邮箱地址来源"
                options={[{ value: "generated", label: "CF 自动创建地址" }, { value: "custom_pool", label: "自定义邮箱池" }]}
                value={form.cf_temp_email.address_mode}
                onChange={(value) => patch("cf_temp_email", "address_mode", value)}
              />
              <Input label="API 地址" value={form.cf_temp_email.base_url} error={errors.base_url} onChange={(e) => patch("cf_temp_email", "base_url", e.target.value)} />
              <Input label="邮箱域名" value={form.cf_temp_email.domain} error={errors.domain} onChange={(e) => patch("cf_temp_email", "domain", e.target.value)} />
              <Input label="邮箱名前缀" value={form.cf_temp_email.name_prefix} onChange={(e) => patch("cf_temp_email", "name_prefix", e.target.value)} />
              <FormNumber label="随机后缀长度" value={form.cf_temp_email.random_length} error={errors.cf_random_length} onChange={(value) => patch("cf_temp_email", "random_length", value)} />
              <FormNumber label="轮询间隔（秒）" value={form.cf_temp_email.poll_interval} error={errors.cf_poll_interval} onChange={(value) => patch("cf_temp_email", "poll_interval", value)} />
              <FormNumber label="验证码超时（秒）" value={form.cf_temp_email.poll_timeout} error={errors.cf_poll_timeout} onChange={(value) => patch("cf_temp_email", "poll_timeout", value)} />
              <FormNumber label="最大重试次数" value={form.cf_temp_email.max_retries} error={errors.cf_max_retries} onChange={(value) => patch("cf_temp_email", "max_retries", value)} />
              <FormNumber label="429 退避（秒）" value={form.cf_temp_email.rate_limit_backoff} error={errors.cf_rate_limit_backoff} onChange={(value) => patch("cf_temp_email", "rate_limit_backoff", value)} />
            </div>
            {form.cf_temp_email.address_mode === "custom_pool" && (
              <div className="space-y-3 rounded-md border border-blue-100 bg-blue-50/40 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-[13px] font-medium text-slate-700">自定义邮箱池</div>
                    <div className="mt-0.5 text-[11px] text-slate-400">注册时使用池内地址；这些地址应已转发到下面的固定收件箱。</div>
                  </div>
                  <Badge color={form.cf_temp_email.custom_pool_count ? "success" : "warning"}>
                    {form.cf_temp_email.custom_pool_count} 个已配置
                  </Badge>
                </div>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-slate-600">邮箱地址池</span>
                  <textarea
                    className={`input min-h-28 resize-y font-mono text-xs ${errors.custom_pool ? "border-red-400" : ""}`}
                    value={form.cf_temp_email.custom_pool}
                    placeholder={form.cf_temp_email.custom_pool_count ? `已配置 ${form.cf_temp_email.custom_pool_count} 个地址；输入新内容可替换` : "每行一个邮箱地址，例如 name@example.com"}
                    onChange={(e) => patch("cf_temp_email", "custom_pool", e.target.value)}
                  />
                  {errors.custom_pool ? <span className="mt-1 block text-[11px] text-red-600">{errors.custom_pool}</span> : <span className="mt-1 block text-[11px] text-slate-400">保存后不会回显地址明文，只显示数量和脱敏样例。</span>}
                </label>
                {form.cf_temp_email.custom_pool_sample?.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5"><span className="text-[11px] text-slate-400">已保存样例：</span>{form.cf_temp_email.custom_pool_sample.map((sample) => <Badge key={sample} color="neutral">{sample}</Badge>)}</div>
                )}
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Input label="固定收件 CF 邮箱" value={form.cf_temp_email.inbox_address} error={errors.inbox_address} placeholder="例如 jackson@708651.xyz" onChange={(e) => patch("cf_temp_email", "inbox_address", e.target.value)} />
                  <SecretInput
                    label="固定收件 JWT"
                    value={form.cf_temp_email.inbox_jwt}
                    configured={form.cf_temp_email.has_inbox_jwt}
                    error={errors.inbox_jwt}
                    visible={visible.inboxJwt}
                    onToggle={() => setVisible((state) => ({ ...state, inboxJwt: !state.inboxJwt }))}
                    onChange={(value) => patch("cf_temp_email", "inbox_jwt", value)}
                    hint="仅用于轮询固定 CF 收件箱，接口不会返回明文。"
                  />
                </div>
              </div>
            )}
            <SecretInput
              label="站点访问密码"
              value={form.cf_temp_email.site_password}
              configured={cfConfigured}
              visible={visible.sitePassword}
              onToggle={() => setVisible((state) => ({ ...state, sitePassword: !state.sitePassword }))}
              onChange={(value) => patch("cf_temp_email", "site_password", value)}
              hint="请求会通过 x-custom-auth 发送，接口不会返回明文。"
            />
          </div>
        </Panel>

        <Panel
          title={<span className="flex items-center gap-2"><AtSign size={15} className="text-violet-600" />outlook</span>}
          extra={form.provider === "outlook" && <Badge color="primary" dot>当前启用</Badge>}
        >
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2">
              <div><div className="text-[13px] font-medium text-slate-700">启用 Provider</div><div className="text-[11px] text-slate-400">第一阶段使用 manual_pool</div></div>
              <Switch checked={form.outlook.enabled} onChange={(value) => patch("outlook", "enabled", value)} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Select label="收信模式" options={[{ value: "manual_pool", label: "manual_pool" }, { value: "imap", label: "imap（预留）" }, { value: "graph", label: "graph（预留）" }]} value={form.outlook.mode} onChange={(value) => patch("outlook", "mode", value)} />
              <div className="flex items-end pb-1 text-xs text-slate-500"><Users size={14} className="mr-1.5 text-slate-400" />{t(`已配置 ${form.outlook.accounts_count} 个账号`)}</div>
            </div>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">账号池</span>
              <textarea
                className="input min-h-28 resize-y font-mono text-xs"
                value={form.outlook.accounts_pool}
                placeholder={form.outlook.accounts_count ? `已配置 ${form.outlook.accounts_count} 个账号；输入新内容可替换，留空不修改` : "每行一个：email,password 或 email----password"}
                onChange={(e) => patch("outlook", "accounts_pool", e.target.value)}
              />
              <span className="mt-1 block text-[11px] text-slate-400">保存和接口返回均不会回显密码。清空文本并保存可清除账号池。</span>
            </label>
            {form.outlook.accounts_sample?.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5"><span className="text-[11px] text-slate-400">样例：</span>{form.outlook.accounts_sample.map((sample) => <Badge key={sample} color="neutral">{sample}</Badge>)}</div>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormNumber label="轮询间隔（秒）" value={form.outlook.poll_interval} error={errors.out_poll_interval} onChange={(value) => patch("outlook", "poll_interval", value)} />
              <FormNumber label="验证码超时（秒）" value={form.outlook.poll_timeout} error={errors.out_poll_timeout} onChange={(value) => patch("outlook", "poll_timeout", value)} />
              <Input label="发件人过滤" value={form.outlook.sender_filter} onChange={(e) => patch("outlook", "sender_filter", e.target.value)} />
              <Input label="主题过滤" value={form.outlook.subject_filter} onChange={(e) => patch("outlook", "subject_filter", e.target.value)} />
            </div>
            <div className="border-t border-slate-100 pt-3">
              <div className="mb-3 flex items-center gap-1.5 text-xs font-medium text-slate-600"><ShieldCheck size={14} />IMAP / Graph 预留字段</div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Input label="IMAP Host" value={form.outlook.imap_host} onChange={(e) => patch("outlook", "imap_host", e.target.value)} />
                <FormNumber label="IMAP Port" value={form.outlook.imap_port} error={errors.imap_port} onChange={(value) => patch("outlook", "imap_port", value)} />
                <Input label="Graph Tenant ID" value={form.outlook.graph_tenant_id} onChange={(e) => patch("outlook", "graph_tenant_id", e.target.value)} />
                <Input label="Graph Client ID" value={form.outlook.graph_client_id} onChange={(e) => patch("outlook", "graph_client_id", e.target.value)} />
                <div className="flex items-end pb-1"><Switch checked={form.outlook.imap_ssl} label="IMAP SSL" onChange={(value) => patch("outlook", "imap_ssl", value)} /></div>
                <SecretInput
                  label="Graph Client Secret"
                  value={form.outlook.graph_client_secret}
                  configured={graphConfigured}
                  visible={visible.graphSecret}
                  onToggle={() => setVisible((state) => ({ ...state, graphSecret: !state.graphSecret }))}
                  onChange={(value) => patch("outlook", "graph_client_secret", value)}
                />
              </div>
            </div>
          </div>
        </Panel>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-3">
        <Button variant="secondary" icon={testing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} onClick={testConnection} disabled={testing}>
          {testing ? "测试中…" : "测试当前 Provider"}
        </Button>
        {lastTest && <span className={`flex items-center gap-1.5 text-xs ${lastTest.ok ? "text-emerald-600" : "text-red-600"}`}><KeyRound size={13} />{lastTest.message}</span>}
      </div>
    </div>
  );
}
