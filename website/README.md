# `website/` — the public site (Azure Static Web Apps)

The go-to-market front end for agentic-firmenbuch: a static, dependency-free site (plain HTML +
inline CSS/JS, German) served from **Azure Static Web Apps**. It talks to the signup/playground
API (the Starlette app in [`../api/`](../api/README.md)) and points users at the MCP server.

↑ Back to the [root README](../README.md).

## Pages
| File | Purpose |
|------|---------|
| `index.html` | Landing page — hero, value, use cases, integrations, the `#playground` section, signup, FAQ. |
| `playground.html` | Standalone "ChatGPT-style" playground (talks to `/api/playground`). |
| `onboarding.html` | How to connect an MCP client (Claude Code, native `type:"http"` connector, etc.). |
| `verified.html` / `verify-fehler.html` | Double-opt-in landing pages after the email verify link. |
| `impressum.html` · `datenschutz.html` · `nutzungsbedingungen.html` | Legal (Impressum / Datenschutz / AGB), German. |
| `favicon.svg` | Site icon. |
| `staticwebapp.config.json` | SWA routing, security headers (CSP), and SPA fallbacks. |

## Conventions
- **No build step, no framework** — open any file in a browser. Theming via CSS variables
  (dark `#0C0E12`, emerald `#19C37D`, Bricolage + IBM Plex).
- `window.API_BASE` selects the API host (defaults to the production `api.` host).
- Bot protection on the forms uses Cloudflare Turnstile (cookieless).

## Deploy
Deployed via the SWA CLI: `swa deploy ./website --deployment-token <token> --env production`.
See the [root README](../README.md) and the Distribution spec (`../docs/Distribution_Spezifikation_v1.md`)
for the full go-live checklist.
