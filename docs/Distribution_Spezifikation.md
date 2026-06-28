# Agentic Firmenbuch — Distribution & Website Spezifikation

**Version:** 1 · **Scope:** the go-to-market layer (marketing website, free email signup, automated API-key delivery, bot protection, legal). Separate workstream from the data pipeline (Technische/Fachliche Spezifikation). English spec; **all user-facing website content is German**.

---

## 1. Goal
Test demand for the Agentic Firmenbuch MCP with a **free, email-gated** offering: a clear German landing page → user leaves an email → **double-opt-in verification** → automated **API-key** delivery → connect the MCP to Claude / Microsoft Copilot / any MCP-compatible agent. Minimal ops, cheap at low volume, scalable from day one.

## 2. Stack — Azure end-to-end (chosen)
| Concern | Service | Why |
|---|---|---|
| Website | **Azure Static Web Apps** (Free tier) | global CDN, auto-HTTPS, custom domain, scale-to-zero, ~€0 at low volume |
| Signup/verify/key API | **Azure Container App** (`app-firmenbuch-signup`, Starlette/uvicorn — `api/asgi.py`) | scale-to-zero; reuses the uv-workspace packages (`fbl_auth`, `fbl_mcp_server`) directly, which SWA-managed Functions can't see at build time. *(The §4 flow is unchanged — same routes `/api/signup\|verify\|regenerate\|unsubscribe\|playground`; only the host is a container, not Functions.)* |
| Accounts store | **Cosmos `00_accounts`** (already in the pipeline spec) | one DB tech; serverless |
| Email | **Azure Communication Services — Email** | verification + key-delivery mails; EU data residency |
| Bot protection | **Cloudflare Turnstile** (cookieless) | blocks form bots without a cookie banner |
| Secrets | **Azure Key Vault** | ACS connection, Turnstile secret, key-signing secret |
| MCP serving | existing `mcp_server` + `auth` | validates the key, per-key rate limit |

All resources in the EU region per the pipeline's region policy (`germanywestcentral` → `westeurope` → `northeurope`).

## 3. Domain & email
- **`agentic-firmenbuch.at`** (primary), `agentic-firmenbuch.com` (brand protection).
- Site: `https://agentic-firmenbuch.at` · MCP: `https://mcp.agentic-firmenbuch.at` · Mail from `no-reply@agentic-firmenbuch.at`.
- Configure **SPF, DKIM, DMARC** for the mail domain (ACS custom domain) so verification/key mails don't land in spam.

## 4. Signup → verification → API key (the automated workflow)
```
[Landing page form]                 [Azure Functions]                 [Cosmos 00_accounts]   [ACS email]
 email + DSGVO-consent + Turnstile
        │ POST /api/signup ───────────────►  verify Turnstile, validate email
        │                                    upsert {status:"pending", verify_token_hash, expires}
        │                                    ───────────────────────────────────►  send VERIFY mail ─►
        │
 user clicks link in mail
        │ GET /api/verify?token=… ─────────►  check token+expiry → status:"verified"
        │                                    generate API key (random, store ONLY sha256(key))
        │                                    ───────────────────────────────────►  send KEY mail ────►
        │
 user adds key to their MCP client → connects to mcp.agentic-firmenbuch.at
        │ POST /api/regenerate (email) ───►  re-send a verify link → issue NEW key, invalidate old
```
**Account document (`00_accounts`):**
```jsonc
{ "id": "<uuid>", "email": "user@x.at", "status": "pending|verified|revoked",
  "verify_token_hash": "sha256:…", "verify_expires_at": "…Z",
  "api_key_hash": "sha256:…", "tier": "free", "rate_per_min": 60, "rate_per_day": 5000,
  "consent": { "text_version": "v1", "at": "…Z", "ip": "…" },
  "created_at": "…Z", "verified_at": "…Z", "usage": { "calls_today": 0, "day": "…" } }
```
Rules: store **only the hash** of the API key and of the verify token (never plaintext). Verify token expires (e.g. 24 h). API key shown to the user **once** in the email.

## 5. API-key change (v1, kept simple)
A "neuen Key anfordern" link → `POST /api/regenerate` with the email → sends a fresh verify link → on click, issue a new key and **invalidate the old hash**. No account dashboard in v1.

## 6. Bot protection & rate limiting (free but abuse-resistant)
- **Signup form:** Cloudflare Turnstile (server-side verified in the Function) + **double-opt-in** (a bot rarely owns the inbox) + **IP throttle** on `/api/signup` (e.g. 5/min/IP) + basic email sanity/disposable-domain check.
- **MCP server:** validate the key on every call; **per-key rate limit** — generous default **60 req/min, 5 000 req/day** (config, raise per request); return `429` with `Retry-After`. Optional global IP throttle in front (Front Door / Cloudflare) for unauthenticated noise.
- Tiers/quotas are config (a paid tier later = config change, not a rewrite).

## 7. Legal & privacy (Austria/EU best practice)
- **Impressum** (ECG §5 + Mediengesetz §24/§25): Einzelunternehmer name, address, email; business purpose; UID if any; Gewerbe/Behörde if applicable. *(Details are placeholders until provided.)*
- **Datenschutzerklärung** (DSGVO): controller; data collected = **email** (+ security logs: IP, timestamp, usage counts); purposes (account, key delivery, abuse prevention); legal bases — **Art 6(1)(a) consent** for the emails, **Art 6(1)(f) legitimate interest** for security/anti-abuse; processors (**Microsoft Azure/ACS** as Auftragsverarbeiter with DPA; **Cloudflare Turnstile**); **EU storage**; retention; data-subject rights (Auskunft, Löschung, Widerruf); complaint to the **Datenschutzbehörde**.
- **Consent:** an explicit, unticked DSGVO checkbox on the form; store the consent text version + timestamp (+ IP).
- **Cookies:** the site sets **no tracking cookies** → a short privacy notice suffices, **no intrusive consent banner**. (Turnstile is privacy-friendly; disclose it in the Datenschutz.) If analytics are wanted later, use a cookieless tool (Plausible/Matomo) to avoid a consent banner.
- **Right to deletion:** an `/api/unsubscribe` link (or email request) removes the account + revokes the key.

## 8. SEO (build the foundation now)
Semantic German HTML; one `<h1>`; descriptive `<title>` + meta description (German keywords: *Firmenbuch API, Firmenbuch MCP, KI-Agent, Bilanzdaten, Unternehmensdaten Österreich*); **OpenGraph + Twitter cards**; **JSON-LD `SoftwareApplication`**; `sitemap.xml` + `robots.txt`; fast static delivery; mobile-first; OG share image. Ranking comes from content over time — the on-page foundation ships day one.

## 9. What's still needed from the owner
1. Register the domain(s); point DNS at Static Web Apps + configure the ACS mail domain (SPF/DKIM/DMARC).
2. Impressum details (name, address, contact email, UID/Gewerbe if any).
3. Cloudflare Turnstile site+secret keys (free).
4. Confirm the free-tier rate limits and the exact German product claims (esp. which agents to name: Claude, Microsoft Copilot, "alle MCP-kompatiblen Tools").

## 10. Deliverables in this workstream
- `website/index.html` — the German landing page (this spec's §4–§8 realised).
- `website/impressum.html`, `website/datenschutz.html` — legal pages (placeholders).
- `website/robots.txt`, `website/sitemap.xml`, OG image — SEO.
- The signup API (`/api/signup`, `/api/verify`, `/api/regenerate`, `/api/unsubscribe`, `/api/playground`) + ACS email templates — **implemented** as the Starlette Container App `api/asgi.py` (not Functions; see §2), deployed as `app-firmenbuch-signup`.

> This is a separate workstream from the autonomous data-pipeline build; it should not block or be blocked by it. The MCP server it points at is the one already built.

### 10a. Go-live wiring checklist (state as of 2026-06-21)
The §4–§6 logic and the security model (random `secrets` token, **sha256 hash-only** at rest, double-opt-in, regenerate-revokes-old, Turnstile + IP throttle) are built and deployed. Remaining wiring to make the public loop complete end-to-end:

| Step | What | State |
|---|---|---|
| 1 | **ACS email** — `ACS_CONNECTION_STRING` (container secret) + `ACS_SENDER_ADDRESS=DoNotReply@<managed>.azurecomm.net` on `app-firmenbuch-signup`; Azure-managed domain has **SPF/DKIM/DMARC verified** | ✅ wired 2026-06-21 (test send accepted). A branded `@agentic-firmenbuch.at` sender (own SPF/DKIM) is a later upgrade. |
| 2 | **Frontend → API host** — the static site fetches signup/playground from the container via `window.API_BASE` + CORS (`connect-src` allows it). Verify-link host = `API_PUBLIC_URL` | ✅ wired (raw container URL; swaps to `api.` once bound) |
| 3 | **Custom domains** `api.` / `mcp.` — CNAME + **`asuid` TXT** at the registrar, then `az containerapp hostname bind` (managed cert) | ⏳ TXT records pending from owner; then bind + flip `API_BASE`/onboarding to the branded hosts |
| 4 | **MCP serves real data** — `10_presentation` populated by backfill→process | ⏳ backfill running (~days) |
| 5 | **Admin visibility** — read-only view of signups/usage from Cosmos `00_accounts` (emails + tier + counters; **never the key** — only its hash exists) | ☐ optional, not built |

**Key facts for operations:** API keys are **never stored or retrievable in plaintext** — only `sha256:<hex>` lives in Cosmos, so even the admin cannot read a user's key (by design); a lost key is replaced via regenerate, which **revokes the old hash**. Exactly one active key per email.

---

## 11. MCP client connection & authentication (confirmed against Claude, Juni 2026)
How users connect Agentic Firmenbuch to their agent — and the one real auth decision.

- **Claude's consumer apps (Desktop / Web / Cowork) "Add custom connector" UI uses OAuth 2.1** (authorization-code + PKCE). That UI has **no field for a Bearer token or custom header** — it only takes a server URL and (optionally) an OAuth Client ID/Secret. So for the smooth **one-click "connect → log in"** experience, the server must speak **OAuth 2.1**.
- **API-key-in-header still works**, just not through that one-click UI: via **Claude Code** (`claude mcp add --header "X-API-Key: …"`) and via the **`mcp-remote --header`** local proxy (which Desktop users can configure to skip OAuth entirely), plus any programmatic MCP client. This is the **developer path** and it's what the built server already supports.

**Decision — support BOTH (recommended, and not a massive headache):**
- **Ship now:** the **API-key header** (already built). Document the Claude Code + native `type:"http"` connector setup in the onboarding page. Good enough for the LinkedIn/dev audience from day 1.
  - **Cross-platform command (best practice):** the `claude mcp add` command in the onboarding + key-delivery email is a **single line** — `claude mcp add --transport http agentic-firmenbuch https://mcp.agentic-firmenbuch.at --header "X-API-Key: …"` — with **no `\` line-continuation**, because backslash-continuation breaks in Windows PowerShell. One line works identically on macOS, Linux and Windows.
  - **Connection-test prompt (best practice):** every "what to do after connecting" example uses a **connection check**, not a data query — e.g. *„Welche Firmenbuch-Werkzeuge stehen dir zur Verfügung?"* — so the user can immediately verify the MCP handshake succeeded (the agent lists the tools) before trying real questions.
  - **MCP HTTP surface:** the protocol lives at **`/mcp`** (a bare `GET /mcp` correctly returns **406** — the client must negotiate the streamable-HTTP session). A bare **`GET /`** returns a friendly **HTML landing** (explains this is the MCP endpoint, links to onboarding) instead of a raw 404, and **`GET /health`** returns `{"status":"ok"}` for probes. Both are registered via FastMCP `custom_route` in `mcp_server/app.py`.
- **Fast-follow (before broad non-dev launch):** add **OAuth 2.1** on the MCP server so Desktop/Cowork users click-connect and log in with their Agentic-Firmenbuch account (the OAuth login validates the same account/key behind the scenes). Effort: **moderate** (~2–4 dev-days) — OAuth 2.1 + PKCE + `/.well-known/oauth-authorization-server` discovery + token endpoint. FastMCP has auth helpers; alternatively front the server with **Azure API Management** or **Entra External ID** which provide the OAuth layer managed (see §12).
- The two coexist on one endpoint: advertise OAuth for the UI, **and** accept a valid `X-API-Key` header for config/proxy clients.

> "Simplest for users" nuance: for **non-technical** users, **OAuth is actually the simpler UX** (click + login, no key pasting); for **developers**, the API key is simplest. Supporting both covers everyone. Sources confirmed: Claude Help Center (custom connectors / remote MCP), modelcontextprotocol.io, Claude Code MCP docs.

## 12. Build vs. buy — the signup/key service
**Build it (recommended for v1).** The flow (email → double-opt-in → issue+hash key → regenerate) is a small, well-trodden pattern: ~4 Azure Functions + Cosmos `00_accounts` + ACS email + Turnstile, a few hundred lines. It will work well and is cheapest; you keep full control of the email-only UX. **Not novel, low risk.**
- **Buy / managed alternative — Azure API Management (APIM):** gives you **subscription keys, rate limiting/quotas, OAuth, and a self-service developer portal** out of the box — i.e. it could cover §11 OAuth **and** key issuance **and** rate limits in one managed service. Trade-off: a base cost + more config than you need for a free demand-test. **Recommendation:** build the simple Functions now; consider APIM later if you want a managed portal + OAuth without building them. Auth0/Clerk etc. are overkill for "email → API key."

## 13. Interactive playground — a FULL ChatGPT-style interface (highest-leverage conversion feature)

**Hard requirement on the UX:** this is a **full chat interface**, like ChatGPT — **NOT** a small "Chat hier" launcher bubble in a corner that opens a little popup. The chat is a **primary, full-width surface**: its own page (`/playground` or `/probieren`) and/or a dominant section, with:
- a centered conversation column, **message bubbles** (user right / assistant left), assistant responses **streamed** token-by-token,
- a **pinned input bar** at the bottom (textarea + send), auto-grow, Enter-to-send,
- **suggested-prompt chips** shown in the empty state *inside* the chat (like ChatGPT's example prompts) — German examples ("GmbHs in der Steiermark, Bilanzsumme > 5 Mio.", "GF über 60 …"); clicking one starts the conversation,
- assistant answers render **structured results** nicely (company cards / a compact table with the key Kennzahlen), not just plain text,
- matches the site's design system exactly (dark `#0C0E12`, emerald `#19C37D`, Bricolage + IBM Plex), responsive, reduced-motion-safe, accessible.
It must *feel* like a real product chat, because that's the whole demo.

**System-Prompt + User-Prompt as two distinct inputs (LOCKED UX — built in `playground.html`, and mirrored as a homepage `#playground` section in `index.html`):**
- The console exposes **two** fields: a **System-Prompt** (the agent's role / tone / rules — set once) and a **User-Prompt** (the concrete question — per request). Both are freely editable by the visitor.
- A **Persona dropdown** pre-fills the System-Prompt with ready-made roles the target users care about — **Finance & M&A-Analyst · B2B-Vertriebs-Researcher · Marktforscher · Due-Diligence-Prüfer** — plus **„Eigene Persona schreiben …"** for a custom one. Selecting a persona also seeds a matching example User-Prompt.
- The **difference is explained inline, without any clicking** — two numbered explainer chips above the console ("1 System-Prompt — Wer ist der Agent? … / 2 User-Prompt — Was soll er tun? …") — so a first-time visitor understands it at a glance.
- Self-explanatory, low-friction: the persona dropdown defaults to the first persona so the console is never empty; Enter sends, Shift+Enter = newline.

**Scope — MCP-only (DECIDED).** The playground answers **only** from the **Firmenbuch MCP server**; it does **not** do open-web / internet aggregation. Rationale: (a) the playground exists to demo *this* tool — official register data an agent can't trivially fetch itself; mixing in general web search blurs the message; (b) the "Quelle: Firmenbuch MCP-Server" badge is a trust signal that every answer is grounded in official data; (c) a public, no-login surface doing live web browsing is an expensive abuse/DoS magnet. The visitor's *own* agent can of course combine this MCP with its own web tools — the playground just showcases the MCP piece specifically.

**Backend (two modes behind the same full UI):**
- **LLM mode (the real experience, recommended for the demo):** free-text German → a **cheap model (Claude Haiku)** does tool-calling against the MCP/query layer → streams a grounded answer with real data. A fake chat (chips only) undermines the point, so the LLM mode is the target.
- **Structured fallback mode (a config flag):** if LLM cost/abuse is a concern at any moment, the same UI can run a deterministic backend (suggested prompts + simple intent→structured-search) with **zero LLM cost**. Flip via env, no UI change. Launch can start here and flip LLM on.

**As-built in v1 — DETERMINISTIC mode is live; no LLM is connected.** The launch runs the structured fallback. There is **no Claude/LLM in the playground hot path**, the Anthropic key is **not** used by the playground, and nothing the visitor types is sent to a model. The transform is a small rule-based intent parser:
- `parse_intent(text)` in `packages/mcp_server/.../playground.py` lowercases the free-text German and runs a series of **regex/keyword rules** → a `SearchFilters` object + human-readable `notes` listing which filters it understood. Examples: `\bag\b|aktiengesellschaft` → `legal_form=AG`; `bilanzsumme über X mio` → `bilanzsumme_min`; `eigenkapitalquote über X%` → ratio floor; `gf … über 60` → `gf_min_age`; a Bundesland name → `bundesland`. Anything it can't map is simply ignored (and the `notes` say so) — it never invents data.
- The resulting `SearchFilters` runs through **the same query layer / MCP read path as the product** (search over `10_presentation`), so the columns returned match the real tool. The answer is rendered from those rows — there is no generated prose beyond the templated `notes`. **`playground_llm_enabled` (env flag, default off)** gates the future LLM-tool-calling mode (Claude Haiku); until it is flipped on, the deterministic parser above is authoritative.
- Consequence for messaging: the playground demonstrates **the data + the filtered-search tool**, not natural-language understanding. The visitor's *own* agent (via the MCP server) is what supplies the LLM in real use. The onboarding/Datenschutz copy must not imply an LLM answers in the playground while this mode is live.

**Mandatory guards (anonymous users = treat as hostile):** Cloudflare Turnstile gate before first message; **per-visitor cap** (e.g. 5–10 messages/day), **per-IP** + **global daily spend cap** with a **kill-switch env flag**; output length limits; **no chat-history persistence**; a tight system prompt scoped to Firmenbuch queries (refuse off-topic / injection). Document the LLM processing in the Datenschutz.

**Dependency / sequencing:** the playground's backend calls the **same query layer / MCP tools and returns the same columns** as the product. Those tools + served fields are **not finalized yet** (see §13a) — so build the **full chat UI + the guards + the deterministic mode now**, and wire the LLM-tool-calling to the MCP **after** the tools/columns are confirmed.

A separate **playground spec is folded in here**; Claude Design should build the chat UI to this brief + the site's design system.

## 13a. Build approach — autonomous loop, with a confirmation checkpoint on MCP tools/columns
Run the **website + Functions + playground UI** build **autonomously in a loop until finished** (same pattern as the pipeline build), committing per piece. **But do NOT finalize the MCP server's exposed tools and served columns autonomously** — the owner wants to **confirm the functionalities (MCP tools) and the columns (served fields) first**. So:
- The agent builds everything that does **not** depend on the final tool/column contract (site, signup Functions, ACS email, the full chat UI, the guards, the deterministic backend).
- For the MCP-dependent parts (the served `10_presented` field set, the exact MCP tool list + their inputs/outputs, and the playground's LLM tool-calling), the agent **produces a concise "Proposed MCP tools + served columns" summary and STOPS for owner confirmation** before wiring/finalizing.
- Only after the owner confirms the tools/columns does the agent finalize the MCP server and connect the LLM playground to it.
This lets the front-end/site/signup race ahead in a loop while the data-facing contract waits for a human sign-off.

## 13b. Playground — make it unmistakably an *agent* surface (not a human chatbot)

The playground's job is to convince a developer that **this is data an AI agent consumes**. v1 shows a human-readable summary + a result table; v2 makes the agent framing explicit and adds the chat affordances people expect. Built in `playground.html` (front-end) + `playground.py` / `playground_llm.py` (backend) where noted.

1. **Markdown rendering of the summary (BUG → FIX).** The LLM emits Markdown (`**bold**`, numbered lists). v1 prints it with `textContent`, so literal `**` leaks into the UI. Render a **safe inline-Markdown subset** — HTML-escape first, then `**x**`→`<strong>`, single/double newlines → `<br>`/paragraph break, leading `N.`/`- ` → list rows. No raw HTML from the model is ever injected (escape-then-format). *Front-end.*

2. **Human ⇄ JSON toggle (KEY for the "for agents" message).** A segmented control above the answer: **"Antwort" | "JSON"**. "Antwort" = today's prose + table; "JSON" = the **exact structured payload the MCP server returns** (pretty-printed, syntax-tinted, copy button), so a visitor sees *what an agent actually receives* (`results[]` cards: fnr, name, legal_form, bundesland, bilanzsumme_latest, equity_ratio_latest, revenue_latest, has_guv_latest …). Label it "Das erhält dein Agent über den MCP-Server." *Front-end (data is already returned); no backend change.*

3. **Streaming output (ChatGPT/Claude-style reveal).** Reveal the answer **progressively, word-by-word at a steady cadence** (~ the pace of Claude.ai), then apply Markdown formatting on completion. Implemented client-side over the already-returned `summary` (no SSE needed in v1); a future SSE/token-stream from the API can replace the simulated reveal. Respect `prefers-reduced-motion` (instant render). *Front-end now; optional backend SSE later.*

4. **Follow-up messages (multi-turn).** Keep the conversation: render a **scrolling chat log** (user bubbles + agent answers + tool chips), and a persistent composer at the bottom. The backend (`/api/playground` → `playground_llm.llm_answer`) accepts a **prior-messages array** so the model has context; per-visitor caps still apply per *message*. No chat history is persisted server-side (privacy, §13). *Backend + front-end.*

5. **"New chat" / clear.** A control that resets the log and starts a fresh conversation (clears client state only; nothing was persisted). *Front-end.*

6. **Company detail view (click a result → full record).** Clicking a result row opens a panel showing **everything in `10_presentation` for that FNR**: identity/status/management(age only), the **Bilanz as a proper statement (Aktiva ↔ Passiva side-by-side)**, **GuV and ratio time series** as a year-over-year table and/or sparkline/line chart, plus source/year provenance. Backed by a public, read-only **`get_company_details(fnr)`**-style call the playground can make for any served FNR (rate-limited like the rest of the playground; names withheld per GDPR §8.7). This is the strongest "look how rich the served data is" moment. *Backend (expose a playground-safe detail fetch) + front-end (detail UI + a tiny chart helper).*

**Sequencing.** 1–3 + 5 are front-end-only → ship first. 4 (multi-turn) and 6 (detail view) touch the backend (`app-firmenbuch-signup` container) → spec'd here, built next, deployed via `az acr build` + `containerapp update`. Throughout, the visible framing stays **"this is the agent's view of official Firmenbuch data,"** never a general-purpose human chatbot.

## 14. Launch-readiness checklist (what's still missing for a real go-live)
1. **MCP connect / onboarding page** ("So verbindest du es") — per client (Claude Code, `mcp-remote`, later OAuth one-click) + the API-key-delivery email content. Without this, users get a key and don't know what to do.
2. **OAuth 2.1** on the MCP server (§11) for the one-click consumer UX (fast-follow).
3. **Signup Functions + ACS email templates + Turnstile** actually implemented (§4, specced, not built).
4. **Playground** implemented (§13).
5. **Nutzungsbedingungen / Terms of Use (AGB)** — we have Impressum + Datenschutz but **no ToS**. Add one: disclaimer of warranty ("Daten ohne Gewähr"), acceptable-use, and pass-through of the **CC BY 4.0 attribution** obligation to users. (German page `nutzungsbedingungen.html`.)
6. **MCP public hosting:** custom domain `mcp.agentic-firmenbuch.at`, TLS, autoscale, **uptime monitoring + a simple status page**.
7. **Mail domain DNS:** SPF / DKIM / DMARC for `agentic-firmenbuch.at` so verification/key mails don't go to spam.
8. **Owner analytics + alerts:** signups, queries/day, errors (for the demand test).
9. **og-image.png** (1200×630) for social sharing.

## 15. Implementation status & rough effort (whole product)
| Area | Status | Remaining work (rough) |
|---|---|---|
| Data pipeline (90→10, registry, client, MCP server, auth module) | **Built**; backfill running now | Hardening (enumeration, official-code/preservation, layer-completeness + e2e test), independent conformance audit → **~3–6 dev-days** + review |
| MCP server auth | **API-key header built** | OAuth 2.1 for one-click → **~2–4 dev-days** |
| Signup/key Functions + ACS email + Turnstile | **Specced, not built** | **~2–4 dev-days** |
| Website (landing + legal) | **Design done** (Claude Design, in `website/`) | Wire form → `/api/signup`, deploy to Static Web Apps, custom domain + mail DNS → **~1–2 dev-days** |
| Playground | **Decided, not specced/built** | Chips version **~1–2 dev-days**; LLM mode **+2–3** |
| ToS + onboarding page + status/monitoring | **Missing** | **~1–2 dev-days** |

**Honest total to a real public launch:** on the order of **~2–3 weeks** of focused agent + review work, dominated by pipeline hardening/validation, the signup+playground build, and OAuth — running in parallel with the data backfill that's already in progress. The data backfill itself is wall-clock time, not dev work.

## 16. Spec structure (confirming best practice)
Three separate specs, by concern (this is the best-practice split):
- **Fachliche Spezifikation** — the *what/why* of the data product (business rules, scope).
- **Technische Spezifikation** — the *how* of the pipeline + MCP server.
- **Distribution Spezifikation** (this doc) — the go-to-market: website, signup, playground, legal, integration.

The **website itself is entirely in German** (`website/index.html`, `impressum.html`, `datenschutz.html`, and the to-be-added `nutzungsbedingungen.html` + onboarding page). The spec documents are kept in **English** because they are read by the coding/design agents (Claude Code / Claude Design) — German user-facing copy lives inside the website files, not in the spec prose.

---

## 17. Animated "Agent in Action" hero — show it running (not a boring list)
**Strategy: market what it *does*, not the MCP server.** The hero must make a visitor *feel* the product is alive and working — animated, not a static screenshot or a dull "new companies today" list. It's the **#1 hero element**; the interactive chat playground (§13) sits directly below as "…now try it yourself."

**Recommended build (Option A + C):**
- **A — Auto-playing live agent (primary):** a chat-style panel that, on load, **auto-types a punchy German query and streams a real answer from live data**, then loops through 2–3 different impressive queries. The visitor sees the actual product working before touching anything. It flows into the real playground ("…oder frag selbst"). This *is* the product, animated.
- **C — Count-up scale strip (above A):** animated numbers on load — *"≈187.000 Unternehmen · X Jahresabschlüsse · täglich automatisch aktualisiert."*
- *(Option B, optional flavor: a terminal-style "live activity" console streaming "09:00:03 — 14 neue Firmen erkannt · Bilanz geparst · Kennzahlen berechnet…" with a pulsing live-dot + "zuletzt aktualisiert vor 2 Std.")*

**Content = real signals, not a registration list.** The looping examples should be interesting ("Signale des Tages": a company with a large equity jump, a Geschäftsführer active at many firms, a sector cluster of new registrations) — the daily delta already surfaces fresh material, so it stays current. Frame as **facts + deterministic signals**, **NO scoring** (no exit/succession score, ever, in v1).

**Data & hosting (Static Web App still works):**
- Animation is **front-end (CSS/JS)** — fully supported on Azure Static Web Apps. "Static" ≠ "no motion".
- Live/dynamic data comes from **Azure Functions**: a small read-only `/api/demo` (and/or a daily-refreshed `briefing/latest.json` in Blob) feeds the looping examples. Generated/served server-side once per day or cached → **no per-visitor LLM cost, no abuse surface** (unlike the live playground).
- **Names already inherit the MCP/data-layer gating** (officer names are blocked everywhere by default, §8.7) — the hero just shows what the product shows; no extra GDPR handling needed here. Companies are public.

**Effort:** ~1–2 dev-days (the animated hero + a tiny `/api/demo` feed). Depends only on the daily delta (already running) + the existing query layer — **not** the final public MCP contract, so it can be built before the §13a checkpoint.

> Net: the hero should *move* and *update itself* — an agent visibly working over live Firmenbuch data — because that's the proof. A list of new GmbHs is the boring version; the auto-playing agent + live counters is the interesting one.
