# Per-user usage metering — Spec (V1 design, V2 implementation)

> Status: **design**. No implementation in this commit. The goal is to land a
> meter you can run forever — billing-grade if you ever monetise, light enough
> to leave on by default during free-tier launch.

---

## 0. TL;DR

1. **What to measure**: collect **three independent counters per call**, not
   one. Each captures something the others miss; the right metric to *show* the
   user depends on context.
2. **The three counters**:
   - **`calls`** — flat count of tool invocations. Cheapest to communicate,
     easiest to rate-limit on. The default "how busy is this key".
   - **`compute_units`** — weighted call cost (search=1, details=2,
     full_record=5, document=3, etc.). The fair-use accounting unit. Maps to
     "how much did you actually cost us".
   - **`ru_consumed`** — sum of Cosmos `x-ms-request-charge` RUs across all
     reads triggered by the call. The exact-cost basis — what Azure literally
     bills us. Use for cost analysis, never to display to the user (it's noisy
     and confusing).
3. **Storage**: one append-only document per `(key_hash, day_utc)` in a new
   Cosmos container `00_usage`. Daily roll-up is the right granularity — cheap
   to write (one update per call, no fan-out), cheap to query.
4. **Where**: a single middleware decorator wraps every MCP tool. Drop-in, no
   per-tool changes. Same wrapper increments `calls` + `compute_units` +
   `ru_consumed`. The Cosmos RU number comes from the response headers
   `x-ms-request-charge` on every Cosmos read, summed in a `contextvars`
   counter that the middleware reads at the end of the call.
5. **Privacy**: only the **key hash** is stored, never the e-mail. That keeps
   the meter GDPR-clean even if the meter doc is later exported.
6. **Exposure**: one admin-scope MCP tool `get_my_usage(window)` lets a user
   read their own usage; an owner-scope query (you) can roll up across keys.
   No public reports — keeps the surface tiny.

---

## 1. Why three counters, not one

The instinctive answer is "just count calls". That's wrong for our setup
because of the heavy tail:

| Tool | Typical Cosmos reads | Typical RUs | Typical response bytes |
|---|---|---|---|
| `search_companies` (page=25) | 1 query | 5-50 RU | ~5-20 KB |
| `get_company_details` | 1 point read | 1-2 RU | ~50-150 KB |
| `get_full_record` | 1 point read on a fat doc | 3-8 RU | ~200-800 KB |
| `get_document` | 1 point read + blob lookup | 1-3 RU | <1 KB (just a URL) |
| `get_cohort_summary` | 1 expensive cross-partition query | 20-200 RU | ~10-30 KB |
| `find_peers` | 1 query + N point reads | 10-50 RU | ~30-100 KB |
| `describe_fields` / `list_sectors` | 0 reads (static) | 0 RU | <5 KB |

If you charge by `calls`, 1000 `describe_fields` calls cost the same as 1000
`get_cohort_summary` calls — but the latter is ~50x more expensive. If you
charge by RUs alone, the user can't predict what a query will cost ahead of
time. The three-counter design lets you:

- **Quote fair-use rate limits in `calls`** (intuitive: "10,000 calls/day")
- **Bill in `compute_units`** if you ever monetise (predictable: each tool
  has a fixed price)
- **Reconcile our Azure bill in `ru_consumed`** (exact, for internal use)

---

## 2. The `compute_units` table

Locked at design time, written into the codebase so the user can quote it.
Numbers chosen so the cheapest call = 1, the heaviest = 10.

| Tool | compute_units | rationale |
|---|---:|---|
| `describe_fields` | 0 | static, no DB read |
| `list_sectors` | 0 | static, no DB read |
| `get_coverage` | 1 | one read of the stats doc |
| `get_document` | 1 | point read |
| `search_companies` (page ≤ 25) | 1 | one cross-partition query |
| `search_companies` (page 26-100) | 2 | larger result set |
| `get_company_details` | 2 | one fat point read |
| `get_company_history` | 3 | point read + per-position fan-out |
| `find_peers` | 5 | query + N peer point reads |
| `get_cohort_summary` | 5 | one expensive aggregate query |
| `get_full_record` | 5 | fattest single point read |
| `list_sectors` (with stats) | 2 | hits stats container |
| Bank/insurer prudential tool (future) | 3 | extra `fi_financials` joining |

Rule of thumb: a unit = ~5 Cosmos RUs. Anyone wanting fairness can convert
their `compute_units` total back to RUs at ~5x and check against Azure
billing.

**Free-tier daily limit (proposal)**: 1,000 compute_units / key / day.
That's ~500 `get_company_details` calls per day per user — plenty for an
agent doing realistic research, low enough to not get crushed by a runaway
script.

---

## 3. Data model — Cosmos container `00_usage`

One doc per `(key_hash, day_utc)`. Partition key = `key_hash` (fits naturally
with how Cosmos partitions and lets us read a user's history in one logical
partition).

```jsonc
{
  "id":              "u_<sha256(key)[:16]>_2026-07-01",
  "partition_key":   "<sha256(key)[:16]>",  // = key_hash; also the doc's id prefix
  "kind":            "daily_usage",
  "key_hash":        "<sha256(key)[:16]>",
  "day_utc":         "2026-07-01",
  "tier":            "free",                 // or "paid" later

  "calls":           1842,
  "compute_units":   3127,
  "ru_consumed":     15843.2,
  "bytes_out":       8204321,                // optional, cheap to add

  "by_tool": {
    "search_companies":      { "calls": 612, "compute_units": 612,  "ru_consumed": 4233.1 },
    "get_company_details":   { "calls": 980, "compute_units": 1960, "ru_consumed": 8521.6 },
    "get_full_record":       { "calls":  84, "compute_units":  420, "ru_consumed":  997.4 },
    "get_cohort_summary":    { "calls":  37, "compute_units":  185, "ru_consumed": 1532.1 },
    "...": { /* one per tool actually called that day */ }
  },

  "first_call_at":   "2026-07-01T00:14:22Z",
  "last_call_at":    "2026-07-01T23:48:51Z",

  "errors":          { "rate_limited": 12, "auth_failed": 0, "tool_error": 3 },

  "_meta": { /* standard lineage block */ }
}
```

Why daily roll-up and not per-call:

- **Cosmos write cost**: one upsert per call is fine, two upserts per call
  (per-call + daily) is overkill. Per-call records would be billions per
  month; daily is bounded.
- **Read pattern**: the user only ever wants "what have I used today /
  this month" — daily is the natural read.
- **GDPR**: less granular data = less data to defend / delete.
- **If per-call audit is ever needed**, the App Insights / Container App
  request log already captures every HTTP call with timestamp + tool name.
  No need to duplicate in Cosmos.

### 3.1 Container config

```bicep
resource usage 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  name: '00_usage'
  properties: {
    resource: {
      id: '00_usage'
      partitionKey: { paths: ['/partition_key'], kind: 'Hash' }
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [{ path: '/day_utc/?' }, { path: '/key_hash/?' }]
        excludedPaths: [{ path: '/*' }]
      }
      defaultTtl: 31_536_000  // 1 year; longer = cost grows linearly
    }
  }
}
```

Indexing only `day_utc` and `key_hash` keeps RU costs low (single-property
indexes are cheap; full `/*` indexing on a high-write container is wasteful).

TTL = 365 days. Older usage gets garbage-collected automatically. If you ever
want to keep monthly aggregates for longer, write a once-a-month rollup job
into a separate `00_usage_monthly` container.

---

## 4. Implementation — the metering middleware

One decorator in `packages/auth/src/fbl_auth/metering.py` that wraps every
tool. The tool registration in `mcp_server/app.py` calls `metered(tool_fn)`
instead of `tool_fn`. No per-tool changes.

```python
# packages/auth/src/fbl_auth/metering.py
from contextvars import ContextVar
from datetime import datetime, UTC

_ru_counter: ContextVar[float] = ContextVar("_ru_counter", default=0.0)

COMPUTE_UNITS = {
    "describe_fields":      0,
    "list_sectors":         0,
    "get_coverage":         1,
    "get_document":         1,
    "search_companies":     1,  # bumped to 2 in handler when page_size > 25
    "get_company_details":  2,
    "get_company_history":  3,
    "find_peers":           5,
    "get_cohort_summary":   5,
    "get_full_record":      5,
}

def metered(tool_name: str):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(ctx, *args, **kwargs):
            _ru_counter.set(0.0)
            t0 = time.perf_counter()
            try:
                result = await fn(ctx, *args, **kwargs)
                error_kind = None
            except RateLimitError:
                error_kind = "rate_limited"; raise
            except AuthError:
                error_kind = "auth_failed"; raise
            except Exception:
                error_kind = "tool_error"; raise
            finally:
                ru = _ru_counter.get()
                units = _resolve_units(tool_name, kwargs)  # handles page_size etc.
                _emit(ctx.key_hash, tool_name, ru, units, error_kind)
            return result
        return wrapper
    return deco
```

The Cosmos client side: every `read_item` / `query_items` call wrapped by our
`fbl_core.storage.cosmos` already exposes `response.headers['x-ms-request-charge']`.
A tiny helper:

```python
# packages/core/src/fbl_core/storage/cosmos.py — additions
def _account_ru(charge: str | float | None) -> None:
    try:
        _ru_counter.set(_ru_counter.get() + float(charge or 0))
    except (TypeError, ValueError):
        pass

# inside every read/query:
#   resp = self._container.read_item(...)
#   _account_ru(self._container.client_connection.last_response_headers.get('x-ms-request-charge'))
#   return resp
```

`_emit` does the upsert into `00_usage`:

```python
def _emit(key_hash, tool, ru, units, error_kind):
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    doc_id = f"u_{key_hash}_{day}"
    # Cosmos `patch_item` is atomic — multiple parallel calls won't race.
    cosmos.patch(
        container="00_usage",
        item=doc_id,
        partition_key=key_hash,
        operations=[
          {"op": "incr", "path": "/calls", "value": 1},
          {"op": "incr", "path": "/compute_units", "value": units},
          {"op": "incr", "path": "/ru_consumed",   "value": ru},
          {"op": "incr", "path": f"/by_tool/{tool}/calls", "value": 1},
          {"op": "incr", "path": f"/by_tool/{tool}/compute_units", "value": units},
          {"op": "incr", "path": f"/by_tool/{tool}/ru_consumed",   "value": ru},
          *( [{"op": "incr", "path": f"/errors/{error_kind}", "value": 1}] if error_kind else [] ),
          {"op": "set",  "path": "/last_call_at", "value": now_utc_z()},
        ],
        # On first call of the day, the doc doesn't exist → fall back to upsert.
    )
```

(Cosmos patch with "incr" on a missing path auto-creates with the value;
combined with a fallback upsert on 404 it handles the first-call-of-day case.)

### 4.1 Performance

- Cosmos `patch` is ~5 RU per call. With ~1,000 free-tier users hitting 100
  calls/day each, that's 100k patches/day = 500k RU = ~$15/month on
  serverless. Linear, predictable.
- The `contextvars` counter is in-process and free.
- The middleware adds ~5-10 ms per call (the patch round-trip). Acceptable.

### 4.2 Race conditions

Cosmos `patch_item` with `incr` ops is **atomic per-document**, so two parallel
calls by the same user (same `key_hash`, same day → same doc) increment safely.
The race we don't need to worry about: two users → two different docs (different
partition keys), no contention.

---

## 5. Rate limiting

The existing rate limit ([packages/auth/src/fbl_auth/oauth.py](../packages/auth/src/fbl_auth/oauth.py)
and request middleware) counts per-IP burst limits. We extend it with a
per-key **daily soft-cap on `compute_units`**:

```python
def check_daily_quota(cosmos, key_hash) -> None:
    day = today_utc()
    doc = cosmos.get("00_usage", f"u_{key_hash}_{day}", partition_key=key_hash) or {}
    used = doc.get("compute_units", 0)
    cap  = QUOTA[doc.get("tier", "free")]    # FREE_DAILY_CAP = 1000
    if used >= cap:
        raise RateLimitError(
            f"daily quota exhausted ({used}/{cap} compute_units). "
            f"Resets at 00:00 UTC. Tools used: {top_3_tools(doc)}."
        )
```

The cap is **soft**: the request that takes you *over* still succeeds (you
get the answer you asked for), the *next* one is blocked until the rollover.
This is friendlier than hard-cutting mid-call.

`QUOTA = {"free": 1000, "paid_1": 10000, "paid_2": 100000, "owner": float('inf')}`
— centralised, env-overridable.

---

## 6. MCP exposure — `get_my_usage`

One new tool, auth-protected, calls only own data:

```jsonc
// Request
{ "tool": "get_my_usage", "args": { "window": "today" | "yesterday" | "month_to_date" | "last_30_days" } }

// Response
{
  "window": "today",
  "key_label": "key-ending-in-...8e3a",   // last 4 chars; no full key
  "tier": "free",
  "daily_quota": 1000,
  "totals": {
    "calls": 1842,
    "compute_units": 3127,
    "compute_units_remaining_today": 0,   // negative if you went over
    "bytes_out": 8204321
  },
  "by_tool": {
    "search_companies":     { "calls": 612, "compute_units": 612 },
    "get_company_details":  { "calls": 980, "compute_units": 1960 },
    ...
  },
  "errors": { "rate_limited": 12, "auth_failed": 0, "tool_error": 3 },
  "first_call_at": "2026-07-01T00:14:22Z",
  "last_call_at":  "2026-07-01T23:48:51Z"
}
```

Deliberately excludes `ru_consumed` (Azure-internal, would confuse).

---

## 7. Owner-scope tools (admin only)

Authentication: a header `X-Owner-Key` against an env-stored owner secret.

| Tool | What it does |
|---|---|
| `admin_list_users` | Roll-up of all keys: total calls / compute_units / RU per key, last 30 days. Sorted by spend. |
| `admin_get_user(key_hash)` | Per-user 90-day history (calls + compute_units + RU per day). |
| `admin_top_tools` | Which tools drive the most RUs / compute_units across all users. |
| `admin_set_quota(key_hash, tier)` | Bump a user from free → paid_1, etc. |

These are **not** exposed via the public MCP server. They run on a separate
admin endpoint (e.g. `https://admin.agentic-firmenbuch.at/...` — IP-restricted
to your home / VPN, or behind a different OAuth flow).

---

## 8. Privacy

- **No e-mail, no IP, no User-Agent** in the usage doc. Only the SHA-256
  truncated key hash.
- Mapping `key_hash → email` exists only in the `00_accounts` container
  (already there for signup), and joining it requires owner-scope. The
  metering doc on its own is GDPR-anonymous.
- TTL = 365 days. After a year the data disappears automatically.
- A user requesting account deletion via `/api/unsubscribe` triggers:
  1. delete their `00_accounts` doc
  2. their `00_usage/*` docs auto-expire (TTL); we leave them alone
     because deleting them on demand would be expensive O(daily_docs)
     point-deletes. The audit trail "key X used N tools in July 2026"
     remains, anonymous.

If a stricter "delete on request" is required, the cleanup runs as part
of the unsubscribe flow:

```python
cosmos.query("00_usage", "SELECT * FROM c WHERE c.partition_key=@pk", params={"@pk": key_hash})
for doc in result:
    cosmos.delete(doc.id, partition_key=key_hash)
```

That's ~N point-deletes for an unsubscribing user, N ≤ 365. Cheap enough.

---

## 9. Migration / backfill

There is no historical metering data and that's fine — the docs only need
to exist forward from day 1. The current `~6` active users (per the audit
on 2026-06-27) will simply start accruing data on day 1 of the meter.

---

## 10. Open questions

1. **`bytes_out` worth tracking?** Cheap (we already compute response size
   for logging). Useful if we ever bill by bandwidth. Default: yes, track it.
2. **Per-hour granularity instead of per-day?** Cosmos write cost ~24x for
   24x finer-grained writes — only worth it if hourly trend analysis becomes
   a feature. Default: no, daily is enough.
3. **Sample full responses for traffic analysis?** Storing full response
   bodies (even at 1% sampling) would balloon storage. Default: no — the
   metering layer should NOT see the response payload, only its size.
4. **Show the quota in MCP describe_fields?** Yes — `describe_fields` should
   include a `quota` block so any agent connecting can introspect what limits
   apply to its key. (Free clients sometimes hit a wall at unexpected times;
   advertising the limit avoids confusion.)
5. **Quota grace period for first-time users?** A "free first 7 days at 10x
   the normal quota" tier might smooth onboarding. Defer to launch + 14 days
   data.

---

## 11. Build order

| Phase | Scope | Effort |
|---|---|---|
| 1 | Add `00_usage` container in Bicep; add `metered()` decorator; wrap every existing MCP tool. Track `calls` + `compute_units`. Skip RU accounting for V1. | 1 d |
| 2 | Add Cosmos RU accounting via header read; wrap every Cosmos read in `fbl_core.storage.cosmos`. | 0.5 d |
| 3 | Add `get_my_usage` MCP tool. Add quota check in middleware. | 0.5 d |
| 4 | Admin tools on separate endpoint. | 1 d |
| 5 | Quota tiers + paid-tier bump. | TBD when monetisation lands |

**Phase 1 + 2 + 3 = ~2 days** to go from zero metering to full per-user
visibility. Phase 4 (admin) is small but separate.

---

## 12. What this does *not* cover

- **Operational metrics** (latency, error rates, replica health) → that's
  Application Insights, already wired up.
- **Aggregate platform metrics** (total queries/sec, hottest companies,
  user-funnel analytics) → separate `00_metrics` container already exists
  for `playground_queries` / `signups_verified`, can be extended.
- **Billing system integration** (Stripe webhooks, invoicing) → out of scope
  for V1. The meter produces the *inputs* a billing system would consume.
