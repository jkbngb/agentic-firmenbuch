# `api/` — Signup + playground API (Starlette on Azure Container Apps)

↑ Back to the [root README](../README.md).

The `/api/*` backend for the marketing site (Distribution Spez §4–§7): a **Starlette ASGI app**
(`asgi.py`, run by uvicorn on an Azure Container App). **All decision logic lives in the
unit-tested `fbl_auth` and `fbl_mcp_server` packages** — this folder is only the thin HTTP
adapter. (`function_app.py` is a legacy Azure Functions variant of the same routes, kept for
reference.)

| Route | Method | Purpose |
|---|---|---|
| `/api/signup` | POST | `{email, consent, consent_text_version, turnstile_token}` → pending account + verify mail |
| `/api/verify` | GET | `?token=…` → issue + email the API key, then 302 to `verified.html` / `verify-fehler.html` |
| `/api/regenerate` | POST | `{email}` → re-send a verify link (issues a new key, revokes the old, on verify) |
| `/api/unsubscribe` | POST | `{email}` → revoke key + remove PII (right to deletion) |

## Guards (Distribution §6)
- **Cloudflare Turnstile** server-side verify on signup/regenerate (skipped when `TURNSTILE_SECRET` is unset, e.g. local/dev).
- **Per-IP throttle** (`SIGNUP_IP_LIMIT_PER_MIN`, default 5/min) via a Cosmos counter.
- **Double-opt-in** + disposable-domain screen.
- Secrets store **only hashes** of the verify token and the API key; the key is shown once (in the email).

## Settings (env / `local.settings.json`)
`COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `ACS_CONNECTION_STRING`, `ACS_SENDER_ADDRESS`,
`TURNSTILE_SECRET`, `SITE_BASE_URL`, `VERIFY_TOKEN_TTL_HOURS`, `SIGNUP_IP_LIMIT_PER_MIN`,
`AZURE_CLIENT_ID` (for the user-assigned managed identity). Copy `local.settings.json.sample`
→ `local.settings.json` (gitignored) to run locally with `func start`.

## Run locally
```bash
cp local.settings.json.sample local.settings.json   # fill in values
pip install -r requirements.txt
func start                                            # Azure Functions Core Tools
```

## Deployment (manual — NOT done by the build)
Static Web Apps serves the site (`website/`) and routes `/api/*` to this Functions app.
The reused workspace packages (`fbl_core`, `fbl_auth`) are path-installed via
`requirements.txt`; for an Oryx/SWA build that can't see `../packages`, build wheels first
(`uv build` per package) and point `requirements.txt` at them, or deploy the Functions app
standalone. Deployment + DNS + the ACS mail domain are owner tasks (Distribution §9).
