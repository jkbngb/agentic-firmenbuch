# `auth` (`fbl_auth`) — Stage 9 · `00_accounts`

**Purpose:** MCP account lifecycle — signup → token, validate, rate-limit, meter usage
(§8.10). Tokens are opaque and stored **hashed** (sha256); the plaintext is returned once
for email delivery (via Azure Communication Services) and never persisted.

## API
| Function | What |
|---|---|
| `signup(email, cosmos)` | create account, return `TokenRecord{token, account}` (token shown once) |
| `issue_token()` | generate an opaque URL-safe token |
| `validate(token, cosmos)` | return the active `Account` for a token, else `None` |
| `check_rate_limit(account, per_min, per_day, now)` | pure per-minute + per-day decision |
| `record_usage(account, tool, cosmos, now)` | increment rolling counters + persist |
| `hash_token(token)` | `sha256:<hex>` (storage key) |

`00_accounts` doc: `{id == token_hash, token_hash, email, tier, status, created_at, usage}`.
Tiers/quotas are config (`RATE_LIMIT_PER_MIN`/`RATE_LIMIT_PER_DAY`), so a paid tier is a
config change.

## Run it standalone
```bash
uv run pytest packages/auth
```

## Definition of Done (§8.10) — met
Signup issues a working token; limits enforced (per-minute + per-day, with window reset);
tokens never stored in plaintext. `ruff` + `mypy --strict` + `pytest` green.

## Place in the pipeline
Used by [`mcp_server`](../mcp_server/README.md) to authorize every tool call. Auxiliary to
the data pipeline (not a `90→10` layer).

---
↑ [Repo root](../../README.md) · Specs: [Technische §8.10](../../docs/specs/Technische_Spezifikation.md)
