# Quick ChatGPT AT notes

This workflow was distilled from the project run on 2026-08-18:

- Initial profile-only fetch of `/api/auth/session` returned only a warning banner and guest backend responses.
- Direct ChatGPT login through the email modal plus password and TOTP caused real `chatgpt.com/backend-api/*` requests with `Authorization: Bearer ...`.
- 5 concurrency worked for most accounts; accounts that failed with page navigation races succeeded with single-concurrency retry.
- The extracted AT is a ChatGPT web access token. It is useful for browser/session health and ChatGPT backend calls, but it is not a Codex OAuth refresh token.
