"""ESVG (ESA 2010) sector key — code → label + a coarse FI kind.

Generated verbatim from the official OeNB legend
(`docs/reference/oenb/Sektor_ESVG_SL_Schluessel_2026-06-28.xlsx`, retrieved 2026-06-28).
The OeNB MFI/NMFI lists carry this code in the `E-VGR` column; it is the authoritative
meaning of an institution's sector (e.g. 1220A = banks, 1280* = insurers, 1290 = pension
funds). Do not hand-edit — regenerate from the source file if OeNB updates the key.
"""

from __future__ import annotations

# Schlüssel → Bezeichnung (verbatim from the official ESVG sector key).
ESVG_LABELS: dict[str, str] = {
    "1100": "Nicht-finanzielle Unternehmen",
    "1210": "Zentralbank",
    "1220A": "MFIs - CRD - MiRe-pflichtig",
    "1220B": "MFIs - Nicht-CRD, other MFI's",
    "1220C": "CRD - nicht MiRe-pflichtig",
    "1220Z": "Kreditinstitute (MFI) nicht zuordenbar",
    "1230A": "Geldmarktfonds",
    "1240B": "Rentenfonds",
    "1240C": "Sonstige Fonds",
    "1240D": "Immobilienfonds",
    "1240E": "Aktienfonds",
    "1240F": "Hedgefonds",
    "1240G": "gemischte Fonds",
    "1240Z": "Investmentfonds nicht zuordenbar",
    "1250B": "Mitarbeitervorsorgekassen",
    "1250C": "Clearinghäuser",
    "1250D": "FVC's nach EZB",
    "1250E": "Finanzleasinggesellschaften",
    "1250F": "Security and Derivate Dealers (SDDS) klassifiziert als KI nach EZB",
    "1250G": "Sonstige Security and Derivate Dealers (SDDS) nach EZB",
    "1250H": "Factoringeinheiten",
    "1250I": "Sonstige Financial Corporations engaged in Lending (FCLs) nach EZB",
    "1250J": "Wohnbaubanken als nicht-MFIs",
    "1250K": "Sonstige spezielle Kapitalgesellschaften",
    "1250Z": "Sonstige Finanzinstitute nicht zuordenbar",
    "1260A": "Kredit- und Versicherungshilfstätigkeiten",
    "1260B": "Finanzielle Head-offices",
    "1270A": "Firmeneigene Finanzinstitute und Kapitalgeber ohne Privatstiftungen (Holdings)",
    "1270B": "Privatstiftungen nach dem Privatstiftungsgesetz",
    "1270C": "Sparkassenstiftungen (Anteilsverwaltungen)",
    "1270E": "Pfandhäuser mit Kreditvergabe",
    "1270F": "Ausländische Nicht-Holdinggesellschaften",
    "1270Z": "Firmeneigene Finanzierungseinrichtungen und Kapitalgeber nicht zuordenbar",
    "1280": "Versicherungsgesellschaften",
    "1280A": "Re-Insurance undertaking - Rückversicherungen",
    "1280B": "Life insurance undertaking - Lebensversicherungen",
    "1280C": "Non life insurance undertaking - Nichtlebensversicherung",
    "1280D": "Composite undertaking - Gemischte Versicherungen",
    "1280Z": "Versicherungsgesellschaften",
    "1290": "Pensionskassen (Alterssicherungssysteme)",
    "1300Z": "Staat nicht zuordenbar",
    "1311": "Zentralstaat",
    "1312": "Länder (inkl. Landeskammern, Landesfonds)",
    "1313": "Gemeinden (inkl. Gemeindefonds und -verbände)",
    "1314": "Sozialversicherung",
    "1400A": "Selbständigenhaushalte (mit u.ohne Arbeitnehmer)",
    "1400B": "sonstige private Haushalte",
    "1400Z": "private Haushalte nicht zuordenbar",
    "1500": "Private Organisationen ohne Erwerbszweck",
    "9999": "Nicht zuordenbar",
}


def esvg_label(code: str | None) -> str | None:
    """Official Bezeichnung for an E-VGR/ESVG sector code (None if unknown/empty)."""
    if not code:
        return None
    return ESVG_LABELS.get(code.strip())


def esvg_kind(code: str | None) -> str:
    """Coarse financial-institution kind from the ESVG sector code (the `E-VGR` column).
    Banks (1210/1220*), insurers (1280*), pension funds (1290 + 1250B Vorsorgekassen),
    investment funds (1230/1240*), and the remaining S.125/S.126/S.127 financial
    institutions. Anything outside S.12 (e.g. 1100) → \"other\"."""
    c = (code or "").strip()
    if c.startswith("1210") or c.startswith("1220"):
        return "bank"
    if c.startswith("1280"):
        return "insurer"
    if c == "1290":
        return "pensionskasse"
    if c == "1250B":
        return "vorsorgekasse"
    if c.startswith("1230") or c.startswith("1240"):
        return "fund"
    if c.startswith("1250") or c.startswith("1260") or c.startswith("1270"):
        return "other_financial"
    return "other"
