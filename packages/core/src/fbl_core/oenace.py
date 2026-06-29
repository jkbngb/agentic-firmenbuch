"""Deterministic ÖNACE-section classifier for the free-text Firmenbuch *Geschäftszweig* (issue #14).

The Firmenbuch carries no industry code — only a free-text activity description (84.8% coverage).
GISA has no ÖNACE either (verified 5 ways, see ``docs/branch_classification_analysis.md``), and WKO
is scraper-only. So branch must be *derived* from the free text.

This module is the **deterministic head** of the planned hybrid: a curated, ordered keyword
ruleset that maps the unambiguous, high-frequency German activity phrases onto the 21 ÖNACE-2008
**sections** (A–U). It returns a guess only when a rule matches — the long, ambiguous tail returns
``None`` and is left to the (offline, batch) LLM stage. No LLM, no network, pure function — cheap
to run over all ~340k companies. Output carries a confidence: ``high`` for a single clear section,
``medium`` when the text spans several sections (multi-activity, e.g. "Handel und Gastronomie").
"""

from __future__ import annotations

from pydantic import BaseModel

# The 21 ÖNACE-2008 / NACE Rev. 2 sections (Statistik Austria), code → German label.
OENACE_SECTIONS: dict[str, str] = {
    "A": "Land- und Forstwirtschaft, Fischerei",
    "B": "Bergbau und Gewinnung von Steinen und Erden",
    "C": "Herstellung von Waren",
    "D": "Energieversorgung",
    "E": "Wasserversorgung, Abwasser-/Abfallentsorgung",
    "F": "Bau",
    "G": "Handel; Instandhaltung und Reparatur von Kfz",
    "H": "Verkehr und Lagerei",
    "I": "Beherbergung und Gastronomie",
    "J": "Information und Kommunikation",
    "K": "Erbringung von Finanz- und Versicherungsdienstleistungen",
    "L": "Grundstücks- und Wohnungswesen",
    "M": "Freiberufliche, wissenschaftliche und technische Dienstleistungen",
    "N": "Sonstige wirtschaftliche Dienstleistungen",
    "O": "Öffentliche Verwaltung, Verteidigung, Sozialversicherung",
    "P": "Erziehung und Unterricht",
    "Q": "Gesundheits- und Sozialwesen",
    "R": "Kunst, Unterhaltung und Erholung",
    "S": "Sonstige Dienstleistungen",
    "T": "Private Haushalte",
    "U": "Exterritoriale Organisationen",
}

# Ordered keyword rules, SPECIFIC → GENERIC (first match wins for the primary section). Each entry
# is (section, keywords). Keys are matched as lowercase substrings against the Geschäftszweig.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # L — real estate (before generic "verwaltung"/"handel")
    (
        "L",
        (
            "immobilien",
            "liegenschaft",
            "grundstück",
            "bauträger",
            "bautraeger",
            "hausverwaltung",
            "wohnungswesen",
            "vermietung von grundstücken",
        ),
    ),
    # K — finance / holding / asset management (before generic "verwaltung")
    (
        "K",
        (
            "holding",
            "beteiligung",
            "vermögensverwaltung",
            "vermoegensverwaltung",
            "vermögensberatung",
            "vermoegensberatung",
            "finanzierung",
            "kreditvermittlung",
            "versicherung",
            "bankgeschäft",
            "leasing",
            "factoring",
            "vermögensberat",
        ),
    ),
    # I — hospitality / food service
    (
        "I",
        (
            "gastgewerbe",
            "gastronomie",
            "restaurant",
            "hotel",
            "beherbergung",
            "café",
            "cafe",
            "kaffeehaus",
            "catering",
            "imbiss",
            "bar betrieb",
            "pension",
        ),
    ),
    # M — professional / scientific / technical services
    (
        "M",
        (
            "steuerberat",
            "wirtschaftsprüf",
            "wirtschaftspruef",
            "unternehmensberat",
            "buchhaltung",
            "bilanzbuchhalt",
            "rechtsanwalt",
            "notar",
            "architekt",
            "ingenieur",
            "ziviltechnik",
            "werbung",
            "werbeagentur",
            "marketing",
            "public relations",
            "übersetzung",
            "uebersetzung",
            "design",
            "consulting",
            "beratung",
        ),
    ),
    # J — information & communication
    (
        "J",
        (
            "software",
            "hardware",
            "edv",
            "informationstechnolog",
            "programmier",
            "webdesign",
            "datenverarbeitung",
            "rechenzentrum",
            "telekommunikation",
            "film",
            "fernseh",
            "rundfunk",
            "verlag",
            "tonstudio",
            "medien",
            "app-entwicklung",
            " it-",
        ),
    ),
    # H — transport & storage
    (
        "H",
        (
            "transport",
            "spedition",
            "logistik",
            "fracht",
            "beförderung",
            "befoerderung",
            "taxi",
            "lagerei",
            "lagerung",
            "güterbeförderung",
            "personenbeförderung",
            "kurier",
        ),
    ),
    # F — construction & building trades
    (
        "F",
        (
            "baumeister",
            "baugewerbe",
            "baunebengewerbe",
            "hochbau",
            "tiefbau",
            "maurer",
            "zimmerei",
            "zimmermeister",
            "dachdecker",
            "installateur",
            "elektrotechnik",
            "elektroinstallat",
            "maler",
            "anstreicher",
            "fliesenleger",
            "spengler",
            "trockenbau",
            "pflasterer",
            "gerüst",
            "geruest",
            "bauträgergewerbe",
            "verputz",
            "estrich",
        ),
    ),
    # A — agriculture, forestry, fishing
    (
        "A",
        (
            "landwirtschaft",
            "forstwirtschaft",
            "forstbetrieb",
            "fischerei",
            "gärtnerei",
            "gaertnerei",
            "weinbau",
            "obstbau",
            "viehzucht",
            "imkerei",
            "tierzucht",
        ),
    ),
    # B — mining & quarrying
    ("B", ("bergbau", "steinbruch", "schottergewinnung", "kiesgewinnung", "gewinnung von steinen")),
    # D — energy supply
    (
        "D",
        (
            "energieversorgung",
            "stromerzeugung",
            "elektrizitätsversorgung",
            "wärmeversorgung",
            "waermeversorgung",
            "fernwärme",
            "fernwaerme",
            "gasversorgung",
            "kraftwerk",
            "photovoltaik",
            "stromhandel",
        ),
    ),
    # E — water / waste
    (
        "E",
        (
            "abwasser",
            "abfallentsorgung",
            "abfallwirtschaft",
            "müllentsorgung",
            "muellentsorgung",
            "recycling",
            "entsorgung",
            "wasserversorgung",
            "kläranlage",
            "klaeranlage",
        ),
    ),
    # Q — health & social
    (
        "Q",
        (
            "arztpraxis",
            "ärztlich",
            "aerztlich",
            "apotheke",
            "pflege",
            "therapie",
            "physiotherap",
            "krankenpflege",
            "gesundheits",
            "sozialwesen",
            "kindergarten",
            "altenheim",
            "tagesmutter",
            "ordination",
        ),
    ),
    # P — education
    (
        "P",
        (
            "schule",
            "bildung",
            "unterricht",
            "ausbildung",
            "nachhilfe",
            "fahrschule",
            "akademie",
            "kurse",
            "erwachsenenbildung",
            "sprachschule",
        ),
    ),
    # R — arts, entertainment, recreation, sport
    (
        "R",
        (
            "theater",
            "musik",
            "fitness",
            "tennis",
            "sportbetrieb",
            "fitnessstudio",
            "unterhaltung",
            "wettbüro",
            "wettbuero",
            "casino",
            "veranstaltung",
            "galerie",
            "museum",
            "freizeit",
            "sportverein",
            "tanzschule",
        ),
    ),
    # S — other services (personal)
    (
        "S",
        (
            "friseur",
            "kosmetik",
            "fußpflege",
            "fusspflege",
            "massage",
            "wäscherei",
            "waescherei",
            "reinigung",
            "textilreinigung",
            "bestattung",
            "tätowier",
            "taetowier",
            "perückenmacher",
            "perueckenmacher",
            "solarium",
        ),
    ),
    # N — other business services (employment, facility, rental, travel, security)
    (
        "N",
        (
            "arbeitskräfteüberlassung",
            "arbeitskraefteueberlassung",
            "personalbereitstellung",
            "gebäudereinigung",
            "gebaeudereinigung",
            "bewachung",
            "sicherheitsdienst",
            "gartenpflege",
            "reisebüro",
            "reisebuero",
            "verleih",
            "call center",
            "callcenter",
            "hausbetreuung",
            "schädlingsbekämpfung",
        ),
    ),
    # C — manufacturing (food trades + generic production; kept late so Handel/services win first)
    (
        "C",
        (
            "bäckerei",
            "baeckerei",
            "fleischer",
            "fleischerei",
            "konditorei",
            "getreidemühle",
            "getreidemuehle",
            "molkerei",
            "brauerei",
            "herstellung",
            "erzeugung",
            "produktion",
            "fertigung",
            "verarbeitung",
            "tischler",
            "schlosser",
            "metallbau",
            "druckerei",
            "buchbinderei",
        ),
    ),
    # G — trade (very generic; near the end so specific sectors above win first)
    (
        "G",
        (
            "kraftfahrzeughandel",
            "kfz-handel",
            "autohandel",
            "kraftfahrzeugtechnik",
            "großhandel",
            "grosshandel",
            "einzelhandel",
            "handelsgewerbe",
            "handel mit",
            "vertrieb",
            "import",
            "export",
            "handel",
        ),
    ),
)


class OenaceGuess(BaseModel):
    """A deterministic ÖNACE-section guess for a Geschäftszweig."""

    section: str  # ÖNACE section code A–U
    label: str  # German section label
    confidence: str  # "high" (one clear section) | "medium" (text spans several sections)
    method: str = "keyword"  # provenance — the deterministic head (vs a future "llm" tail)


def classify_oenace(geschaeftszweig: str | None) -> OenaceGuess | None:
    """Map a free-text Geschäftszweig to an ÖNACE section, or ``None`` if no rule matches.

    Returns the section of the **first** (most specific) matching rule. Confidence is ``high`` when
    every match resolves to a single section, ``medium`` when the text triggers rules from several
    sections (multi-activity — a primary is still chosen, but flagged). ``None`` means "leave to the
    LLM tail" — never a forced guess."""
    text = (geschaeftszweig or "").lower().strip()
    if not text:
        return None
    matched: list[str] = [sec for sec, kws in _RULES if any(k in text for k in kws)]
    if not matched:
        return None
    primary = matched[0]
    confidence = "high" if len(set(matched)) == 1 else "medium"
    return OenaceGuess(section=primary, label=OENACE_SECTIONS[primary], confidence=confidence)
