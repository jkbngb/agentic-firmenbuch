"""Runtime configuration & feature flags (Technische Spezifikation §10).

Values come from environment variables (``.env`` locally, Key Vault in Azure).
Nothing here requires network access at import time, so it is safe for unit tests.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings, populated from the environment (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Firmenbuch HVD API ---
    justizonline_api_url: str = "https://justizonline.gv.at/jop/api/at.gv.justiz.fbw/ws"
    firmenbuch_api_key: str | None = None

    # --- Azure data plane (Managed Identity in Azure; endpoints only, no keys) ---
    cosmos_endpoint: str | None = None
    cosmos_database: str = "firmenbuch"
    blob_account_url: str | None = None
    acs_connection_string: str | None = None
    acs_sender_address: str | None = None  # verified ACS sender, e.g. DoNotReply@<domain>
    appinsights_connection_string: str | None = None
    # Where pipeline ops alerts go (directory-sync failures etc.). Override with ALERT_EMAIL.
    alert_email: str = "jakobneugebauer@pm.me"

    # --- Distribution / signup (Distribution Spez §4, §6) ---
    turnstile_secret: str | None = (
        None  # Cloudflare Turnstile server-side secret; None = skip verify
    )
    site_base_url: str = "https://agentic-firmenbuch.at"  # base for verify/unsubscribe links
    # Public base URL of THIS API (the container). Used to build the verify link in emails so
    # it hits the reachable API host, not the static site (whose /api/* has no backend). Falls
    # back to site_base_url when empty (e.g. when a same-origin proxy is configured).
    api_public_url: str = ""
    # Comma-separated browser origins allowed to call this API cross-site (signup + playground
    # fetch from the static site). Empty → the production defaults wired in api/asgi.py.
    cors_allowed_origins: str = ""
    verify_token_ttl_hours: int = 24  # double-opt-in verify link lifetime
    signup_ip_limit_per_min: int = 5  # /api/signup throttle per IP per minute

    # --- Playground (Distribution §13) ---
    playground_enabled: bool = True  # kill-switch: False → /api/playground returns 503
    playground_llm_enabled: bool = False  # True → Claude does tool-calling; False → rule-based
    playground_per_visitor_day: int = 10  # messages/visitor/day
    playground_per_ip_day: int = 30  # messages/IP/day
    playground_global_day: int = 2000  # global daily message cap (spend guard)
    playground_max_results: int = 8  # output-length limit per answer
    # LLM-mode config. Cheap model + a tight max_tokens keep cost bounded; combined with the
    # daily caps above this is the playground spend guard. Key is server-side only (Key Vault).
    anthropic_api_key: str | None = None
    playground_llm_model: str = "claude-haiku-4-5-20251001"  # cheapest current Claude
    playground_llm_max_tokens: int = 900  # cap per answer (cost + output-length guard)

    log_level: str = "INFO"

    # --- Feature flags (config, not code) ---
    growth_horizons: list[int] = Field(default_factory=lambda: [1, 3, 5])
    enable_deterministic_summary: bool = False
    enable_observations: bool = False
    expose_personal_data: bool = False  # GDPR gate for officer names
    rate_limit_per_min: int = 60  # the "free" tier (default when a tier has no override)
    rate_limit_per_day: int = 5000
    # Per-tier quota overrides as [per_min, per_day]; "free" falls back to the two above
    # so a paid tier is purely a config change (§8.10). Override via TIER_QUOTAS env (JSON).
    # guest + legacy get Pro-level rate limits (guest = 14-day trial; legacy = grandfathered
    # existing keys). Feature gating (which tools, free monthly cap) is separate — see
    # fbl_mcp_server.plans — only "free" is feature-limited; every other plan is full-access.
    tier_quotas: dict[str, list[int]] = Field(
        default_factory=lambda: {
            "pro": [600, 100_000],
            "guest": [600, 100_000],
            "legacy": [600, 100_000],
            "enterprise": [3_000, 1_000_000],
        }
    )

    # --- Privileged accounts (config, not code) ---
    # Emails/domains that always get full (``legacy``-equivalent) access regardless of what
    # ``Account.tier`` says or whether they've ever paid — e.g. the owner's own test/partner
    # accounts. Checked in fbl_auth.accounts.is_privileged, applied in McpService (plan +
    # rate-limit quota), so it survives any future migration/signup bug that leaves the stored
    # tier wrong. Override via PRIVILEGED_EMAILS / PRIVILEGED_EMAIL_DOMAINS env (JSON list).
    privileged_emails: list[str] = Field(
        default_factory=lambda: [
            "thomas.gaar@gmail.com",
            "thomas.gaar@coachfident.com",
            "team@coachfident.com",
            "jakobneugebauer@pm.me",
        ]
    )
    privileged_email_domains: list[str] = Field(default_factory=lambda: ["coachfident.com"])

    # --- Billing / plan feature gates (config, not code) ---
    # Free plan: monthly cap on full company profiles (get_company_details). The search stays
    # usable (flattened card). Override via FREE_DETAILS_PER_MONTH. Chosen from real usage:
    # casual testers who tried once ran ~6-22 total calls, few of them full profiles.
    free_details_per_month: int = 10
    # Where a gated free response points the user to upgrade. No dedicated pricing page yet;
    # onboarding covers key + plans. Override via UPGRADE_URL. (Set to /preise.html in Phase 4.)
    upgrade_url: str = "https://www.agentic-firmenbuch.at/onboarding.html"

    # --- Stripe (billing). TEST mode first; keys ONLY via ENV / Key Vault, never in repo. ---
    stripe_secret_key: str | None = None  # sk_test_… (test) / sk_live_… (go-live)
    stripe_webhook_secret: str | None = None  # whsec_… (from the webhook endpoint config)
    stripe_price_lookup_key: str = "pro_monthly"  # resolves to the Pro price id at request time
    stripe_trial_days: int = 14  # 14-day free trial on checkout (subscription_data)
    billing_success_url: str = "https://www.agentic-firmenbuch.at/willkommen.html"
    billing_cancel_url: str = "https://www.agentic-firmenbuch.at/onboarding.html"
    billing_portal_return_url: str = "https://www.agentic-firmenbuch.at/onboarding.html"
    schema_version: str = "1.0"
    metrics_version: str = "1.0"

    # --- Operational (pipeline scheduling / concurrency, §15a) ---
    daily_cron: str = "0 3 * * *"
    hvd_max_requests_per_sec: int = 5
    ingest_workers: int = 8
    run_lock_ttl_sec: int = 14400
    delta_mode: str = "change_feed"  # change_feed | rolling_rescan
    rolling_rescan_days: int = 14
    # change_feed: how many days back each daily run re-checks (overlap). A small overlap
    # catches late-arriving feed entries; set high for a one-time catch-up after a backfill
    # (e.g. 10) so nothing changed since the raw load is missed. The monthly full grind is
    # the completeness backstop (§15a.1). Env: DELTA_LOOKBACK_DAYS.
    delta_lookback_days: int = 3
    registry_sync_cron: str = "0 2 * * 0"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
