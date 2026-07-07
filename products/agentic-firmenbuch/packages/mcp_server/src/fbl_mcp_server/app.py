"""FastMCP app + auth-enforcing service wrapper (§8.9).

``McpService`` is the testable core: it validates the token, enforces the rate limit,
meters usage, then delegates to the read functions in ``service.py``. ``build_app``
wires those onto a FastMCP server (the transport is not unit-tested here).
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context, FastMCP

from fbl_auth import (
    Account,
    check_rate_limit,
    get_usage,
    is_privileged,
    quota_for,
    record_metered_usage,
    record_usage,
    validate,
    validate_bearer,
)
from fbl_core.config import Settings, get_settings
from fbl_core.storage import BlobStore, BlobStoreLike, CosmosStoreLike
from fbl_core_at.models import SearchFilters, Sort

from . import plans, service
from .errors import RateLimited, Unauthorized
from .oauth_app import _OAuthChallenge, register_oauth_endpoints

# FastMCP injects a tool param annotated as the bare `Context` class (its matcher needs a class,
# not a parameterized generic). mypy --strict wants the type args, so alias it: at type-check time
# it's the fully-parameterized generic; at runtime it's the bare class FastMCP detects.
if TYPE_CHECKING:
    ToolContext = Context[Any, Any, Any]
else:
    ToolContext = Context


class McpService:
    """Auth + rate limit + metering in front of the read tools."""

    def __init__(
        self,
        cosmos: CosmosStoreLike,
        settings: Settings | None = None,
        blob: BlobStoreLike | None = None,
    ) -> None:
        self._cosmos = cosmos
        self._settings = settings or get_settings()
        # Blob is needed only by get_document (to mint a SAS download link). Built lazily from the
        # configured account URL when not injected; stays None offline/in tests → get_document
        # degrades to metadata only. The BlobStore opens no connection until first used.
        if blob is None and self._settings.blob_account_url:
            blob = BlobStore(self._settings.blob_account_url)
        self._blob = blob

    def _authorize(self, token: str, tool: str) -> Account:
        # Two credential kinds resolve to the same Account: an X-API-Key (legacy header
        # path) OR an OAuth Bearer token (Cowork/claude.ai, §8.10b). Try API key first
        # since most live traffic still uses it; fall back to bearer.
        account = validate(token, self._cosmos) or validate_bearer(self._cosmos, token)
        if account is None:
            raise Unauthorized("invalid or unknown token")
        # Privileged accounts (owner override, independent of Account.tier) get Pro-level
        # quota too, so a stale/free tier on record never rate-limits them either.
        privileged = is_privileged(account.email, self._settings)
        quota_tier = plans.PLAN_LEGACY if privileged else account.tier
        per_min, per_day = quota_for(quota_tier, self._settings)
        decision = check_rate_limit(account, per_min=per_min, per_day=per_day)
        if not decision.allowed:
            raise RateLimited(decision.reason or "rate limited")
        record_usage(account, tool, self._cosmos)  # rolling counters (rate limit)
        # Persistent daily-rollup meter (Erweiterungen-Spec §8). Best-effort: a metering write must
        # never fail a tool call — the rate-limit counters above are authoritative.
        with contextlib.suppress(Exception):
            record_metered_usage(account, tool, self._cosmos)
        return account

    # --- plan feature gating (Free vs full-access plans; see fbl_mcp_server.plans) ----------

    def _plan(self, account: Account) -> str:
        """The plan actually in force for this account (an expired guest reverts to free).

        Privileged accounts (owner override, e.g. Thomas Gaar / coachfident.com) always resolve
        to full access here regardless of the stored ``tier`` — this is the one place ALL
        feature gating reads the plan from, so it can't be bypassed by a stale/wrong tier.
        """
        if is_privileged(account.email, self._settings):
            return plans.PLAN_LEGACY
        return plans.effective_plan(account.tier, account.plan_expires_at)

    def _free_details_this_month(self, account: Account) -> int:
        """Month-to-date ``get_company_details`` call count for this key (drives the free cap)."""
        usage = get_usage(self._cosmos, account.token_hash, window="month_to_date")
        stat = usage.get("by_tool", {}).get("get_company_details", {})
        return int(stat.get("calls", 0))

    def search_companies(
        self,
        token: str,
        filters: SearchFilters | None = None,
        sort: Sort | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        account = self._authorize(token, "search_companies")
        resp = service.search_companies(self._cosmos, filters, sort, page, page_size).model_dump(
            mode="json"
        )
        # Free plan: search stays open but each card is flattened to basic fields.
        if not plans.is_full_access(self._plan(account)):
            resp = plans.flatten_free_search_response(resp)
        return resp

    def get_company_details(self, token: str, fnr: str) -> dict[str, Any]:
        account = self._authorize(token, "get_company_details")
        # Free plan: cap full profiles per month. ``_authorize`` has already metered THIS call,
        # so the month-to-date count includes it; allow exactly ``cap`` successes by blocking
        # once the count exceeds the cap.
        if not plans.is_full_access(self._plan(account)):
            cap = self._settings.free_details_per_month
            if self._free_details_this_month(account) > cap:
                return plans.gate_details_cap(cap, self._settings.upgrade_url)
        return service.get_company_details(self._cosmos, fnr)

    def _gate_pro_only(self, account: Account, tool: str) -> dict[str, Any] | None:
        """Return an upgrade gate if this Pro-only tool is called on the free plan, else None."""
        if plans.is_full_access(self._plan(account)):
            return None
        return plans.gate_pro_only(tool, self._settings.upgrade_url)

    def describe_fields(self, token: str) -> dict[str, Any]:
        """Static catalog of every field the server can return, by tool tier (§9)."""
        self._authorize(token, "describe_fields")
        return service.describe_fields()

    def get_company_history(
        self, token: str, fnr: str, metrics: list[str] | None = None
    ) -> dict[str, Any]:
        account = self._authorize(token, "get_company_history")
        gate = self._gate_pro_only(account, "get_company_history")
        if gate is not None:
            return gate
        return service.get_company_history(self._cosmos, fnr, metrics)

    def get_full_record(self, token: str, fnr: str) -> dict[str, Any]:
        """The complete consolidated/derived record — full superset, nothing reduced (§5.1)."""
        account = self._authorize(token, "get_full_record")
        gate = self._gate_pro_only(account, "get_full_record")
        if gate is not None:
            return gate
        return service.get_full_record(
            self._cosmos, fnr, expose_personal_data=self._settings.expose_personal_data
        )

    def get_document(self, token: str, doc_key: str) -> dict[str, Any]:
        account = self._authorize(token, "get_document")
        gate = self._gate_pro_only(account, "get_document")
        if gate is not None:
            return gate
        return service.get_document(self._cosmos, doc_key, self._blob)

    def list_sectors(self, token: str) -> dict[str, Any]:
        self._authorize(token, "list_sectors")
        return service.list_sectors(self._cosmos)

    def get_cohort_summary(self, token: str, dimension: str, value: str) -> dict[str, Any]:
        account = self._authorize(token, "get_cohort_summary")
        gate = self._gate_pro_only(account, "get_cohort_summary")
        if gate is not None:
            return gate
        return service.get_cohort_summary(self._cosmos, dimension, value)

    def find_peers(self, token: str, fnr: str, n: int = 10) -> dict[str, Any]:
        account = self._authorize(token, "find_peers")
        gate = self._gate_pro_only(account, "find_peers")
        if gate is not None:
            return gate
        return service.find_peers(self._cosmos, fnr, n)

    def get_coverage(self, token: str) -> dict[str, Any]:
        """Internal coverage dashboard (XML vs PDF-only vs none) — auth-restricted (§11).
        Served from the precomputed ``__stats__`` doc so it can't full-scan in-request."""
        self._authorize(token, "get_coverage")
        return service.coverage(self._cosmos)

    def get_my_usage(self, token: str, window: str = "today") -> dict[str, Any]:
        """The caller's own consumption over *window* (Erweiterungen-Spec §8.5). Reads only
        the key's own usage docs; never exposes another user's data or the e-mail behind it."""
        account = self._authorize(token, "get_my_usage")
        return dict(get_usage(self._cosmos, account.token_hash, window=window))


def _http_credential(ctx: Any) -> tuple[str, str]:
    """Return ``(kind, token)`` for the credential the client presented.

    Two paths produce the same Account downstream (§8.10b):
    * ``X-API-Key: <token>`` -- the existing header path (Claude Code, Copilot, Cursor).
    * ``Authorization: Bearer <token>`` -- the OAuth path (Cowork, claude.ai), validated
      against ``00_oauth_tokens`` instead of ``00_accounts``.

    ``kind`` is one of ``"api_key"``, ``"bearer"``, or ``""`` (unauthenticated).
    Headers are case-insensitive (Starlette).
    """
    try:
        request = ctx.request_context.request
    except Exception:
        return "", ""  # no HTTP request context (e.g. stdio transport)
    if request is None:
        return "", ""
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        return "api_key", str(api_key)
    auth = request.headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return "bearer", auth[7:].strip()
    return "", ""


def _http_token(ctx: Any) -> str:
    """Backwards-compatible: return whichever credential the client presented as a string.
    McpService._authorize knows to try X-API-Key first then bearer (see ``McpService``)."""
    _, token = _http_credential(ctx)
    return token


def build_app(cosmos: CosmosStoreLike, settings: Settings | None = None) -> Any:
    """Construct the FastMCP server with all tools registered (§9)."""
    svc = McpService(cosmos, settings)
    # Public base URL of THIS MCP host (used for OAuth issuer below + the absolute icon src).
    base_url = os.environ.get("PUBLIC_BASE_URL", "https://mcp.agentic-firmenbuch.at").rstrip("/")
    # Bind 0.0.0.0 so the Container App ingress can reach the streamable-HTTP server
    # (FastMCP defaults to 127.0.0.1, which is unreachable from outside the container).
    # website_url + icons travel in the MCP serverInfo so a spec-aware client renders our brand
    # (the green grid) and links to the site instead of showing a generic placeholder (issue #14).
    from mcp.types import Icon

    mcp = FastMCP(
        "firmenbuch-live",
        host="0.0.0.0",
        port=int(os.environ.get("MCP_PORT", "8000")),
        website_url="https://www.agentic-firmenbuch.at",
        icons=[
            Icon(src=f"{base_url}/icon.png", mimeType="image/png", sizes=["512x512"]),
            Icon(src=f"{base_url}/favicon.svg", mimeType="image/svg+xml", sizes=["any"]),
        ],
    )

    # Serve the brand mark from the MCP domain too — the favicon path is the most widely
    # honored fallback across clients that don't yet read serverInfo.icons.
    @mcp.custom_route("/favicon.svg", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _favicon_svg(_request: Any) -> Any:
        from starlette.responses import Response

        from .branding import FAVICON_SVG

        return Response(
            FAVICON_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route("/icon.png", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _icon_png(_request: Any) -> Any:
        from starlette.responses import Response

        from .branding import ICON_PNG

        return Response(
            ICON_PNG,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route("/favicon.ico", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _favicon_ico(_request: Any) -> Any:
        # Browsers/clients that blindly request /favicon.ico get the PNG (valid; clients sniff).
        from starlette.responses import Response

        from .branding import ICON_PNG

        return Response(
            ICON_PNG,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @mcp.custom_route("/.well-known/glama.json", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _glama_wellknown(_request: Any) -> Any:
        # Domain-ownership proof for the Glama MCP registry connector claim: publishing this
        # file on the server's domain verifies we control it. The maintainer email is env-driven
        # (GLAMA_MAINTAINER_EMAIL) so it can be changed without a rebuild.
        import json as _json

        from starlette.responses import Response

        email = os.environ.get("GLAMA_MAINTAINER_EMAIL", "jakobneugebauer@pm.me")
        body = _json.dumps(
            {
                "$schema": "https://glama.ai/mcp/schemas/connector.json",
                "maintainers": [{"email": email}],
            }
        )
        return Response(
            body,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Friendly landing for humans who open the bare host in a browser. The MCP
    # protocol itself lives at ``/mcp`` (a bare GET there correctly returns 406);
    # without this, ``GET /`` would 404 with an unhelpful "Not Found".
    @mcp.custom_route("/", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _root(_request: Any) -> Any:
        from starlette.responses import HTMLResponse

        return HTMLResponse(
            "<!doctype html><html lang=de><meta charset=utf-8>"
            "<title>Agentic-Firmenbuch.at — MCP-Server</title>"
            "<body style='font-family:system-ui,sans-serif;max-width:42rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.6;color:#1a1a1a'>"
            "<h1>Agentic-Firmenbuch.at — MCP-Server</h1>"
            "<p>Das ist der <strong>MCP-Endpunkt</strong>, kein Website. Er ist für "
            "KI-Tools (Claude, Cursor, Copilot …) gedacht, nicht für den Browser.</p>"
            "<p>Verbinde dein Tool mit <code>https://mcp.agentic-firmenbuch.at/mcp</code> "
            "und dem Header <code>X-API-Key</code>.</p>"
            "<p>→ <a href='https://www.agentic-firmenbuch.at/onboarding.html'>Anleitung &amp; "
            "API-Key anfordern</a></p>"
            "</body></html>",
            status_code=200,
        )

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def _health(_request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok"})

    # --- MCP OAuth 2.1 (§8.10b) ----------------------------------------------------------
    # These endpoints let clients that cannot use the X-API-Key header (Claude Cowork,
    # claude.ai) attach by URL + login. Discovery + DCR are live now; /authorize and
    # /token follow in phase 3.

    # The authorization-base URL is the host root with the MCP path stripped. The metadata
    # endpoint MUST live at the root per RFC 8414 / MCP spec — and Cowork won't even try
    # the URL if this 404s.
    _base = base_url
    from fbl_auth import email_sender_from_settings

    _settings = settings or get_settings()
    _email = email_sender_from_settings(_settings)

    # OAuth 2.1 discovery / DCR / authorize / token endpoints (§8.10b) live in oauth_app.py
    # to keep this file focused on the MCP tools.
    register_oauth_endpoints(mcp, cosmos, _base, _email)

    # The API key comes from the X-API-Key connection header (see _http_token); it is NOT a
    # tool argument, so the agent never has to know or pass it (and it never leaks into a
    # tool-call payload). `ctx: Context` is injected by FastMCP and excluded from the schema.
    #
    # Every tool here is READ-ONLY: it queries our served snapshot and never writes, mutates,
    # or calls a third party. We declare that via MCP tool annotations so a spec-aware client
    # (and registries like Glama) can show "safe to call" without parsing the prose.
    from mcp.types import ToolAnnotations

    readonly = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    @mcp.tool(annotations=readonly)
    def search_companies(
        ctx: ToolContext,
        filters: SearchFilters | None = None,
        sort: Sort | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """Find or look up Austrian companies by name — START HERE for any company lookup.

        This is the primary entry point: search/find/look up companies by NAME (substring) or
        filter by location, industry, legal form, size or financials, ranked. Whenever the user
        names a company or wants a list of companies, call THIS tool first (you do not need the
        Firmenbuchnummer/FNR to search by name). Read-only.

        Parameters:
        - filters (optional): a SearchFilters object; every field is optional and AND-combined
          (name substring; legal_form; bundesland; size_gkl W/K/M/G; bilanzsumme / revenue /
          equity_ratio / employees min+max ranges; growth_profile; has_guv; last_filing_year_min;
          founded_year_min/max; gf_age_min; status active/inactive/all). Omit for an unfiltered
          list. Full-name inputs like "Wien" or "GmbH" are mapped to stored codes automatically.
        - sort (optional): {field, descending}; field in {bilanzsumme, revenue, equity_ratio,
          employees, last_filing_year}; default bilanzsumme descending. Companies missing the sort
          field sort last.
        - page (optional, default 1) and page_size (optional, default 25, clamped to 1..100).

        Returns the total match count plus a COMPACT summary card per company on the page (name,
        legal_form, bundesland, size, Bilanzsumme, equity ratio, revenue, growth, has_guv, industry)
        — NOT the full record. Use to find/rank companies; for one company's full profile call
        get_company_details, for the complete record call get_full_record, for aggregates over a
        whole group call get_cohort_summary.
        Field reference: https://www.agentic-firmenbuch.at/felder.html
        """
        return svc.search_companies(_http_token(ctx), filters, sort, page, page_size)

    @mcp.tool(annotations=readonly)
    def get_company_details(ctx: ToolContext, fnr: str) -> dict[str, Any]:
        """Full served profile for one company by FNR. Read-only.

        Parameter:
        - fnr (required): Firmenbuchnummer, e.g. "123456a" (the `fnr` from a search card).

        Returns one company's identity, location, financials (per-year Bilanz + GuV), all computed
        ratios, growth, employees, filings, and management. Use when you already know the company
        (from search_companies); for the complete record (full position taxonomy, unknown-code
        passthrough, per-year lineage) use get_full_record; for specific metric trends use
        get_company_history.
        Field reference: https://www.agentic-firmenbuch.at/felder.html
        """
        return svc.get_company_details(_http_token(ctx), fnr)

    @mcp.tool(annotations=readonly)
    def describe_fields(ctx: ToolContext) -> dict[str, Any]:
        """Catalog of every field the server can return, by tool tier (search card -> full
        profile -> full record), with code tables and availability/null rules. Read-only,
        no parameters.

        This describes the SCHEMA only (field names, types, code tables, null rules) so you can
        pick the right tool and interpret its output. It returns no company data itself; for that
        call search_companies (many) or get_company_details / get_full_record (one). Call this
        once up front when unsure what a field means or which tool to use.
        Human-readable version: https://www.agentic-firmenbuch.at/felder.html"""
        return svc.describe_fields(_http_token(ctx))

    @mcp.tool(annotations=readonly)
    def get_company_history(
        ctx: ToolContext, fnr: str, metrics: list[str] | None = None
    ) -> dict[str, Any]:
        """Per-metric multi-year time series for one company. Read-only.

        Parameters:
        - fnr (required): Firmenbuchnummer, e.g. "123456a" (the `fnr` from a search card).
        - metrics (optional): list of metric names to return, e.g.
          ["bilanzsumme", "umsatzerloese", "eigenkapital"]; omit to get every available series.
          The card alias "revenue" is accepted for "umsatzerloese".

        Returns, per requested metric, the yearly values (year -> value) plus latest/latest_year.
        Use when you need the trend of specific figures; for a full one-shot profile use
        get_company_details, for the complete record (all positions) use get_full_record.
        """
        return svc.get_company_history(_http_token(ctx), fnr, metrics)

    @mcp.tool(annotations=readonly)
    def get_full_record(ctx: ToolContext, fnr: str) -> dict[str, Any]:
        """Complete per-company record, the full superset we hold for one company. Read-only.

        Parameter:
        - fnr (required): Firmenbuchnummer, e.g. "123456a" (the `fnr` from a search card).

        Returns everything: every position's full per-year history, unknown-code passthrough
        (nothing dropped), completeness flags, guv_years, and lineage (§5.1). This is the heaviest
        read. Use it when you need raw completeness; for the normal curated profile use
        get_company_details, for a single metric's trend use get_company_history.
        """
        return svc.get_full_record(_http_token(ctx), fnr)

    @mcp.tool(annotations=readonly)
    def get_document(ctx: ToolContext, doc_key: str) -> dict[str, Any]:
        """Get a time-limited download link to a company's official Jahresabschluss document.
        Read-only.

        Parameter:
        - doc_key (required): a filing's `document_ref` ("{fnr}:{stichtag}") from
          get_company_details, a bare FNR (-> latest filing), or a legacy doc_key.

        Returns `download.url`, a short-lived signed link (open it, don't expect bytes inline), plus
        the `financial_institution` flag + caveat for banks/insurers, whose figures live only in the
        PDF. `download` is null if nothing is ingested for that filing. Use to fetch the original
        filing artifact; for the already-parsed figures use get_company_details / get_full_record
        instead of downloading."""
        return svc.get_document(_http_token(ctx), doc_key)

    @mcp.tool(annotations=readonly)
    def list_sectors(ctx: ToolContext) -> dict[str, Any]:
        """Valid filter values for search_companies, with company counts. Read-only, no parameters.

        Returns the legal-form (Rechtsform) codes and the size-class (`gkl`: W/K/M/G) values
        present in the served dataset, each with its count. Call this first to discover the real
        `legal_form` / `size_gkl` values to pass to search_companies or get_cohort_summary, instead
        of guessing codes. For region/format coverage instead, use get_coverage.
        """
        return svc.list_sectors(_http_token(ctx))

    @mcp.tool(annotations=readonly)
    def get_cohort_summary(ctx: ToolContext, dimension: str, value: str) -> dict[str, Any]:
        """Aggregate statistics for a cohort of companies. Read-only.

        Parameters:
        - dimension (required): which axis defines the cohort, one of "gkl" (size class),
          "bundesland" (federal state), or "legal_form" (Rechtsform). The search-filter alias
          "size_gkl" is accepted for "gkl".
        - value (required): the cohort value on that axis, e.g. dimension="bundesland",
          value="Wien" (full name or the code "W" both work); dimension="gkl", value="M".
          Use list_sectors to see valid legal_form / gkl values.

        Returns cohort counts plus distribution statistics (e.g. Bilanzsumme median; the exact
        median is skipped for very large cohorts to keep the request fast), NOT per-company rows.
        Use for "what does group X look like in aggregate"; for the individual companies use
        search_companies, for one company use get_company_details.
        """
        return svc.get_cohort_summary(_http_token(ctx), dimension, value)

    @mcp.tool(annotations=readonly)
    def find_peers(ctx: ToolContext, fnr: str, n: int = 10) -> dict[str, Any]:
        """Find the companies most similar in size to a given one. Read-only.

        Parameters:
        - fnr (required): the reference company's Firmenbuchnummer, e.g. "123456a".
        - n (optional, default 10, clamped to 1..50): how many peers to return.

        Returns up to `n` companies in the SAME size class (`gkl`) as the reference, nearest to it
        by Bilanzsumme (closest first), each as a compact card like search_companies. The reference
        company itself is excluded. Returns an empty list if the FNR is unknown or has no
        Bilanzsumme to rank against. Use for "companies comparable to X"; for arbitrary filtered
        lists use search_companies, for group aggregates use get_cohort_summary.
        """
        return svc.find_peers(_http_token(ctx), fnr, n)

    @mcp.tool(annotations=readonly)
    def get_coverage(ctx: ToolContext) -> dict[str, Any]:
        """How much of the register we actually serve, as an aggregate dashboard. Read-only,
        no parameters.

        Returns coverage counts broken down by data availability — companies with parsed XML
        financials vs PDF-only (linked but not machine-readable) vs none — and by format/status, so
        you can gauge what share of the universe has usable financials. It is a dataset-wide
        overview (served O(1) from a precomputed stats doc), NOT per-company data, no filters. Use
        for "how complete is the data"; for the valid filter values use list_sectors, for one
        group's aggregate figures use get_cohort_summary.
        """
        return svc.get_coverage(_http_token(ctx))

    @mcp.tool(annotations=readonly)
    def get_my_usage(ctx: ToolContext, window: str = "today") -> dict[str, Any]:
        """Your own API-key usage: call count and weighted compute-units, per tool. Read-only.

        Parameter:
        - window (optional, default "today"): one of "today", "yesterday", "month_to_date",
          "last_30_days", "all".

        Returns only the calling key's own usage (totals + per-tool breakdown) for that window;
        never another user's data and never the e-mail behind the key. Use to check your own
        consumption against the free-tier rate limits."""
        return svc.get_my_usage(_http_token(ctx), window)

    return mcp


def build_asgi_app(cosmos: CosmosStoreLike, settings: Settings | None = None) -> Any:
    """The production ASGI app: the FastMCP streamable-HTTP transport wrapped so unauthenticated
    ``/mcp`` requests trigger OAuth discovery (see ``_OAuthChallenge``). This is what
    ``__main__`` serves with uvicorn; tests drive it directly via Starlette's TestClient."""
    mcp = build_app(cosmos, settings)
    base = os.environ.get("PUBLIC_BASE_URL", "https://mcp.agentic-firmenbuch.at")
    return _OAuthChallenge(mcp.streamable_http_app(), base, cosmos)
