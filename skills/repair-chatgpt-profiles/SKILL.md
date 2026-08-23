---
name: repair-chatgpt-profiles
description: "Repair ChatGPT web-login browser Profiles in this openai-register repo by finding accounts with access_token present and refresh_token missing, re-authenticating only through chatgpt.com with copied Camoufox profiles and local email/password/TOTP, and committing successful copies back. Use when the user asks to \u8865 Profile, \u4fee\u590d Profile, \u68c0\u67e5 AT \u6709 RT \u65e0\u8d26\u53f7, \u6279\u91cf\u6062\u590d\u767b\u5f55\u6001, or rerun web-profile login; do not use for Codex OAuth."
---

# Repair ChatGPT Profiles

## Overview

Use the repository's `backend/scripts/relogin_web_profiles.py` to restore local ChatGPT web login state without touching Codex OAuth state. The script works on temporary copies, uses exactly three concurrent browser workers, and replaces an original Profile only after successful web login verification.

## Preconditions

Run commands from `D:\PRO\openai-register` and use the project's Python runtime:

```powershell
$py = 'E:\python\python3.13.3\python.exe'
```

Before a run:

- Confirm `backend/data/openai_register.db` exists and the configured Profile root exists.
- Confirm the registration worker and Codex OAuth worker are stopped, or that none of their browsers can use the target Profiles. Do not open an original Profile while another process may be using it.
- Confirm the required Python packages and Camoufox runtime are available. If the import check fails, fix the project's environment first; do not switch to a system Python with different dependencies.
- Keep the default headless mode. Use `--headed` only for visual debugging and only after confirming no conflicting browser is active.

Process check:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'oauth|registration|relogin_web_profiles|camoufox' } |
  Select-Object ProcessId, Name, CommandLine
```

Dependency check:

```powershell
& $py -c "import camoufox, playwright, pyotp, sqlalchemy; print('dependencies: ok')"
```

If a registration or OAuth process is active, stop the repair before launching browsers and report the conflicting process. Do not kill unrelated processes automatically.

## Target Selection

The default target is every account whose `access_token` is non-empty and `refresh_token` is empty. Do not print token values, passwords, TOTP secrets, or cookies.

Use a token-safe preflight to see only IDs and counts:

```powershell
$code = @'
import json
import sqlite3

con = sqlite3.connect("backend/data/openai_register.db")
rows = con.execute(
    "select id from accounts where trim(coalesce(access_token, '')) <> '' "
    "and trim(coalesce(refresh_token, '')) = '' order by id"
).fetchall()
print(json.dumps({"count": len(rows), "account_ids": [row[0] for row in rows]}))
'@
& $py -c $code
```

For a bounded repair, use `--account-ids 142,143` or an ID range. For the full database, use `--min-id 0 --max-id 2147483647` together with `--only-missing-refresh`.

## Repair Workflow

1. Run the existing script with exactly three workers:

   ```powershell
   $result = "backend/output/relogin_missing_refresh_$(Get-Date -Format yyyyMMdd_HHmmss).json"
   & $py -u backend/scripts/relogin_web_profiles.py `
     --min-id 0 `
     --max-id 2147483647 `
     --concurrency 3 `
     --timeout 120 `
     --only-missing-refresh `
     --result $result
   $exitCode = $LASTEXITCODE
   ```

2. Read the summary JSON and report only `count`, `success`, `failed`, target IDs, stages, and sanitized errors. Exit code `0` means all selected accounts succeeded; exit code `2` means at least one account failed and needs inspection. Record the successful `account_id` values from each result file because this script does not populate `refresh_token`.

3. Retry only failed IDs, still with three workers. Use `--account-ids` and keep `--only-missing-refresh` as a guard against stale account state. Do not put successful IDs in the retry list:

   ```powershell
   & $py -u backend/scripts/relogin_web_profiles.py `
     --account-ids 142,143 `
     --concurrency 3 `
     --timeout 160 `
     --only-missing-refresh `
     --result backend/output/relogin_missing_refresh_retry.json
   ```

4. If the registration worker created new accounts during the run, repeat the token-safe preflight, subtract IDs already recorded as successful, and run only the newly discovered IDs. The AT-only query will continue to match repaired accounts because the script intentionally does not write `refresh_token`; completion is therefore based on the result ledger plus the absence of unprocessed new IDs, not on that query returning zero.

5. Treat a failed browser attempt as non-destructive: the script must remove only its temporary copy and leave the original Profile untouched. Never manually delete or replace an original Profile to force progress.

## Login and Safety Contract

- Navigate only to `https://chatgpt.com/auth/login` and the normal ChatGPT web login flow.
- Use the account's local email, password, and TOTP secret through the existing script. Do not request SMS and do not invoke Codex OAuth, `auth.openai.com`, OAuth callbacks, or token exchange endpoints.
- Each source Profile is copied under `backend/profiles/web_relogin_tmp/account_<id>_<uuid>` before launch. Browser lock files are removed from the copy only.
- Commit the temporary Profile only after all of these checks pass: the page is on the normal ChatGPT app, the same persistent Profile's cookies return HTTP 200 from `/backend-api/me`, the response contains an authenticated identity, and the returned email matches the local account email when both are available. A page URL such as `https://chatgpt.com/` by itself is not proof of login. A Cloudflare challenge, OAuth redirect, disabled account, timeout, failed form submission, HTTP 401, or missing identity is a failure, not permission to replace the source Profile.
- Keep concurrency fixed at `3`; the script rejects other values because Profile and browser state are sensitive to concurrent access.
- Use the account's configured proxy through `build_launch_options`; do not silently bypass the configured proxy.

## Verification

Inspect the result file without exposing secrets, then verify target Profile structure. This file check is only a preliminary integrity check; it cannot prove that the browser session is authenticated:

```powershell
$code = @'
import json
import sqlite3
from pathlib import Path

con = sqlite3.connect("backend/data/openai_register.db")
rows = con.execute(
    "select id, profile_path from accounts "
    "where trim(coalesce(access_token, '')) <> '' "
    "and trim(coalesce(refresh_token, '')) = '' order by id"
).fetchall()
required = ("cookies.sqlite", "storage.sqlite", "places.sqlite")
bad = []
for account_id, profile in rows:
    path = Path(profile).expanduser() if profile else None
    if not path or not path.is_dir() or any(not (path / name).exists() for name in required):
        bad.append(account_id)
print(json.dumps({"target_count": len(rows), "profile_incomplete_ids": bad}))
'@
& $py -c $code
```

Also check that `backend/profiles/web_relogin_tmp` is empty after the run. A non-empty directory indicates interrupted cleanup and must be investigated before another repair. Do not claim completion while result failures, incomplete Profiles, or temporary copies remain unexplained.

For every reported success, the runtime result must also include evidence equivalent to:

- `profile_session_status=200`
- `profile_session_authenticated=true`
- `profile_session_email` matching the local account email when the endpoint returns one

If any of these fields is absent, treat the result as unverified and do not commit the copied Profile.
