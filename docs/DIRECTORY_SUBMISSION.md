# Anthropic Connectors Directory — submission packet (Firmenbuch)

Everything needed to fill out <https://clau.de/mcp-directory-submission> in one place.
Copy the fields below verbatim. Last updated: 2026-07-09.

> **Goal:** get "**Firmenbuch**" listed so typing the word in Claude renders a clickable
> connector chip (like "Salesforce"). The chip's name + logo come 1:1 from this listing.

---

## 1. Core identity (paste into the form)

| Field | Value |
|---|---|
| **Listing / display name** | `Firmenbuch` |
| **Server URL (Streamable HTTP)** | `https://mcp.agentic-firmenbuch.at/mcp` |
| **Website** | `https://www.agentic-firmenbuch.at` |
| **Category** | Business data / Company & financial data |
| **Support email** | `office@jngb.online` |
| **Publisher / owner** | Jakob Neugebauer, Postgasse 8b, 1010 Wien, Österreich |
| **Privacy policy** | `https://www.agentic-firmenbuch.at/datenschutz.en.html` |
| **Terms of use** | `https://www.agentic-firmenbuch.at/nutzungsbedingungen.html` |
| **Documentation** | `https://www.agentic-firmenbuch.at/onboarding.en.html` |
| **Logo (512×512 PNG)** | `https://mcp.agentic-firmenbuch.at/icon.png` (also `website/icon.png`) |
| **Favicon (SVG)** | `https://mcp.agentic-firmenbuch.at/favicon.svg` |

### Tagline (≤ ~60 chars)
> Austria's official company register, for AI agents.

### Short description (1–2 sentences)
> Query the Austrian Firmenbuch — official master data, balance sheets, financial ratios and
> live register changes for every registered company — directly from Claude. Read-only.

### Long description
> Firmenbuch gives Claude live, structured access to Austria's official company register
> (Firmenbuch) and the annual financial statements filed with it. Search and rank companies by
> name, location, industry (ÖNACE), legal form, size class or financials; pull a full company
> profile with per-year balance sheet, P&L and computed ratios; compare a company against its
> size peers; run aggregate cohort statistics; and watch the daily change feed for management
> changes, capital increases, relocations and more. The data is served from an automated,
> deterministic ETL pipeline over the EU High Value Datasets — no LLM in the data path — so
> figures are traceable to the original filing. Every tool is read-only. Ideal for sales
> prospecting, due diligence, market research and competitive monitoring in the DACH region.

---

## 2. Example prompts (the 3 required — English)

1. **Search / filter** — "Find active Austrian GmbHs in Upper Austria with a balance sheet total
   over €5 million, sorted by revenue."
2. **Company profile / history** — "Show me the balance-sheet and key-figure development of
   *[company name]* over the last few years."
3. **Peers / cohort** — "Compare *[company name]* with its peers in the same size class and show
   how it ranks on equity ratio."

*(German equivalents already live on the landing page for the localized listing, if the form
allows a second locale.)*

---

## 3. Tools (13, all read-only — for the "tool listing" section)

Every tool declares `readOnlyHint: true` and a human-readable `title` (verified by the regression
test `test_every_tool_declares_title_and_readonly_hint`). No write/destructive tools; no catch-all
`method`/`action` dispatcher.

| Tool (`snake_case`) | Title | What it does |
|---|---|---|
| `search_companies` | Search Austrian companies | Find/rank companies by name or filters (start here) |
| `get_company_details` | Company profile | Full curated profile for one company by FNR |
| `get_full_record` | Full company record | Complete superset record (heaviest read) |
| `get_company_history` | Company financial history | Per-metric multi-year time series |
| `find_peers` | Find peer companies | Nearest companies by size class |
| `get_cohort_summary` | Cohort statistics | Aggregate stats for a group (state / size / legal form) |
| `list_events` | Register change feed | Cross-company feed of register changes |
| `get_event_stats` | Register change statistics | Aggregate counts of register changes |
| `get_coverage` | Dataset coverage | How much of the register is served |
| `list_sectors` | List sectors & legal forms | Valid filter values with counts |
| `describe_fields` | Describe available fields | Field/schema catalog |
| `get_document` | Download annual filing | Time-limited link to the official Jahresabschluss |
| `get_my_usage` | My API usage | The caller's own consumption vs. rate limits |

---

## 4. Authentication (for the auth section of the form)

- **Primary: OAuth 2.1 + PKCE (S256).** Dynamic Client Registration (RFC 7591) at `/register`.
  Discovery documents served at the host root:
  - RFC 9728 protected-resource metadata: `/.well-known/oauth-protected-resource`
  - RFC 8414 authorization-server metadata: `/.well-known/oauth-authorization-server`
  - Redirect URI `https://claude.ai/api/mcp/auth_callback` is accepted via DCR (no allowlist).
  - Refresh-token grant supported; expired/revoked tokens return `401` + `WWW-Authenticate`.
- **401 contract:** every unauthenticated `/mcp` request — **including the first `initialize`** —
  returns `401` with `WWW-Authenticate: Bearer resource_metadata="…"`. (Enforced by default;
  the `MCP_ANONYMOUS_DISCOVERY` flag is **false** in production. Only flip it if a registry
  health check needs anonymous preview — it makes the reviewer probe get 200 instead of 401.)
- **Secondary: `X-API-Key` header** (for Claude Code / Cursor / Copilot). Optional; does not
  interfere with the OAuth 401 contract for keyless clients.

---

## 5. Reviewer test access (give this to Anthropic)

The Anthropic reviewer should connect as a **custom connector** with the OAuth (email) flow —
no pre-shared key needed:

1. In Claude → Settings → Connectors → add custom connector.
2. URL: `https://mcp.agentic-firmenbuch.at/mcp` (leave OAuth client fields blank — DCR).
3. Authorize with any email; confirm via the double-opt-in link.
4. All 13 tools are then callable against live data.

**Alternatively**, issue the reviewer a dedicated API key ahead of time and paste it into the
submission's "test credentials" box with: `Header: X-API-Key: <key>`. → **OWNER ACTION**: mint a
reviewer key and privilege it (add its email to `PRIVILEGED_EMAILS`) so it never hits free-tier
caps during functional testing.

---

## 6. Compliance answers

- **Data collected:** email (signup), a hashed API key, and per-tool usage counters (no query
  content, no conversation data). Retention ≤ 12 months. See `datenschutz.html`.
- **Third parties:** Cloudflare Turnstile (bot check at signup), Azure (EU hosting). No resale;
  bulk extraction barred by the usage terms.
- **GDPR:** officer names are public Firmenbuch data served per-query; birth data is year-only.
- Confirm compliance with the [Software Directory Terms](https://support.claude.com/en/articles/13145338-anthropic-software-directory-terms)
  and [Policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy).

---

## 7. What is already DONE in the repo (Phase 1)

- [x] All 13 tools carry `title` + `readOnlyHint: true`; regression test added.
- [x] Tool names ≤ 64 chars, `snake_case`; no catch-all tool.
- [x] OAuth 2.1 + PKCE, DCR, RFC 9728 / RFC 8414 discovery at root.
- [x] **401 on the first `initialize`** now the default (`MCP_ANONYMOUS_DISCOVERY=false`).
- [x] Redirect URI `https://claude.ai/api/mcp/auth_callback` accepted; refresh + revocation 401s.
- [x] Privacy policy, terms, imprint, onboarding + Cowork docs (DE + EN) exist on the site.
- [x] Branding: logo (`/icon.png`, 512×512) + favicon (`/favicon.svg`); `server.json` title = `Firmenbuch`.
- [x] 3 English example prompts + consolidated descriptions (this file).

## 8. Manual steps for the owner — do these in order

1. **Deploy the updated MCP server** so the new 401-on-`initialize` behavior and tool titles are
   live: rebuild + `containerapp update` (see memory: manual `az acr build` + `containerapp
   update`; CI deploy is gated off). Confirm `MCP_ANONYMOUS_DISCOVERY` is unset/false in prod.
2. **Deploy the website** so `website/icon.png` is served at `https://www.agentic-firmenbuch.at/icon.png`
   (manual `swa deploy` — memory: SWA does not auto-deploy from git).
3. **Smoke-test as a reviewer would:** add the custom connector in claude.ai → OAuth → run each
   tool once. Also run MCP Inspector against `https://mcp.agentic-firmenbuch.at/mcp` and fix
   anything it flags. Verify an unauthenticated `initialize` returns 401 (e.g.
   `curl -i -X POST https://mcp.agentic-firmenbuch.at/mcp -H 'content-type: application/json'
   -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'`).
4. **(Optional) Mint + privilege a reviewer API key** and add it to the submission (see §5).
5. **Fill out** <https://clau.de/mcp-directory-submission> using §1–§6 above. **Record the
   submission date.**
6. If no response after ~3–4 weeks, email `mcp-directory@anthropic.com`. In the same thread, ask
   whether extra trigger aliases (e.g. "Firmenbuchauszug") can be registered for chip matching.

---

## 9. Known non-blockers (optional polish)

- **Revoked-credential error** returns a spec-correct `invalid_grant` but no human "please
  reconnect" `error_description`. Fine for review; nice-to-have later
  (`fbl_auth.oauth.consume_refresh` would need to surface revoked-vs-unknown).
- **Filter enums:** `bundesland` / `legal_form` / `size_gkl` are free strings on purpose — the
  server maps full names ("Wien", "GmbH") to codes and `list_sectors` exposes valid values, so a
  strict JSON-Schema enum would remove that flexibility. Left as-is intentionally.
