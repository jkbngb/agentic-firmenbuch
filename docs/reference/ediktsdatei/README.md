# docs/reference/ediktsdatei — official Insolvenzdatei interface docs

Official upstream material for the Austrian **Insolvenzdatei** (insolvency register) IWG REST
API, from the **BRZ / BMJ**. Reference only — our analysis + integration design lives in
[`docs/research/ediktsdatei_insolvency.md`](../../research/ediktsdatei_insolvency.md).

| File | What it is |
|---|---|
| `IWG-Schnittstellenbeschreibung-Insolvenzdatei-2021-02-09.pdf` | The official IWG interface spec: endpoints (collections `All` / `Deletions`, document GET), search syntax, record fields, sync/deltas, deletions. |

**Access is authenticated + paid** (BMJ/BRZ-issued credentials, IWG agreement) — see the
research doc §1. Used by: **P4 Ediktsdatei** (planned, issue #9).
