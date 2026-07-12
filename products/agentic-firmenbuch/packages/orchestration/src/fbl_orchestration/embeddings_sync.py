"""Keep the per-company semantic-search embeddings fresh (T14).

Mirrors ``industry_sync``: iterate the served docs, (re)embed only those whose content changed
(``embedding_hash`` ≠ hash of the current embedding text, or absent), batch the AOAI calls, and
patch ``/embedding`` + ``/embedding_hash`` back onto the doc. Geschäftszweige rarely change, so
steady state is a handful of embeds per day; the first pass is the one-time backfill.

The embedder is injected (``EmbedFn``) so this module imports no Azure SDK at module load and the
pure parts — :func:`embedding_text`, :func:`embedding_hash` — unit-test offline. When no Azure
OpenAI endpoint is configured the whole step is skipped and the ``query`` filter falls back to its
lexical leg (search.py); nothing here is required for search to work.

STATUS: scaffolding. Wiring into the daily job + the one-time backfill is gated on the owner
enabling Cosmos vector search + provisioning the EU Azure OpenAI resource (see
docs/search-quality/SEMANTIC_SEARCH.md). Do NOT enable in the daily pipeline until then.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from fbl_core.storage import CosmosStoreLike

PRESENTED = "10_presentation"
BATCH_SIZE = 500

# Takes a batch of texts, returns one vector per text (same order). Injected — see
# make_azure_openai_embedder.
EmbedFn = Callable[[list[str]], list[list[float]]]


def _g(doc: dict[str, Any], *path: str) -> Any:
    cur: Any = doc
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def embedding_text(doc: dict[str, Any]) -> str:
    """The single string embedded per company: identity name + activity free text + the German
    ÖNACE labels. Deliberately NO location (structured filters handle it), NO financials
    (structured), NO manager names (GDPR/noise) — so retrieval is about WHAT the company does."""
    name = _g(doc, "identity", "name") or ""
    industry = doc.get("industry") or {}
    activity = _g(doc, "industry", "geschaeftszweig") or _g(doc, "company", "description") or ""
    oenace = industry.get("oenace") or {}
    group = oenace.get("group_label_de") or ""
    division = oenace.get("division_label_de") or ""
    section = oenace.get("section_label_de") or ""
    return f"{name}. Tätigkeit: {activity}. Branche: {group}; {division}; {section}."


def embedding_hash(text: str) -> str:
    """Freshness guard: sha256 of the embedding text (prefixed, matching the stored form)."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _needs_embedding(doc: dict[str, Any]) -> bool:
    return doc.get("embedding_hash") != embedding_hash(embedding_text(doc))


def _batched(items: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for it in items:
        batch.append(it)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def sync_embeddings(
    cosmos: CosmosStoreLike,
    embed: EmbedFn,
    *,
    batch_size: int = BATCH_SIZE,
    limit: int | None = None,
) -> dict[str, int]:
    """Re-embed every served doc whose content changed. Returns ``{scanned, embedded}``. Idempotent
    — a doc already carrying the right ``embedding_hash`` is skipped, so a re-run after a partial
    pass only does the remainder."""
    scanned = embedded = 0
    stale = (
        d
        for d in cosmos.query(PRESENTED, "SELECT * FROM c WHERE NOT STARTSWITH(c.id, '__')", [])
        if _needs_embedding(d)
    )
    for batch in _batched(stale, batch_size):
        texts = [embedding_text(d) for d in batch]
        vectors = embed(texts)
        for doc, text, vec in zip(batch, texts, vectors, strict=True):
            doc["embedding"] = vec
            doc["embedding_hash"] = embedding_hash(text)
            cosmos.upsert(PRESENTED, doc)
            embedded += 1
        scanned += len(batch)
        if limit is not None and embedded >= limit:
            break
    return {"scanned": scanned, "embedded": embedded}


def make_azure_openai_embedder(
    endpoint: str, deployment: str, dimensions: int
) -> EmbedFn:  # pragma: no cover - requires the Azure OpenAI SDK + a live endpoint
    """Build the AOAI embedder (lazy import; Managed-Identity auth). One vector per input text,
    ``text-embedding-3-small`` at the configured dimension. Called only by the wired job."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from openai import AzureOpenAI  # type: ignore[import-not-found]

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version="2024-10-21",
    )

    def embed(texts: list[str]) -> list[list[float]]:
        resp = client.embeddings.create(model=deployment, input=texts, dimensions=dimensions)
        return [d.embedding for d in resp.data]

    return embed
