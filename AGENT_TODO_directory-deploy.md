# Agent task list — ship the "Firmenbuch" directory submission

**For a coding agent.** All code changes are already committed to `main` (working tree clean).
Your job is to **deploy** what's committed and **verify** it, so the owner can then fill out the
web form. Do the tasks in order. If a task is blocked (missing credentials/vars), **stop that
task, report exactly what's missing, and continue with the ones you can do** — never guess
secrets or invent resource names.

Context you can rely on:
- Repo root: `/Users/jakob/Documents/Projects/agentic-first/agentic-firmenbuch`
- What was changed & why: `docs/DIRECTORY_SUBMISSION.md` §7. Summary: 13 MCP tools now have
  `title` + `readOnlyHint`; unauthenticated `initialize` now returns **401** by default (new
  setting `MCP_ANONYMOUS_DISCOVERY`, default false); `server.json` title = `Firmenbuch`;
  `website/icon.png` added.
- Deploy is **manual** (CI deploy job is gated off). Commands below come from
  `.github/workflows/ci.yml` (deploy job) and `website/README.md`.

---

## Task 0 — Pre-flight (always safe)
```bash
cd /Users/jakob/Documents/Projects/agentic-first/agentic-firmenbuch
git status --short           # expect clean
git rev-parse --short HEAD   # note the SHA you are deploying
uv run pytest products/agentic-firmenbuch/packages/mcp_server/tests/ -q   # expect all green
```
If tests fail, STOP and report — do not deploy a red build.

---

## Task 1 — Deploy the MCP server (Container App)
**Needs:** `az login` already done, and the three deploy vars. Discover them instead of guessing:
```bash
az account show >/dev/null || { echo "NOT LOGGED IN → stop, ask owner to 'az login'"; exit 1; }
# Resource group + app name (hint: RG is like 'rg-firmenbuch-prod', ACR like 'acrfirmenbuch<suffix>',
# suffix seen in repo = xbjux2hw). Confirm by listing:
az containerapp list -o table          # find the MCP Container App name + its resource group
az acr list -o table                   # find the ACR name
```
Set `ACR_NAME`, `RESOURCE_GROUP`, `MCP_APP_NAME` from what you found, then:
```bash
TAG=$(git rev-parse HEAD)
LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)

# Build + push the MCP image (only the mcp image is needed for this change):
az acr build --registry "$ACR_NAME" \
  --image "firmenbuch-mcp:$TAG" --image "firmenbuch-mcp:latest" \
  --file infra/docker/mcp.Dockerfile .

# Roll the Container App to the new image:
az containerapp update --name "$MCP_APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --image "$LOGIN_SERVER/firmenbuch-mcp:$TAG"
```
**Critical guard — the 401 fix must be active:** ensure the app does NOT have
`MCP_ANONYMOUS_DISCOVERY=true` set. Check and, if present as true, remove it:
```bash
az containerapp show -n "$MCP_APP_NAME" -g "$RESOURCE_GROUP" \
  --query "properties.template.containers[0].env" -o json | grep -i anonymous || echo "not set → good (defaults to false)"
```
Wait for the new revision to be healthy:
```bash
az containerapp revision list -n "$MCP_APP_NAME" -g "$RESOURCE_GROUP" -o table   # newest = Running/Healthy
```

---

## Task 2 — Deploy the website (Static Web App)
**Needs:** the SWA deployment token (owner-held secret). If you don't have it, STOP this task and
report; do not attempt to fetch it.
```bash
swa deploy ./website --deployment-token <SWA_DEPLOYMENT_TOKEN> --env production
```
This publishes the new `website/icon.png` (the directory logo).

---

## Task 3 — Post-deploy verification (do after Tasks 1 & 2)
```bash
# 3a. THE 401 CONTRACT — unauthenticated initialize must be 401 + WWW-Authenticate:
curl -i -s -X POST https://mcp.agentic-firmenbuch.at/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | head -20
#   EXPECT: "HTTP/.. 401" and a "www-authenticate: Bearer resource_metadata=..." header.
#   If you get 200 → the deploy didn't take or MCP_ANONYMOUS_DISCOVERY=true is set. Fix + redeploy.

# 3b. Discovery docs resolve at the host root:
curl -s https://mcp.agentic-firmenbuch.at/.well-known/oauth-protected-resource | head
curl -s https://mcp.agentic-firmenbuch.at/.well-known/oauth-authorization-server | head

# 3c. Logo is live (200, image/png):
curl -sI https://www.agentic-firmenbuch.at/icon.png | head -3
curl -sI https://mcp.agentic-firmenbuch.at/icon.png | head -3

# 3d. (Optional, if npx available) full protocol validation:
npx @modelcontextprotocol/inspector --cli https://mcp.agentic-firmenbuch.at/mcp
```
Report the actual HTTP status lines you observed for 3a and 3c — those are the two that gate
the submission.

---

## Task 4 — (Optional) Mint a reviewer access key
Only if the owner asks for a pre-shared key for Anthropic's reviewer (the OAuth/email flow works
without one). `scripts/grant_pro.py` creates a time-boxed full-access ("guest") account by email
and prints its API key. Needs `COSMOS_ENDPOINT` + Azure auth:
```bash
COSMOS_ENDPOINT=https://cosmos-firmenbuch-<suffix>.documents.azure.com:443/ \
  uv run python scripts/grant_pro.py --help    # read the flags, then run with the reviewer email
```
Give the printed key + `Header: X-API-Key: <key>` to the owner for the form.

---

## What you CANNOT do (leave for the human owner)
- **Fill the submission form** at <https://clau.de/mcp-directory-submission> — needs a browser +
  the owner's account. All field values are ready in `docs/DIRECTORY_SUBMISSION.md` §1–§6.
- **Provide Azure login or the SWA deployment token** if they aren't already available to you.
- **Decide the reviewer email** for Task 4.

## Definition of done for the agent
Tasks 1–3 executed; Task 3a shows **401 + WWW-Authenticate** and Task 3c shows **200** for the
logo. Then report back so the owner submits the form.
