---
name: codex-oauth-reauthorization
description: Use when an account needs Codex OAuth reauthorization, refresh_token/id_token recovery, profile-based login recovery, OAuth MFA handling, add-phone fallback, or Sub2API error-account re-login in this openai-register project.
---

# Codex OAuth Reauthorization

## Purpose

This skill describes the project's complete reauthorization chain. It is for recovering Codex OAuth credentials from an existing account and persistent browser profile. It is not the registration flow and it is not the web-only access-token extraction flow.

The successful result is an OAuth callback code exchanged for fresh OAuth credentials, followed by a database or Sub2API write. A page reaching `chatgpt.com/` or a web `access_token` alone is not proof that Codex OAuth succeeded.

## Boundaries

Use this skill for:

- missing or invalid `refresh_token` / `id_token`;
- accounts marked with OAuth refresh `401`, `refresh_token_invalidated`, or `refresh_token_reused`;
- direct Codex OAuth from the Accounts page;
- OAuth login recovery that requires email, password, or the account's TOTP;
- an OAuth `add-phone` page and the controlled SMS fallback;
- Sub2API remote error-account re-login.

Do not use this skill for:

- creating a new account or running the Registration Workbench;
- extracting only ChatGPT web `access_token` (use `skills/quick-chatgpt-at`);
- repairing a web profile without Codex OAuth (use `skills/repair-chatgpt-profiles`).

## Non-Negotiable Invariants

1. Reuse the account's own persistent `profile_path`. Do not use another account's profile and do not replace an original profile before success.
2. The local account record must provide the email, password, and TOTP secret when login or MFA may be required. A missing TOTP secret is a hard failure for an MFA challenge, not a reason to skip MFA.
3. `refresh_token` and `id_token` are the OAuth result. A successful web session or `access_token` does not complete reauthorization.
4. Generate a fresh PKCE verifier/challenge and random state for every OAuth authorization attempt. The callback state must match exactly before exchanging the code.
5. Use the project callback listener at `http://localhost:1455/auth/callback`; do not accept an arbitrary callback or a callback with a mismatched state.
6. Only write credentials after callback capture, state validation, token exchange, and identity parsing succeed. A failed attempt must not be recorded as OAuth success.
7. Keep full tokens, passwords, TOTP secrets, SMS codes, cookies, and authorization URLs with codes out of chat summaries and reports. Report booleans, lengths, account IDs, stages, and sanitized errors.
8. Do not mix registration and reauthorization. Registration may save a web session first, while this skill separately obtains Codex OAuth credentials.

## Runtime And Preflight

Run from `D:\PRO\openai-register` with the project's runtime:

```powershell
$py = 'E:\python\python3.13.3\python.exe'
& $py -c "import camoufox, playwright, pyotp, sqlalchemy; print('dependencies: ok')"
```

Check that another job is not using the same profiles:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'oauth|registration|relogin|camoufox' } |
  Select-Object ProcessId, Name, CommandLine
```

For the UI worker, check the backend job before starting another batch:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/accounts/oauth/jobs/active
```

A returned job with `status` in `pending`, `running`, or `stopping` is active. Do not start a second Codex OAuth batch. Use the existing cancel endpoint and wait until browser cleanup finishes.

Inspect candidates without printing secrets:

```powershell
$code = @'
import json, sqlite3
con = sqlite3.connect("backend/data/openai_register.db")
rows = con.execute("""
select id, email, status, profile_path,
       case when trim(coalesce(access_token,'')) <> '' then 1 else 0 end has_at,
       case when trim(coalesce(refresh_token,'')) <> '' then 1 else 0 end has_rt,
       case when trim(coalesce(id_token,'')) <> '' then 1 else 0 end has_id,
       case when trim(coalesce(totp_secret,'')) <> '' then 1 else 0 end has_totp
from accounts order by id
""").fetchall()
print(json.dumps([dict(zip(("id","email","status","profile_path","has_at","has_rt","has_id","has_totp"), r)) for r in rows], ensure_ascii=False))
con.close()
'@
& $py -c $code
```

Required preflight for direct OAuth: `profile_path` exists, the profile is not locked by another browser, and the account has the credentials needed for possible login recovery. If the target needs MFA and has no `totp_secret`, stop and report it instead of attempting a blind flow.

## Choose The Correct Mode

```text
Need fresh Codex RT/ID from an existing local account?
  -> Direct Codex OAuth

Direct OAuth reaches add-phone?
  -> allow_phone_fallback=false: fail with a phone-required result
  -> allow_phone_fallback=true: same-browser SMS phone fallback

The target is a remote Sub2API account in an error state?
  -> Sub2API re-login flow
```

### Mode A: Direct Codex OAuth

Use this first for accounts with an existing profile. It does not rent a phone unless `allow_phone_fallback` is enabled.

Single-account endpoint:

```http
POST /api/accounts/{account_id}/oauth/refresh-from-profile
Content-Type: application/json

{"headless": true}
```

Batch endpoint:

```http
POST /api/accounts/oauth/jobs
Content-Type: application/json

{
  "account_ids": [110, 163],
  "headless": true,
  "concurrency": 5,
  "allow_phone_fallback": false,
  "countries": ["PH", "ID", "GB"],
  "max_price": 0.03,
  "low_price_first": true,
  "max_phone_attempts": 0,
  "sms_poll_timeout": 60,
  "sms_poll_interval": 4
}
```

The batch endpoint requires non-empty `account_ids`. When the UI has no explicit selection, resolve candidates locally first: accounts with a real profile and no `refresh_token`. Do not send an empty list and do not silently re-run already successful accounts.

Poll the job and logs:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/accounts/oauth/jobs/active
Invoke-RestMethod http://127.0.0.1:8000/api/accounts/oauth/jobs/<job_id>
Invoke-RestMethod 'http://127.0.0.1:8000/api/accounts/oauth/logs?after=0&limit=300'
```

Stop only the active job:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/accounts/oauth/jobs/<job_id>/cancel"
```

The configured batch concurrency is clamped to `1..10`. It controls browser tasks; it does not mean a single account opens multiple browsers.

### Direct OAuth Browser Chain

The implementation is in `backend/app/services/registrator.py`:

1. Load the persistent Camoufox profile through `build_launch_options`.
2. Generate PKCE and a random state; call the configured OpenAI authorize endpoint.
3. Start `OAuthCallbackListener` on port `1455` before opening the authorize URL.
4. Navigate to the authorize URL and wait for SPA hydration.
5. If the profile is redirected to `auth.openai.com/log-in`, recover the same session in the same browser with the local email and password.
6. If the login or OAuth page displays `Check your authenticator app`, generate the current TOTP from that account's `totp_secret`, fill the code, and submit it. Missing or invalid TOTP is a hard failure.
7. Click only normal OAuth actions such as `Select account`, `Continue`, `Authorize`, `Allow`, or `Confirm`. Never click generic `Continue` on `add-phone` as a substitute for phone verification.
8. Capture the callback URL from page navigation, requests, or responses. Require the expected `state` and extract the authorization `code` without logging it.
9. Exchange the code with the same PKCE verifier and redirect URI.
10. Parse the returned identity from `id_token` and confirm that the exchange contains the expected OAuth credential fields.
11. Write `access_token`, `refresh_token`, `id_token`, account/user identity, plan, and refresh status through `_write_oauth_tokens`.

The normal stages shown by the job are `profile -> open -> select -> exchange -> write -> done`. A successful write clears stale `oauth_refresh_status=failed` and `oauth_refresh_error`.

### Network Retry

`OAuthProxyNetworkError` / connection-reset / proxy-tunnel / timeout failures are transport failures, not proof that the account is invalid. `oauth_from_profile` allows two network retries, rotates the configured Clash node when possible, and recreates the browser context with the same profile. Do not change credentials or rent a phone solely because of a transport error.

If node rotation fails, the implementation may retry the current proxy once. Record the actual error and stop after the configured retry limit; do not create an unbounded network loop.

### Mode B: Same-Browser Phone Fallback

Use phone fallback only after direct OAuth reports `add-phone` / phone verification. It is not a replacement for the direct path.

The batch job switches to `oauth_from_profile_with_phone_attempts`, which keeps one browser/profile session open while it:

1. Recovers the profile login first, then prefetches the next SMS rental.
2. Uses the configured country list in order and never exceeds `max_price`.
3. Uses `low_price_first=true` when the user explicitly wants the cheapest eligible provider first.
4. Fills the current `add-phone` form, submits the number, polls the matching activation ID, fills the SMS code, and submits it.
5. Marks a successful SMS order as completed and waits for the OAuth callback.
6. On a number-level failure, classifies the reason, cancels the order when appropriate, returns to the same `add-phone` page, and continues with the next number in the same browser session.

`max_phone_attempts=0` means keep replacing numbers until success or an external stop/error. A positive value is a hard upper bound. The SMS order, phone, country, provider, and price must remain correlated in logs and in the attempt record.

Important failure distinctions:

- `invalid_auth_step` or an OpenAI authorization-step rejection: classify as OpenAI risk and replace the number; do not take a screenshot for this known class.
- `couldn't send a text message` / switched to WhatsApp: classify as phone/provider risk and replace the number according to the current implementation.
- SMS cancel is not confirmed: the implementation may poll the old activation for up to 30 seconds; if a code arrives, submit it before forcing a new number.
- No eligible number under the price cap: keep waiting only when unlimited mode is explicitly enabled; otherwise fail the account attempt.
- A network error: rotate/retry the proxy path, not the account credentials.

Single-account phone endpoints are available when a number is already rented:

```http
POST /api/accounts/{account_id}/oauth/complete-phone-from-profile
POST /api/accounts/{account_id}/oauth/auto-phone-from-profile
POST /api/accounts/{account_id}/oauth/dry-run-phone-from-profile
```

The dry-run must not rent, submit, or write tokens. The complete endpoint requires the existing `activation_id`, phone, country ISO, and dialing code. The auto endpoint performs the project SMSBower rental loop.

## Mode C: Sub2API Re-login

Use this only for remote Sub2API accounts in the selected error groups. The service is `backend/app/services/sub2api_relogin.py`; the local page is `Sub2API 重登`.

Preview the remote error set first:

```http
GET /api/sub2api/relogin/preview?group_ids=1,2&only_error=true
```

Create a job only after reviewing `remote_total`, `error_total`, `matched_local`, `missing_local`, and `runnable`:

```http
POST /api/sub2api/relogin/jobs
Content-Type: application/json

{
  "group_ids": [1, 2],
  "only_error": true,
  "headless": true,
  "concurrency": 3,
  "timeout_s": 160,
  "retry_reauth_url": 2,
  "delete_deactivated": false
}
```

Per-item chain:

1. Request the remote reauth URL and session data.
2. Validate the callback state in the remote URL against the returned state.
3. Copy the local profile into the protected `sub2api_relogin_tmp` directory.
4. Open the copy with the local email, password, and TOTP in the configured proxy.
5. Fill login/MFA pages and capture the Sub2API OAuth callback.
6. Reject phone verification and deactivated/terminal remote states as explicit `skipped` outcomes, not successful reauth.
7. Exchange the callback code with the remote Sub2API session.
8. Require returned OAuth credentials, apply them to the matching remote account, clear the remote error, and restore scheduling.
9. Commit the temporary profile over the original profile only after all remote writes succeed.
10. On failure, discard the temporary profile and retry only within `retry_reauth_url`.

The re-login job can run remote preparation concurrently, but the local OAuth callback/browser critical section is protected by `_CALLBACK_LOCK`. Do not infer that `concurrency=5` means five browsers can safely mutate the same profile. Never reuse a profile copy from a failed attempt.

Job endpoints:

```http
GET  /api/sub2api/relogin/jobs
GET  /api/sub2api/relogin/jobs/{job_id}
GET  /api/sub2api/relogin/jobs/{job_id}/items
GET  /api/sub2api/relogin/jobs/{job_id}/logs?after=0
POST /api/sub2api/relogin/jobs/{job_id}/cancel
```

## Persistence Rules

For local Codex OAuth, `_write_oauth_tokens` updates the account only after the token exchange. Verify these fields using booleans, not values:

```sql
select id, email, oauth_refresh_status,
       length(coalesce(access_token,'')) > 0 as has_at,
       length(coalesce(refresh_token,'')) > 0 as has_rt,
       length(coalesce(id_token,'')) > 0 as has_id,
       length(coalesce(totp_secret,'')) > 0 as has_totp,
       profile_path
from accounts where id in (...);
```

Expected direct-success state:

- `has_rt=1` and `has_id=1`;
- `oauth_refresh_status=success`;
- `oauth_refresh_error` is empty;
- `profile_path` still points to the original existing profile;
- returned identity matches the account when both identities are available.

For Sub2API success, also require the item status `success`, remote error cleared, scheduling restored, and no leftover temporary profile copies. A failed or skipped item must not commit its temporary profile.

## Failure Classification

| Symptom | Meaning | Action |
|---|---|---|
| No `profile_path` | Cannot reproduce the account session | Stop; repair/import the profile first |
| Login page + no email/password | Local credentials are incomplete | Stop; do not guess credentials |
| `Check your authenticator app` | Account MFA is required | Use that account's TOTP; missing TOTP is a hard failure |
| `add-phone` | Direct OAuth requires phone verification | Use phone fallback only when explicitly enabled |
| `401 refresh_token_invalidated` | Old RT is unusable | Clear only the old RT if requested, then rerun OAuth |
| `refresh_token_reused` | Old RT was replayed/rotated | Treat old RT as unusable and obtain a fresh OAuth result |
| `state 不一致` | Callback may belong to another attempt | Reject; do not exchange the code |
| Callback timeout | Browser did not complete OAuth | Inspect stage/url/network, then bounded retry |
| Proxy reset/timeout | Transport failure | Rotate node and retry within the limit |
| Missing remote credentials | Sub2API exchange failed | Discard temporary profile and mark the item failed |
| Deactivated/phone-required Sub2API page | This path cannot finish automatically | Mark skipped with an explicit reason |

## Common Mistakes

- Running `quick-chatgpt-at` and assuming its web AT is a Codex refresh token.
- Starting OAuth with an account that has no profile and expecting a fresh browser to inherit its session.
- Using a profile from another account because the email looks similar.
- Treating `chatgpt.com/` or `access_token=yes` as OAuth success without checking RT/ID.
- Clicking generic `Continue` on `add-phone` and classifying the result as a callback failure.
- Renting a phone before the profile login is restored, causing an expired SMS order.
- Reusing the same activation ID after a confirmed cancellation.
- Replacing the original Sub2API profile before remote credential application succeeds.
- Retrying the whole batch instead of retrying only failed IDs.
- Dumping full token/password/TOTP values into a ticket, screenshot, or final report.

## Implementation Map

- Local OAuth browser and callback: `backend/app/services/registrator.py`
  - `Registrator.oauth_from_profile`
  - `Registrator.oauth_from_page`
  - `Registrator._recover_oauth_login`
  - `Registrator._handle_oauth_mfa_challenge`
  - `Registrator._capture_oauth_code_on_page`
  - `Registrator.oauth_from_profile_with_phone_attempts`
- Local OAuth API/job and database write: `backend/app/api/accounts.py`
  - `POST /api/accounts/oauth/jobs`
  - `_run_codex_oauth_target`
  - `_write_oauth_tokens`
  - single-account OAuth endpoints
- Sub2API remote re-login: `backend/app/services/sub2api_relogin.py`
- Browser launch/profile rules: `backend/app/services/browser_stack.py`
- Account model and credential fields: `backend/app/models.py`
- Existing web-only AT skill: `skills/quick-chatgpt-at/SKILL.md`
- Existing web-profile repair skill: `skills/repair-chatgpt-profiles/SKILL.md`

## Completion Checklist

Before reporting reauthorization complete:

- [ ] The target IDs and reason for reauthorization were recorded.
- [ ] The correct existing profile was used and remained intact unless a Sub2API success committed a verified copy.
- [ ] Direct OAuth reached callback capture and state validation.
- [ ] Login/MFA was handled with the account's own credentials when required.
- [ ] The result contains a non-empty RT and ID, not only a web AT.
- [ ] Local OAuth status is `success` with no stale error.
- [ ] Phone attempts, rental outcomes, and failures are correlated by account and activation ID when phone fallback was used.
- [ ] Sub2API success cleared the remote error and restored scheduling.
- [ ] Failed/skipped attempts did not leave temporary profiles or overwrite originals.
- [ ] Logs and the final report do not expose full secrets.
