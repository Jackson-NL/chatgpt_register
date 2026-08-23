---
name: quick-chatgpt-at
description: Project-level workflow for quickly extracting ChatGPT web access_token (AT) from accounts in this openai-register repo by reusing existing Camoufox browser profiles, logging in with email/password/TOTP when needed, capturing Bearer tokens from authenticated chatgpt.com backend requests, and writing them back to backend/data/openai_register.db. Use when the user asks to 快速拿 AT, 补 AT, 提取 web AT/session AT, 批量复用 profile 获取 access_token, or rerun AT extraction without Codex OAuth.
---

# Quick ChatGPT AT

## Core idea

Use the project account database plus existing Camoufox `profile_path`. Open `https://chatgpt.com/`; if the profile is guest, close Google One Tap, use the email login modal, submit password and TOTP, then capture the first authenticated `Authorization: Bearer <jwt>` request to `chatgpt.com/backend-api/*`. A home-page URL alone is never proof that the profile is valid: success requires an authenticated backend request from the same persistent profile, and the decoded identity should match the local account when available. Save only the token value to `accounts.access_token`; do not run Codex OAuth and do not require phone/SMS.

## Default command

Run from the repo root:

```powershell
E:\python\python3.13.3\python.exe skills\quick-chatgpt-at\scripts\quick_chatgpt_at.py --concurrency 5
```

Target specific IDs:

```powershell
E:\python\python3.13.3\python.exe skills\quick-chatgpt-at\scripts\quick_chatgpt_at.py --ids 33-62 --concurrency 5
E:\python\python3.13.3\python.exe skills\quick-chatgpt-at\scripts\quick_chatgpt_at.py --ids 44,45 --concurrency 1
```

Useful options:

- `--ids 33-62,70,72`: limit target accounts.
- `--markers imported-2fa-email-20260818 imported-single-20260818`: select accounts by note marker.
- `--concurrency 5`: normal fast batch setting.
- `--concurrency 1`: retry unstable accounts after page-object/navigation races.
- `--headful`: use headed browser for visual debugging.
- `--timeout 110`: raise per-account login/capture timeout.

## Workflow

1. Inspect DB count first if needed:
   ```powershell
   E:\python\python3.13.3\python.exe - <<'PY'
   import sqlite3, json
   con=sqlite3.connect('backend/data/openai_register.db'); con.row_factory=sqlite3.Row
   rows=con.execute("select id,email,length(access_token) at_len,profile_path from accounts order by id").fetchall()
   print(json.dumps({'total':len(rows),'with_at':sum(1 for r in rows if r['at_len']),'missing':[r['id'] for r in rows if not r['at_len']]}, ensure_ascii=False))
   PY
   ```
2. Run 5 concurrency for missing AT accounts.
3. If only a few accounts fail with transient browser errors such as `object ... no longer usable`, rerun only those IDs with `--concurrency 1`.
4. Verify with the summary JSON and a DB length query. Never print full AT values.

## Expected result

The script writes:

- `accounts.access_token`
- best-effort `accounts.user_id`
- best-effort `accounts.account_id`
- best-effort `accounts.plan_type`

It does not write `refresh_token`; this is web AT extraction, not Codex OAuth.

## Failure handling

- **Guest page / `/api/auth/session` only warning banner**: normal before login. Continue with email/password/TOTP login.
- **Google One Tap steals Continue click**: close top-right One Tap first; the script does this before clicking the modal email Continue.
- **`Page.evaluate: object ... no longer usable`**: retry the failed IDs with `--concurrency 1`; it is usually a navigation race.
- **Wrong password/TOTP**: check account password and `totp_secret` in DB, then rerun that ID.
- **Profile locked / parent.lock**: another browser is using the same profile; stop that run or retry later.
- **Still no token but ChatGPT is logged in**: wait longer with `--timeout 160`; authenticated backend requests may appear after hydration.

## Artifacts

Each run creates a timestamped directory under `output/quick-chatgpt-at/` containing:

- `run_stdout.log` if caller tees output
- `summary.json`
- `acc_<id>.log`
- `acc_<id>.png` only for failures

Keep logs token-safe: the script records token lengths, source, exp, and email claim, not full tokens.
