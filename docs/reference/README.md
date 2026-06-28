# docs/reference — official external source material

These are **official, third-party source documents** that the project builds *on* —
**not** our own specs. We keep them in the repo so the build is reproducible and so
nobody has to re-hunt the upstream files. They are authoritative for the external
system they describe; our own specs (in `docs/`) and research (in `docs/research/`)
reference and interpret them.

| Folder / file | Source | What it is | Used by |
|---|---|---|---|
| `JustizOnline_API_Complete_Reference.md` | BMJ – Justiz | The Firmenbuch HVD API (sucheFirma, sucheUrkunde, urkunde, auszug, change feeds) | `firmenbuch_client`, ingest |
| `jab40_struktur/` | BMJ – Justiz | JAb 4.0 filing format: XSDs + Excel structure + change-log | `70_parse` |
| `gisa/` | BMAW (Gewerbe) | GISA public interface (V1 + V2 PDFs, V2 XML-structure xlsx) — trade-license register | **P3 GISA** (planned, see ROADMAP) |

## Convention

- `docs/` = **our** specs (the `*_Spezifikation.md` ones = system as built / stable;
  `Erweiterungen_Spezifikation.md` + root `ROADMAP.md` = forward plan). No version numbers in
  filenames — history is git.
- `docs/reference/` = **official upstream** source files (this folder). One sub-folder per
  external system.
- `docs/research/` = **our** research reports that analyse the above (e.g. the bank/insurer
  BWG/VAG findings).

When a new external data source is added (e.g. Ediktsdatei), drop its official docs in a new
`docs/reference/<source>/` sub-folder and add a row here.
