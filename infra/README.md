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
| `modules/rbac.bicep` | Role assignments for the MI | Blob Data Contributor, KV Secrets User, AcrPull, Cosmos data-plane |
| `main.bicep` | subscription-scoped orchestrator | creates the RG + all modules |

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
↑ [Repo root](../README.md) · Specs: [Technische §4](../docs/Technische_Spezifikation.md) · Next stage: [`ingest`](../packages/90_ingest/README.md)
