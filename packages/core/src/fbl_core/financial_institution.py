"""Heuristic classifier: is a company a regulated financial institution (bank / insurer)?

Banks keep their books under the **BWG** (§§43-58), insurers under the **VAG** (§§136-167) —
a different balance-sheet scheme than the **UGB** (§§224/231) that the Firmenbuch JAb-4.0 XML
models. The Firmenbuch only built a structured XML pipe for UGB, so banks/insurers file their
Jahresabschluss as a **PDF** and our UGB pipeline extracts no figures for them. A consumer that
doesn't know this reads the empty UGB numbers as "no data" (or worse, compares an ordinary GmbH
against a bank). This flag says: *regulated FI — UGB figures are absent by construction, look at
the official document instead*.

The signal is **legal form + name keywords**. It is a deliberate HEURISTIC: the authoritative
activity classification (GISA / NACE) is not yet wired (ROADMAP P3). The result records
``source="heuristic"`` so a later authoritative pass can supersede it. Erring slightly toward
false positives is acceptable — the flag only suppresses UGB-ratio expectations and points to the
source filing; it never deletes or alters data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Legal forms that ARE a regulated FI by definition.
_FORM_KIND = {
    "SPA": "bank",  # Sparkasse
    "VER": "insurer",  # Versicherungsverein auf Gegenseitigkeit (VVaG)
}

# Unambiguous bank/insurer name tokens (substring match, already lower-cased).
_INSURER_TERMS = (
    "versicherung",
    "rückversicherung",
    "rueckversicherung",
    "assicurazioni",
    "insurance",
    "wechselseitige",
)
_BANK_TERMS = (
    "sparkasse",
    "volksbank",
    "raiffeisenbank",
    "raiffeisen-bank",
    "raiffeisenkasse",
    "kreditinstitut",
    "bausparkasse",
    "hypothekenbank",
    "landesbank",
    "privatbank",
    "bankhaus",
)

# Compounds where "bank" is NOT a credit institution — guard the generic ``\bbank\b`` match.
_BANK_FALSE_FRIENDS = (
    "datenbank",
    "werkbank",
    "blutbank",
    "samenbank",
    "genbank",
    "fleischbank",
    "schaubank",
    "spielbank",  # casino
    "wortbank",
)
_BANK_WORD = re.compile(r"\bbank\b")


@dataclass(frozen=True)
class FinancialInstitution:
    """Classification result. ``kind`` is ``"bank"`` or ``"insurer"``."""

    kind: str
    source: str  # "legal_form" | "name" — which signal fired (both → "legal_form")

    @property
    def caveat(self) -> str:
        scheme = "BWG (§§43-58)" if self.kind == "bank" else "VAG (§§136-167)"
        what = "Bank" if self.kind == "bank" else "Versicherung"
        return (
            f"{what}: Rechnungslegung nach {scheme}, nicht UGB. Strukturierte UGB-Kennzahlen "
            "liegen daher nicht vor; der amtliche Jahresabschluss ist als PDF einzusehen."
        )


def classify_financial_institution(
    legal_form: str | None, name: str | None
) -> FinancialInstitution | None:
    """Return a :class:`FinancialInstitution` if *legal_form* / *name* indicate a regulated
    bank or insurer, else ``None``. Pure and side-effect-free (heuristic, ROADMAP P2.1)."""
    form = (legal_form or "").strip().upper()
    if form in _FORM_KIND:
        return FinancialInstitution(kind=_FORM_KIND[form], source="legal_form")

    n = (name or "").lower()
    if not n:
        return None
    if any(t in n for t in _INSURER_TERMS):
        return FinancialInstitution(kind="insurer", source="name")
    if any(t in n for t in _BANK_TERMS):
        return FinancialInstitution(kind="bank", source="name")
    if _BANK_WORD.search(n) and not any(ff in n for ff in _BANK_FALSE_FRIENDS):
        return FinancialInstitution(kind="bank", source="name")
    return None
