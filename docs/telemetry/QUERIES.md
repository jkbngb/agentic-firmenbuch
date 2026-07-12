# Telemetry — App Insights KQL queries (T5)

The MCP server emits one custom span per tool call (`fbl_mcp_server.tools`, span name
`tool.<name>`) with these attributes (prefixed `fbl.` in App Insights `customDimensions`):

| attribute | meaning |
|---|---|
| `fbl.tool` | tool name (`search_companies`, `get_company_details`, …) |
| `fbl.duration_ms` | wall-clock of the tool call |
| `fbl.ru_total` | Cosmos request units the call consumed (summed across every query it ran) |
| `fbl.result_total` | `total` match count (search-like tools) |
| `fbl.zero_hit` | `true` when `result_total == 0` — the retry-spiral signal T6 targets |
| `fbl.page` | requested page |
| `fbl.filters_used` | comma-separated filter **field names** — never values (privacy) |
| `fbl.plan` | the plan in force for the call (`free` / `legacy` / …) |
| `fbl.mcp_session_id` | streamable-HTTP session id = one LLM conversation ("rounds per session") |

Wiring: set `APPINSIGHTS_CONNECTION_STRING` on the MCP container app (bicep passes it from the
`monitoring` module; the live one-off is below). With it unset the server no-ops telemetry.

```bash
# Live wiring (owner runs; connection string from the monitoring module output / portal):
az containerapp update -n app-firmenbuch-mcp -g rg-firmenbuch-prod \
  --set-env-vars APPINSIGHTS_CONNECTION_STRING="<connection-string>"
```

Spans land in the `dependencies` table (OpenTelemetry client spans) — adjust to `requests`/
`traces` if your exporter maps them differently. Each query below is copy-paste ready.

## 1. p50 / p95 latency per tool

```kql
dependencies
| where timestamp > ago(24h)
| where name startswith "tool."
| extend tool = tostring(customDimensions["fbl.tool"])
| extend dur = todouble(customDimensions["fbl.duration_ms"])
| summarize p50=percentile(dur,50), p95=percentile(dur,95), calls=count() by tool
| order by calls desc
```

## 2. RU per tool (median + total)

```kql
dependencies
| where timestamp > ago(24h)
| where name startswith "tool."
| extend tool = tostring(customDimensions["fbl.tool"])
| extend ru = todouble(customDimensions["fbl.ru_total"])
| summarize ru_p50=percentile(ru,50), ru_p95=percentile(ru,95), ru_sum=sum(ru), calls=count() by tool
| order by ru_sum desc
```

## 3. Zero-hit rate for search_companies

```kql
dependencies
| where timestamp > ago(7d)
| where name == "tool.search_companies"
| extend zero = tobool(customDimensions["fbl.zero_hit"])
| summarize searches=count(), zero_hits=countif(zero == true)
| extend zero_hit_rate = round(100.0 * zero_hits / searches, 1)
```

## 4. Tool-calls-per-session histogram (LLM rounds per intent)

```kql
dependencies
| where timestamp > ago(7d)
| where name startswith "tool."
| extend sid = tostring(customDimensions["fbl.mcp_session_id"])
| where isnotempty(sid)
| summarize calls_in_session = count() by sid
| summarize sessions = count() by calls_in_session
| order by calls_in_session asc
```

A fat right tail in query 4 (many calls per session) is the filter-guessing spiral — the number
Phase 1 (T6 relaxation + T7 docs) is meant to shrink. Track its p50/p90 before vs after those
ship. Which `filters_used` combinations correlate with `zero_hit == true` (join queries 3 + the
`fbl.filters_used` dimension) tells you which filters callers most often over-constrain.
