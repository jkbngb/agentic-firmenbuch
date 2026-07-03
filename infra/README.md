# `infra/` — Azure infrastructure (Bicep) · Stage 4

↑ Back to the [root README](../README.md).

**Purpose:** bring the whole EU-only Azure environment up from nothing with **one
idempotent command** (§4.0). Bicep is declarative, so re-running creates only what's
missing and leaves existing resources untouched.

## What it provisions
| Module | Resource | Notes |
|---|---|---|
| `modules/storage.bicep` | Storage (ADLS Gen2) + containers `90-raw`, `70-parsed` | HNS on, no public access |
| `modules/cosmos.bicep` | Cosmos (serverless) DB `firmenbuch` + containers | `50/30/10`, `99_registry`, `00_accounts`, reserved `40/20`; `10_presentation` index policy (§4.1); local auth disabled |
| `modules/keyvault.bicep` | Key Vault | RBAC-authorized |
| `modules/acr.bicep` | Container Registry | admin disabled (pull via MI) |
| `modules/monitoring.bicep` | Log Analytics + App Insights | §11 |
| `modules/communication.bicep` | Communication Services + Email | `dataLocation: Europe` |
| `modules/identity.bicep` | User-assigned Managed Identity | shared by Job + MCP app |
| `modules/containerapps.bicep` | ACA env + pipeline **Job** (cron, singleton) + **MCP app** (scale-to-zero) | §8.8/§8.9 |
| `modules/rbac.bicep` | Role assignments for the MI | Blob Data Contributor, **Blob Delegator** (user-delegation SAS for `get_document` downloads), KV Secrets User, AcrPull, Cosmos data-plane |
| `main.bicep` | subscription-scoped orchestrator | creates the RG + all modules |

## Live Container Apps Jobs (runbook)

Four scheduled Jobs share one image (`firmenbuch-pipeline:<tag>`) and dispatch on `--mode`.
The pipeline is two-phase: **ingest** fetches raw from the API into Blob `90-raw`; **process**
turns that raw into the served `10_presentation`. State as of 2026-06-28:

| Job | Mode | Does | Reads → writes | API? | Cron |
|---|---|---|---|---|---|
| `job-firmenbuch-daily` | `daily` | Change-feed delta: detect changed FNRs, ingest + process the dirty set | feed → Blob + Cosmos | yes | `0 3 * * *` (daily) |
| `job-firmenbuch-backfill-ingest` | `backfill-ingest` | Bulk **fetch** of filing artifacts (publication-required forms first) | Registry → `90-raw` | **yes** | `0 4 * * *` (daily; drains dead-letters, then idles) |
| `job-firmenbuch-backfill-process` | `backfill-process` | Bulk **transform** raw → served layer | `90-raw` → `10_presentation` | no | `0 6 * * *` (daily) |
| `job-firmenbuch-pipeline` | (manual) | Generic entrypoint CI rolls to the latest image | — | — | manual |

Notes:
- **Pause a job** without deleting it: set its cron to an impossible date, `0 0 31 2 *`
  (31 Feb = never). That is the parked state; restore a real cron to resume. The backfill
  was parked this way from 23.06.–28.06.
- **Keep the backfill jobs** even when idle — an idle scheduled run exits in seconds (nothing
  pending) and they are the dead-letter recovery + re-grind / new-Rechtsform mechanism.
- **Roll to a new image:** `az acr build --registry $ACR --image firmenbuch-pipeline:$TAG
  --file infra/docker/pipeline.Dockerfile .` then `az containerapp job update -n <job> --image …`.
  CI rolls only `job-firmenbuch-pipeline` + the MCP app on push to `main`; the daily/backfill
  jobs are rolled by hand (they pin specific tags). Live status:
  `az containerapp job execution list -g rg-firmenbuch-prod -n <job>`.

## Region policy (EU-only, ordered fallback, §4.0)
`setup.sh` tries **germanywestcentral → westeurope → northeurope**. Override a single
region with `REGION=...`. Nothing deploys outside the EU (GDPR / data residency).

## Run it
```bash
az login                       # select the right subscription
./infra/setup.sh               # idempotent bring-up (what-if, then create)
# teardown is explicit and separate:
./infra/teardown.sh
```
`setup.sh` runs a `what-if` first, so a re-run on an existing environment clearly
reports "no changes". Environment/base name via `ENVIRONMENT` / `BASE_NAME` env vars.

## Validate without deploying
```bash
az bicep build --file infra/main.bicep   # compiles to ARM JSON; 0 = valid (no deploy, no cost)
```
> **Deployment is billable** and needs an `az login` + subscription, so it is **not**
> run as part of the automated build — author/validate here, deploy when ready.

## Definition of Done (§15 step 4)
Bicep compiles cleanly (`az bicep build` exits 0); `setup.sh` is idempotent with the
EU region fallback; teardown is a separate explicit script. Live `az deployment`
verification is a manual step (billable).

## After deploy
1. Put `FIRMENBUCH_API_KEY` into Key Vault.
2. Build + push the pipeline and MCP images to ACR and roll the Job/App — automated by
   the CI `deploy` job (§13) on every push to `main`. It runs only when the deploy
   **repo variables** are set (else it skips cleanly): `ACR_NAME`, `RESOURCE_GROUP`,
   `PIPELINE_JOB_NAME`, `MCP_APP_NAME`, plus the OIDC **secrets** `AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` (federated credential on the user-assigned
   identity — no stored passwords). Images build from [`docker/pipeline.Dockerfile`](docker/pipeline.Dockerfile)
   (`fbl-pipeline`) and [`docker/mcp.Dockerfile`](docker/mcp.Dockerfile) (`fbl-mcp`).
   To deploy by hand instead: `az acr build … && az containerapp{,' job'} update --image …`.
3. Run the Initial Load by hand (`--mode sync-registry → backfill-ingest → backfill-process`, §15a.1).

---
↑ [Repo root](../README.md) · Specs: [Technische §4](../docs/specs/Technische_Spezifikation.md) · Next stage: [`ingest`](../products/agentic-firmenbuch/packages/90_ingest/README.md)
