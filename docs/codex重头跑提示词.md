# 任务：重头跑通 openai-register 的 SMSBower Gmail 模式有头注册

## 项目位置
`D:\PRO\openai-register`

## 项目是什么
OpenAI/ChatGPT 账号批量注册机。用真实浏览器（Camoufox + Playwright）模拟人工注册流程，自动完成：
租临时 Gmail（SMSBower Mail API）→ 生成别名 → 浏览器自动注册 → SMSBower 收验证码 → OAuth 授权拿 access_token → 落库。

本次任务：**用 SMSBower Gmail 模式 + 有头浏览器（headless=false）完整跑通一次注册**，并顺手解决已知问题。

## 技术栈与环境（重要！）
- 后端：Python 3.13 + FastAPI 0.138 + SQLAlchemy 2.0 + SQLite
- 浏览器：Camoufox 0.4.11 + Playwright 1.53（Firefox 内核，指纹伪装）
- 前端：React 18 + Vite 5（本次任务可不开前端，纯 API 即可）
- 接码：SMSBower 平台（短信 + Mail API 两种）
- 临时邮箱：Cloudflare Workers + Email Routing

**⚠️ 运行 Python 必须用 `E:\python\python3.13.3\python.exe`**
（PATH 上的 python 是 3.11 conda 环境，缺依赖，会报错）

## 启动方式（三件套）
1. 后端（后台运行）：
   `cd D:\PRO\openai-register\backend`
   `E:\python\python3.13.3\python.exe -m uvicorn app.main:app --port 8000`
2. 前端（本次可不启）：
   `cd D:\PRO\openai-register\frontend && npm run dev`（5173，/api proxy → 8000）
3. 触发注册：`POST http://127.0.0.1:8000/api/registrations`
   body: `{"proxy":"http://127.0.0.1:7890","headless":false,"bind_totp":false,"gmail_alias":"...","gmail_mail_id":"..."}`
   后台任务跑 registrator，结果落 Account 表。

## 代码结构
- `backend/app/services/registrator.py` — 核心注册引擎（Camoufox 自动化全流程）
- `backend/app/services/registrations.py` — 注册任务管理器（异步队列、重试）
- `backend/app/services/smsbower_mail.py` — SMSBower Mail API 客户端（租 Gmail、收码）
- `backend/app/api/gmail_sessions.py` — Gmail 会话管理（rent / next-alias / release）
- `backend/app/api/registrations.py` — 注册任务 API
- `backend/app/models.py` — 数据模型（Account / Registration / GmailSession）
- `frontend/src/pages/Register.jsx` — 前端注册页（Gmail 模式交互）

## 已完成的优化（不要回退）
1. **缩短等待时间**：registrator.py 中 human-like 延迟已缩短
   （wait_spa_ready networkidle 45s→15s、随机停顿 3-6s→1-2s、human_pause 4-12s→1-2s、Continue with password 后 6-9s→2-4s、设密码后 8-12s→3-5s、about-you 20s→15s、chatgpt_home 40s→25s、session 提取前 5s→2s）
2. **Gmail 复用修复**：`gmail_sessions.py` 的 `/next-alias` 已改为 async，每次重新调用 `SmsbowerMailClient.get_activation()` 获取**新 mail_id**（旧 activation 无法接收发往新别名的邮件）
3. **前端别名**：Register.jsx 已移除"启用 Gmail 模式自动生成别名"的 useEffect，只在用户点"开始注册"或"新别名"时调用 next-alias
4. **重试换新邮箱**：registrator.py 的 `register_by_email` 重试逻辑已加 `_new_gmail_activation()` helper —— Gmail 模式重试时从 SMSBower 获取全新邮箱 + mail_id（避免复用旧 alias 导致 OpenAI 走登录流程而非注册流程），获取失败保留旧值
5. **poll_code 超时**：Gmail 模式 120s / 间隔 3s；临时邮箱轮询 120s
6. **修复双窗口 bug**：`_register_by_email_once` 中 persistent_context 模式（有 profile_path）下 Camoufox 自带默认页，原来又 new_page() 导致开两个窗口；已改为复用 `context.pages[0]`

## 已知问题（重点！本次要解决）
### 问题 A：验证码超时误判（最严重，用户已被坑过）
- **现象**：第一次注册时，SMSBower 的验证码邮件**其实已经到达**，但 `poll_code` 在 120s 内没从 SMSBower API 查到码 → 抛 `VerificationTimeoutError` → 触发重试逻辑换新邮箱 → 浏览器被关闭，**已到达的验证码被浪费，流程被打断**。
- **用户原话**："验证码都来了你重启干什么啊"
- **排查方向**：
  1. `smsbower_mail.py` 的 `get_code()` 对 SMSBower `getCode` API 的响应解析是否正确（status 字段、code 字段、错误信息判定）
  2. SMSBower getCode 的轮询语义：码到达后是否要等一会儿才可查？有没有缓存/延迟？
  3. `poll_code` 超时判定太武断 —— 超时后应先**再查一次旧 activation 是否已有码**，有就不换邮箱
  4. 可以考虑：超时先不判死，延长到 180s；或重试前先查旧 mail_id
- **不要做的事**：验证码可能已到达时，不要立刻杀浏览器/重启/换邮箱

### 问题 B：注册失败时密码丢失
- 密码由 `gen_password()` 生成，只存内存（`_register_by_email_once` 局部变量），**注册成功才落库**（accounts.password / result_json）
- 注册中断（超时/浏览器关闭）后密码丢失，即使 OpenAI 那边已设过密码，本地也不知道
- **建议**：任务开始时就把生成密码先写进 registrations 表（新加字段或 result_json 草稿），中断也能找回

### 问题 C：重试同邮箱会被 OpenAI 记住
- 第一次提交邮箱后 OpenAI 记住该邮箱 → 重试**同一个 alias** 会跳登录页（`log-in/password`）而不是注册设密码页（`create-account/password`），报 `WrongPhaseError: 预期[set_password]实际[login_password]`
- 已有 `_new_gmail_activation()` 缓解（重试换新邮箱），但要确认它正确生效

## 本次任务步骤
1. 启动后端（见上）
2. 调 API：
   - `POST /api/gmail-sessions/rent` 租一个全新 Gmail（记录 base_email + mail_id）
   - `POST /api/gmail-sessions/next-alias` 生成第一个 alias（会返回新 mail_id）
   - `POST /api/registrations` 提交注册（headless=false 有头模式、gmail_alias、gmail_mail_id、proxy=http://127.0.0.1:7890、bind_totp=false）
3. 监控：`GET /api/registrations/{id}`（状态）+ `GET /api/registrations/{id}/logs`（实时日志）
4. 全程**不要**在验证码未确认超时前杀浏览器/重启
5. 若验证码超时：先手动/脚本查 SMSBower 该 mail_id 是否有码，确认真的没有再判断是否重试
6. 注册成功标准：registrations.status = success，accounts 表出现该邮箱记录（含 password / access_token / totp_secret 视配置）
7. 顺手修复问题 A（验证码超时误判）和问题 B（密码落库）—— 改完代码重启后端再验证

## 注意事项
- 不要用 PATH 上的 python（3.11 conda），必须 `E:\python\python3.13.3\python.exe`
- 代理 `http://127.0.0.1:7890`（本机代理，需先确保代理在跑，出口地区会被 registrator 探测并用于指纹匹配）
- 每个 alias 是一次性：SMSBower 的 activation（mail_id）只能收一封验证码邮件，用完即废
- SMSBower API key 在 `backend/.env` 的 `SMSBOWER_API_KEY`
- 注册日志中文是 UTF-8，PowerShell 直接看会乱码，用 Python 脚本读或设置编码
