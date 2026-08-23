# Cloudflare 临时邮箱 (cf-temp-mail)

用 Cloudflare Email Routing + Worker + KV 搭一个临时邮箱，接收 ChatGPT 注册验证邮件，HTTP API 查询验证码。

## 架构

```
任意地址@yourdomain.com
        │  Email Routing (catch-all)
        ▼
   Cloudflare Worker (email handler)
        │  提取 6 位验证码
        ▼
   Cloudflare KV (key=收件地址)
        │
        ▼
   HTTP API: GET https://<worker>/?address=foo@yourdomain.com
        → {"code":"123456", ...}
```

## 部署步骤

### 0. 前置条件
- Cloudflare 账号 + 已托管域名（如 yourdomain.com）
- 域名已启用 Email Routing（Dashboard → Email → Email Routing → 开启）
- 已安装 Node.js + 已登录 wrangler（`npm i -g wrangler` 或 npx）

### 1. 创建 KV namespace
```bash
cd D:\PRO\openai-register\cf-temp-mail
npx wrangler kv namespace create CF_TEMP_MAIL
# 输出: { binding: "CF_TEMP_MAIL", id: "xxxx" }
```
把输出的 `id` 填到 `wrangler.toml` 的 `id = "REPLACE_WITH_KV_NAMESPACE_ID"`。

### 2. 配置 Email Routing 规则
Cloudflare Dashboard → Email → Email Routing：
- **Routing rules → Catch-all**：选择 "Send to a Worker" → 选 `cf-temp-mail` 的 Worker
- （可选）也可以在 Custom addresses 里添加具体地址转发到 Worker

> 注意：Worker 的 email 事件需要 Worker 绑定到域名，且域名必须有 Email Routing 接收规则。

### 3. 部署 Worker
```bash
npx wrangler deploy
```

### 4. 验证
```bash
# 给任意地址发一封含 6 位验证码的邮件（如 foo@yourdomain.com）
# 查询：
curl "https://cf-temp-mail.<你的subdomain>.workers.dev/?address=foo@yourdomain.com"
# 或自定义域名路由
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/?address=foo@domain.com` | 查询验证码，返回 JSON |

响应示例：
```json
{
  "to": "foo@yourdomain.com",
  "from": "no-reply@openai.com",
  "code": "123456",
  "receivedAt": 1786800000000,
  "subject": "Your OpenAI verification code",
  "snippet": "...邮件正文前800字符..."
}
```
未收到时：`{"code": null, "received": false}`

## 项目集成（openai-register）

后续把 `cf-temp-mail` 的 API 接入 `backend/app/services/tempmail.py`：
- 注册时：用 API 生成一个随机地址 `xxx@yourdomain.com`（worker 端不需要预创建，catch-all 自动接收）
- 等待邮件：轮询 `GET /?address=xxx@yourdomain.com` 直到 `code` 非空
- 密码：注册流程里设置

## 文件

```
cf-temp-mail/
├─ src/index.js      # Worker 代码（email handler + fetch API）
├─ wrangler.toml     # Worker 配置（KV + email routes）
└─ package.json
```
