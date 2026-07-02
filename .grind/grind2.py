#!/usr/bin/env python
"""ÖNACE v2 re-grind (#34): classify unique Geschäftszweig TEXTS at 2008 CLASS level,
audit before anything is served, then write the v2 `industry` block to every company.

Principles enforced here (docs/classification/README.md):
  P1  the LLM emits 4-digit 2008 classes; everything after is the deterministic
      class-level crosswalk + label lookups (build_industry_block)
  P2  one classification per unique normalised text — same text, same code, always
  P3  the frequent head is frozen into a reviewable lexicon (--phase freeze-lexicon)
  P4  every emitted code is validated against the official tree
  P5  audit gate (distribution plausibility) blocks the upload phase on red
  P6  golden.json regression gate blocks the upload phase on red

Phases (each resumable via journal files; run them in order, or --phase all):
  fetch           pull fnr + Geschäftszweig (+name) for every served company -> input2.jsonl
  classify        LLM over unique texts (batch 25, 16 workers)               -> texts2.jsonl
  audit           P5 + P6 gates over texts2.jsonl; writes audit2.json; FAILS LOUDLY
  upload          patch /industry onto every company, drop legacy /branch    -> uploaded2.jsonl
  names           abstention-capable name-only pass for companies w/o text   -> names2.jsonl
  upload-names    patch the confident name classifications (source: llm, from: name)
  freeze-lexicon  write the head (count >= 10) into fbl_core data + a review file

Usage:
  caffeinate -dims uv run python .grind/grind2.py --phase all
  uv run python .grind/grind2.py --phase classify --limit 200   # smoke test
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from azure.cosmos import CosmosClient

from fbl_core.classification.crosswalk import map_class
from fbl_core.classification.industry import build_industry_block
from fbl_core.classification.taxonomy import load_oenace_tree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
INPUT = HERE / "input2.jsonl"  # every company: {fnr, gz, name}
TEXTS = HERE / "texts2.jsonl"  # classified unique texts: {key, gz, cls08}
AUDIT = HERE / "audit2.json"
UPLOADED = HERE / "uploaded2.jsonl"  # {fnr}
NAMES = HERE / "names2.jsonl"  # {fnr, name, cls08|null}
UPNAMES = HERE / "uploaded_names2.jsonl"
GOLDEN = HERE / "golden.json"
LEXICON_OUT = (
    ROOT / "packages/core/src/fbl_core/classification/data/oenace/geschaeftszweig_lexicon.json"
)
REVIEW_OUT = HERE / "lexicon_review.md"

MODEL = "claude-sonnet-4-6"
BATCH = 25
WORKERS = 16
ACCOUNT, RG = "cosmos-firmenbuch-xbjux2hw", "rg-firmenbuch-prod"
DB, CONTAINER = "firmenbuch", "10_presentation"
PRICE = {"in": 3.0, "out": 15.0, "cache_w": 3.75, "cache_r": 0.30}

t08 = load_oenace_tree(2008)
t25 = load_oenace_tree(2025)
_CATALOG = "\n".join(f"{n.code} {n.title_de}" for _, n in t08.nodes.items() if n.level == 4)

# Generic prompt — identical wording to v1, only the catalogue is CLASS level (P1).
SYSTEM_TEXT = [
    {
        "type": "text",
        "text": (
            "Du klassifizierst österreichische Firmen nach ÖNACE 2008 anhand ihres "
            "Geschäftszweigs. Wähle für jede Firma GENAU EINEN Code aus der offiziellen "
            "Klassenliste unten (Format 'DD.DD'), erfinde keine. Bei mehreren Tätigkeiten "
            'die Haupttätigkeit. Antworte NUR mit JSON: {"<idx>":"DD.DD",...}.'
            f"\n\nÖNACE-2008-KLASSEN:\n{_CATALOG}"
        ),
        "cache_control": {"type": "ephemeral"},
    }
]
# Name pass: abstention is mandatory — a name is only evidence when it names the trade.
SYSTEM_NAME = [
    {
        "type": "text",
        "text": (
            "Du klassifizierst österreichische Firmen nach ÖNACE 2008 anhand NUR ihres "
            "Firmennamens. Gib einen Code aus der Klassenliste unten (Format 'DD.DD') NUR "
            "dann, wenn der Name die Tätigkeit EINDEUTIG erkennen lässt (z. B. 'Müller "
            "Transporte GmbH', 'Bäckerei Huber GmbH'). Reine Namens-/Holding-/Fantasie-"
            "Bezeichnungen ('Huber GmbH', 'ALPHA Beteiligungs GmbH' NUR wenn eindeutig "
            "Beteiligung) bekommen null. Im Zweifel IMMER null. Antworte NUR mit JSON: "
            '{"<idx>":"DD.DD" oder null,...}.'
            f"\n\nÖNACE-2008-KLASSEN:\n{_CATALOG}"
        ),
        "cache_control": {"type": "ephemeral"},
    }
]

# P5 distribution gate: no 2025 group may exceed this share of classified companies,
# except structurally huge Austrian populations (Immo-GmbHs, holdings, consulting, trade).
SHARE_LIMIT = 0.10
SHARE_WHITELIST = {"68.2", "68.3", "64.2", "70.2", "46.8", "47.7", "41.0"}

_stop = threading.Event()
_lock = threading.Lock()
_spent = 0.0


class CreditExhausted(Exception):
    pass


def norm(text: str) -> str:
    """P2 equivalence key: casefold + whitespace collapse (content untouched)."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def cost(u: object) -> float:
    g = lambda k: float(getattr(u, k, 0) or 0)  # noqa: E731
    return (
        g("input_tokens") * PRICE["in"]
        + g("output_tokens") * PRICE["out"]
        + g("cache_creation_input_tokens") * PRICE["cache_w"]
        + g("cache_read_input_tokens") * PRICE["cache_r"]
    ) / 1_000_000


def first_obj(txt: str) -> dict[str, Any]:
    s = txt.find("{")
    if s < 0:
        return {}
    d = 0
    for i in range(s, len(txt)):
        if txt[i] == "{":
            d += 1
        elif txt[i] == "}":
            d -= 1
            if d == 0:
                try:
                    obj: dict[str, Any] = json.loads(txt[s : i + 1])
                    return obj
                except Exception:
                    return {}
    return {}


def anthropic_client() -> Anthropic:
    key = next(
        (
            line.split("=", 1)[1].strip().strip('"').strip("'")
            for line in (ROOT / ".env").read_text().splitlines()
            if line.startswith("ANTHROPIC_API_KEY=")
        ),
        "",
    )
    return Anthropic(api_key=key)


def cosmos_container():
    cos_key = subprocess.check_output(
        ["az", "cosmosdb", "keys", "list", "-n", ACCOUNT, "-g", RG,
         "--query", "primaryMasterKey", "-o", "tsv"], text=True,
    ).strip()
    return (
        CosmosClient(f"https://{ACCOUNT}.documents.azure.com:443/", cos_key)
        .get_database_client(DB)
        .get_container_client(CONTAINER)
    )


def jread(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# --------------------------------------------------------------------------- fetch
def phase_fetch(container) -> None:
    if INPUT.exists():
        print(f"fetch: {INPUT.name} existiert ({sum(1 for _ in INPUT.open())} Zeilen) — skip")
        return
    q = (
        "SELECT c.fnr, c.company.description AS gz, c.identity.name AS name "
        "FROM c WHERE NOT STARTSWITH(c.id, '__')"
    )
    n = 0
    with INPUT.open("w") as f:
        for row in container.query_items(q, enable_cross_partition_query=True):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
            if n % 50000 == 0:
                print(f"  fetch {n}…", flush=True)
    print(f"fetch: {n} Firmen -> {INPUT.name}")


# ------------------------------------------------------------------------ classify
def _classify_batch(client: Anthropic, system: list[dict[str, Any]], items: list[str]) -> dict[str, str | None]:
    lines = "\n".join(f'{i}: "{t}"' for i, t in enumerate(items))
    for attempt in range(4):
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=3000, system=system,
                messages=[{"role": "user", "content": f"Klassifiziere diese Firmen:\n{lines}"}],
            )
            break
        except Exception as e:  # noqa: BLE001
            if any(s in str(e).lower() for s in ("credit balance", "billing", "insufficient")):
                raise CreditExhausted(str(e)) from e
            if attempt == 3:
                print(f"  ! classify failed: {e}", file=sys.stderr)
                return {}
            time.sleep(2 * (attempt + 1))
    raw = first_obj(next((b.text for b in msg.content if b.type == "text"), ""))
    global _spent
    with _lock:
        _spent += cost(msg.usage)
    out: dict[str, str | None] = {}
    for i in range(len(items)):
        v = raw.get(str(i))
        out[str(i)] = str(v).strip() if v else None
    return out


def phase_classify(client: Anthropic, limit: int = 0) -> None:
    companies = jread(INPUT)
    texts: dict[str, str] = {}
    for c in companies:
        gz = (c.get("gz") or "").strip()
        if gz:
            texts.setdefault(norm(gz), gz)
    done = {r["key"] for r in jread(TEXTS)}
    todo = [(k, v) for k, v in texts.items() if k not in done]
    if limit:
        todo = todo[:limit]
    print(f"classify: {len(texts)} unique Texte, {len(done)} fertig, {len(todo)} offen")
    if not todo:
        return
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    out = TEXTS.open("a")
    n = 0
    t0 = time.time()

    def work(batch: list[tuple[str, str]]) -> list[dict[str, Any]]:
        res = _classify_batch(client, SYSTEM_TEXT, [gz for _, gz in batch])
        recs = []
        for i, (key, gz) in enumerate(batch):
            cls = res.get(str(i))
            cls = cls if cls and t08.is_valid(cls) and t08.get(cls) and t08.get(cls).level == 4 else None  # type: ignore[union-attr]
            recs.append({"key": key, "gz": gz, "cls08": cls})
        return recs

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        it = iter(batches)
        futs = {}
        for _ in range(WORKERS * 2):
            b = next(it, None)
            if b is None:
                break
            futs[ex.submit(work, b)] = b
        while futs:
            fut = next(as_completed(list(futs)))
            futs.pop(fut)
            try:
                recs = fut.result()
            except CreditExhausted as e:
                print(f"\n⛔ Anthropic-Guthaben aufgebraucht: {e}", file=sys.stderr)
                _stop.set()
                recs = []
            for r in recs:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
            n += len(recs)
            if n % (BATCH * 40) == 0 and n:
                rate = n / max(time.time() - t0, 1)
                eta = (len(todo) - n) / max(rate, 0.1) / 60
                print(f"  {len(done) + n}/{len(texts)}  ${_spent:.2f}  ETA ~{eta:.0f} min", flush=True)
            if not _stop.is_set():
                b = next(it, None)
                if b is not None:
                    futs[ex.submit(work, b)] = b
    out.close()
    print(f"classify: fertig bis auf Abbrüche; ${_spent:.2f} ausgegeben")


# --------------------------------------------------------------------------- audit
def phase_audit() -> bool:
    companies = jread(INPUT)
    text_cls = {r["key"]: r["cls08"] for r in jread(TEXTS)}
    counts: Counter[str] = Counter()
    unclassified = 0
    classified_companies = 0
    for c in companies:
        gz = (c.get("gz") or "").strip()
        if not gz:
            continue
        cls = text_cls.get(norm(gz))
        if not cls:
            unclassified += 1
            continue
        g25 = map_class(cls)
        if not g25:
            unclassified += 1
            continue
        counts[g25] += 1
        classified_companies += 1

    total = max(classified_companies, 1)
    violations = [
        (g, n, n / total)
        for g, n in counts.most_common()
        if n / total > SHARE_LIMIT and g not in SHARE_WHITELIST
    ]
    golden = json.loads(GOLDEN.read_text())["cases"]
    golden_fail = []
    for text, want in golden.items():
        cls = text_cls.get(norm(text))
        got = map_class(cls) if cls else None
        if got != want:
            golden_fail.append({"text": text, "want": want, "got": got, "cls08": cls})

    report = {
        "companies_with_text": sum(1 for c in companies if (c.get("gz") or "").strip()),
        "classified": classified_companies,
        "unclassified_texts": unclassified,
        "top25_groups": [
            {"group": g, "label": t25.title(g), "companies": n, "share": round(n / total, 4)}
            for g, n in counts.most_common(25)
        ],
        "share_violations": [
            {"group": g, "label": t25.title(g), "companies": n, "share": round(s, 4)}
            for g, n, s in violations
        ],
        "golden_failures": golden_fail,
    }
    AUDIT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report["top25_groups"][:10], ensure_ascii=False, indent=1))
    ok = not violations and not golden_fail
    if violations:
        print(f"⛔ P5 VERTEILUNGS-ALARM: {report['share_violations']}", file=sys.stderr)
    if golden_fail:
        print(f"⛔ P6 GOLDEN-SET ROT: {golden_fail}", file=sys.stderr)
    print(f"audit: {'GRÜN — Upload freigegeben' if ok else 'ROT — Upload GESPERRT'} (audit2.json)")
    return ok


# -------------------------------------------------------------------------- upload
def _patch(container, fnr: str, industry: dict[str, Any] | None) -> None:
    ops: list[dict[str, Any]] = []
    if industry is not None:
        ops.append({"op": "set", "path": "/industry", "value": industry})
    ops.append({"op": "remove", "path": "/branch"})
    try:
        container.patch_item(item=fnr, partition_key=fnr, patch_operations=ops)
    except Exception:
        # /branch may not exist (post-v1 docs) — retry without the remove
        if industry is not None:
            container.patch_item(
                item=fnr, partition_key=fnr,
                patch_operations=[{"op": "set", "path": "/industry", "value": industry}],
            )


def phase_upload(container) -> None:
    if not phase_audit():
        print("⛔ upload verweigert: Audit ist rot (P5/P6).", file=sys.stderr)
        sys.exit(2)
    companies = jread(INPUT)
    text_cls = {r["key"]: r["cls08"] for r in jread(TEXTS)}
    # Only companies uploaded WITH a real code count as done. A company uploaded with a
    # null block (its text was not yet classified at the time) is deliberately NOT skipped
    # on resume — otherwise it would keep serving null after its text gets classified.
    done = {r["fnr"] for r in jread(UPLOADED) if r.get("coded")}
    # one block per unique text (P2), shared across its companies
    block_cache: dict[str, dict[str, Any] | None] = {}
    todo = []
    for c in companies:
        if c["fnr"] in done:
            continue
        gz = (c.get("gz") or "").strip()
        if not gz:
            continue  # name pass handles these
        key = norm(gz)
        if key not in block_cache:
            block_cache[key] = build_industry_block(gz, text_cls.get(key), "llm")
        todo.append((c["fnr"], block_cache[key]))
    print(f"upload: {len(done)} fertig (mit Code), {len(todo)} offen")
    out = UPLOADED.open("a")
    n = 0
    t0 = time.time()

    def work(item: tuple[str, dict[str, Any] | None]) -> tuple[str, bool]:
        fnr, block = item
        _patch(container, fnr, block)
        return fnr, bool(block and block.get("oenace"))

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fnr, coded in ex.map(work, todo):
            out.write(json.dumps({"fnr": fnr, "coded": coded}) + "\n")
            n += 1
            if n % 10000 == 0:
                out.flush()
                rate = n / max(time.time() - t0, 1)
                print(f"  {n}/{len(todo)}  {rate:.0f}/s", flush=True)
    out.close()
    print(f"upload: {n} Firmen aktualisiert")


# --------------------------------------------------------------------------- names
def phase_names(client: Anthropic, limit: int = 0) -> None:
    companies = jread(INPUT)
    done = {r["fnr"] for r in jread(NAMES)}
    todo = [
        c for c in companies
        if not (c.get("gz") or "").strip() and (c.get("name") or "").strip() and c["fnr"] not in done
    ]
    if limit:
        todo = todo[:limit]
    print(f"names: {len(todo)} Firmen ohne Geschäftszweig offen (Enthaltung erlaubt)")
    if not todo:
        return
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    out = NAMES.open("a")
    n = 0

    def work(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        res = _classify_batch(client, SYSTEM_NAME, [c["name"] for c in batch])
        recs = []
        for i, c in enumerate(batch):
            cls = res.get(str(i))
            cls = cls if cls and t08.is_valid(cls) and t08.get(cls) and t08.get(cls).level == 4 else None  # type: ignore[union-attr]
            recs.append({"fnr": c["fnr"], "name": c["name"], "cls08": cls})
        return recs

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        it = iter(batches)
        futs = {}
        for _ in range(WORKERS * 2):
            b = next(it, None)
            if b is None:
                break
            futs[ex.submit(work, b)] = b
        while futs:
            fut = next(as_completed(list(futs)))
            futs.pop(fut)
            try:
                recs = fut.result()
            except CreditExhausted as e:
                print(f"\n⛔ Guthaben aufgebraucht: {e}", file=sys.stderr)
                _stop.set()
                recs = []
            for r in recs:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
            n += len(recs)
            if n % (BATCH * 40) == 0:
                print(f"  names {n}/{len(todo)}  ${_spent:.2f}", flush=True)
            if not _stop.is_set():
                b = next(it, None)
                if b is not None:
                    futs[ex.submit(work, b)] = b
    out.close()
    assigned = sum(1 for r in jread(NAMES) if r.get("cls08"))
    print(f"names: fertig; {assigned} mit Code, Rest Enthaltung (bleibt null)")


def phase_upload_names(container) -> None:
    done = {r["fnr"] for r in jread(UPNAMES)}
    todo = [r for r in jread(NAMES) if r.get("cls08") and r["fnr"] not in done]
    print(f"upload-names: {len(todo)} offen")
    out = UPNAMES.open("a")

    def work(r: dict[str, Any]) -> str:
        block = build_industry_block(None, r["cls08"], "llm", classified_from="name")
        _patch(container, r["fnr"], block)
        return str(r["fnr"])

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        n = 0
        for fnr in ex.map(work, todo):
            out.write(json.dumps({"fnr": fnr}) + "\n")
            n += 1
            if n % 5000 == 0:
                out.flush()
                print(f"  {n}/{len(todo)}", flush=True)
    out.close()
    print("upload-names: fertig")


# ------------------------------------------------------------------ freeze-lexicon
def phase_freeze_lexicon(min_count: int = 10) -> None:
    companies = jread(INPUT)
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for c in companies:
        gz = (c.get("gz") or "").strip()
        if gz:
            k = norm(gz)
            counts[k] += 1
            display.setdefault(k, gz)
    text_cls = {r["key"]: r["cls08"] for r in jread(TEXTS)}
    head = {k: text_cls.get(k) for k, n in counts.items() if n >= min_count and text_cls.get(k)}
    lex = {
        "_meta": {
            "source": "v2 re-grind head texts (count >= %d), P3 verified lexicon" % min_count,
            "coverage_companies": sum(counts[k] for k in head),
            "entries": len(head),
        },
        "text_to_class_2008": dict(sorted(head.items())),
    }
    LEXICON_OUT.write_text(json.dumps(lex, ensure_ascii=False, indent=1))
    with REVIEW_OUT.open("w") as f:
        f.write("# Lexikon-Review: Top-Texte (Anzahl, Code, Label)\n\n")
        f.write("| Firmen | Geschäftszweig | 2008-Klasse | ÖNACE 2025 | Label |\n|---|---|---|---|---|\n")
        for k, n in counts.most_common(150):
            cls = text_cls.get(k)
            g25 = map_class(cls) if cls else None
            f.write(
                f"| {n} | {display[k][:60]} | {cls or '-'} | {g25 or '-'} | "
                f"{(t25.title(g25) or '')[:45] if g25 else '-'} |\n"
            )
    cov = lex["_meta"]["coverage_companies"]
    print(f"lexicon: {len(head)} Texte, deckt {cov} Firmen; Review: {REVIEW_OUT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all",
                    choices=["fetch", "classify", "audit", "upload", "names",
                             "upload-names", "freeze-lexicon", "all"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    needs_cosmos = args.phase in ("fetch", "upload", "upload-names", "all")
    needs_llm = args.phase in ("classify", "names", "all")
    container = cosmos_container() if needs_cosmos else None
    client = anthropic_client() if needs_llm else None

    if args.phase in ("fetch", "all"):
        phase_fetch(container)
    if args.phase in ("classify", "all"):
        phase_classify(client, args.limit)
    if args.phase == "audit":
        sys.exit(0 if phase_audit() else 2)
    if args.phase in ("upload", "all"):
        phase_upload(container)  # runs the audit gate itself
    if args.phase in ("names", "all"):
        phase_names(client, args.limit)
    if args.phase in ("upload-names", "all"):
        phase_upload_names(container)
    if args.phase in ("freeze-lexicon", "all"):
        phase_freeze_lexicon()
    print(f"GESAMT ausgegeben: ${_spent:.2f}")


if __name__ == "__main__":
    main()
