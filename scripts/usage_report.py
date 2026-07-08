"""Daily usage dashboard — signups, per-account requests, tool mix — printed to the terminal.

Read-only over ``00_accounts``. Run any day to see who is using the service and how much:

    COSMOS_ENDPOINT=https://cosmos-firmenbuch-xbjux2hw.documents.azure.com:443/ \
    COSMOS_DATABASE=firmenbuch \
    uv run python scripts/usage_report.py

Auth to Cosmos is DefaultAzureCredential (your ``az login``). Add ``--html report.html`` to
also write a simple HTML page you can open in the browser.

What is tracked (and what is NOT): per account we store the total request count, today's count,
the LAST tool used, and the last-used timestamp — plus a daily "verified signups" counter. We do
**not** store the natural-language query text (privacy by design); "which queries" therefore means
"which tools" (search_companies / get_company_details / …), not the questions themselves. For a
per-tool daily histogram, add per-tool counters in record_usage (see the report footer).
"""

from __future__ import annotations

import argparse
import html as _html
from datetime import UTC, datetime

from fbl_auth.accounts import ACCOUNTS_CONTAINER
from fbl_core.config import get_settings
from fbl_core.storage import CosmosStore


def _rows(cosmos: CosmosStore, today: str) -> tuple[list[dict], int, dict[str, int]]:
    accounts: list[dict] = []
    signups_today = 0
    tools: dict[str, int] = {}
    for d in cosmos.iter_all(ACCOUNTS_CONTAINER):
        if str(d.get("id", "")).startswith("__"):
            continue
        kind = d.get("kind")
        if kind == "pending_signup":
            if (d.get("created_at") or "").startswith(today):
                signups_today += 1
            continue
        if kind:  # ip_throttle / invite_code / other non-account docs
            continue
        accounts.append(d)
        lt = (d.get("usage") or {}).get("last_tool")
        if lt:
            tools[lt] = tools.get(lt, 0) + 1
    accounts.sort(key=lambda x: (x.get("usage") or {}).get("total", 0), reverse=True)
    return accounts, signups_today, tools


def _today_count(u: dict, today: str) -> int:
    return int(u.get("day_count", 0)) if u.get("day_window") == today else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily usage report over 00_accounts.")
    ap.add_argument("--html", metavar="PATH", help="also write an HTML report to PATH")
    args = ap.parse_args()

    settings = get_settings()
    if not settings.cosmos_endpoint:
        raise SystemExit("COSMOS_ENDPOINT is not set.")
    cosmos = CosmosStore(settings.cosmos_endpoint, settings.cosmos_database)
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    accounts, signups_today, tools = _rows(cosmos, today)
    total_reqs = sum((a.get("usage") or {}).get("total", 0) for a in accounts)
    reqs_today = sum(_today_count(a.get("usage") or {}, today) for a in accounts)
    by_tier: dict[str, int] = {}
    for a in accounts:
        by_tier[a.get("tier", "free")] = by_tier.get(a.get("tier", "free"), 0) + 1

    print(f"\n  AGENTIC-FIRMENBUCH — Nutzungsreport  ({today})")
    print("  " + "=" * 68)
    print(f"  Konten gesamt: {len(accounts)}   |   Anmeldungen heute: {signups_today}")
    print(f"  Requests gesamt: {total_reqs}   |   Requests heute: {reqs_today}")
    print("  Konten nach Plan: " + ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())))
    print("  " + "-" * 68)
    print(f"  {'E-Mail':34} {'Plan':6} {'ges':>5} {'heute':>5}  letztes Tool / zuletzt")
    for a in accounts:
        u = a.get("usage") or {}
        email = (a.get("email") or "(leer)")[:34]
        last = f"{u.get('last_tool') or '-'}  {u.get('last_used_at') or '-'}"
        print(
            f"  {email:34} {a.get('tier', 'free'):6} {u.get('total', 0):>5} "
            f"{_today_count(u, today):>5}  {last}"
        )
    print("  " + "-" * 68)
    print("  Tool-Nutzung (nach letztem Tool je Konto):")
    for t, c in sorted(tools.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c}")
    print("  Hinweis: Query-TEXTE werden aus Datenschutzgründen NICHT gespeichert —")
    print("  sichtbar sind Request-Zahlen + eingesetzte Tools, nicht die Fragen.\n")

    if args.html:
        _write_html(args.html, today, accounts, signups_today, tools, total_reqs, reqs_today)
        print(f"  HTML-Report geschrieben: {args.html}\n")


def _write_html(
    path: str,
    today: str,
    accounts: list[dict],
    signups_today: int,
    tools: dict[str, int],
    total_reqs: int,
    reqs_today: int,
) -> None:
    def td(x: object) -> str:
        return f"<td>{_html.escape(str(x))}</td>"

    rows = ""
    for a in accounts:
        u = a.get("usage") or {}
        rows += (
            "<tr>"
            + td(a.get("email") or "(leer)")
            + td(a.get("tier", "free"))
            + td(u.get("total", 0))
            + td(_today_count(u, today))
            + td(u.get("last_tool") or "-")
            + td(u.get("last_used_at") or "-")
            + "</tr>"
        )
    toolrows = "".join(
        f"<tr>{td(t)}{td(c)}</tr>" for t, c in sorted(tools.items(), key=lambda x: -x[1])
    )
    doc = f"""<!doctype html><meta charset=utf-8><title>Nutzungsreport {today}</title>
<style>body{{font-family:system-ui,sans-serif;background:#0b0d10;color:#e8ebf0;margin:24px}}
h1{{color:#19C37D}}table{{border-collapse:collapse;margin:14px 0;width:100%}}
td,th{{border:1px solid #263042;padding:6px 10px;text-align:left;font-size:14px}}
th{{color:#9aa2af;text-transform:uppercase;font-size:11px;letter-spacing:.08em}}
.k{{display:inline-block;margin-right:24px;font-size:15px}}.k b{{color:#19C37D;font-size:22px}}</style>
<h1>Agentic-Firmenbuch — Nutzungsreport</h1><p>{today}</p>
<div><span class=k>Konten <b>{len(accounts)}</b></span><span class=k>Anmeldungen heute <b>{signups_today}</b></span>
<span class=k>Requests gesamt <b>{total_reqs}</b></span><span class=k>Requests heute <b>{reqs_today}</b></span></div>
<h3>Konten</h3><table><tr><th>E-Mail<th>Plan<th>Requests ges.<th>heute<th>letztes Tool<th>zuletzt</tr>{rows}</table>
<h3>Tool-Nutzung</h3><table><tr><th>Tool<th>Konten</tr>{toolrows}</table>
<p style="color:#6b7280;font-size:13px">Query-Texte werden aus Datenschutzgründen nicht gespeichert.</p>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)


if __name__ == "__main__":
    main()
