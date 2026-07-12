"""T14 — the pure, offline parts of the embeddings sync (no Azure OpenAI needed)."""

from __future__ import annotations

from typing import Any

from fbl_core.storage import InMemoryCosmosStore
from fbl_orchestration.embeddings_sync import (
    embedding_hash,
    embedding_text,
    sync_embeddings,
)

PRESENTED = "10_presentation"


def _doc(fnr: str) -> dict[str, Any]:
    return {
        "id": fnr,
        "fnr": fnr,
        "identity": {"name": "Novomatic AG"},
        "industry": {
            "geschaeftszweig": "Glücksspielautomaten",
            "oenace": {
                "group_label_de": "Herstellung von Spielwaren",
                "division_label_de": "Sonstige Warenherstellung",
                "section_label_de": "Verarbeitendes Gewerbe",
            },
        },
    }


def test_embedding_text_is_activity_focused_no_location_no_financials() -> None:
    text = embedding_text(_doc("1a"))
    assert "Novomatic AG" in text
    assert "Glücksspielautomaten" in text
    assert "Herstellung von Spielwaren" in text
    # deliberately excluded content stays out
    assert "Wien" not in text and "Bilanzsumme" not in text


def test_embedding_hash_is_stable_and_content_addressed() -> None:
    t = embedding_text(_doc("1a"))
    assert embedding_hash(t) == embedding_hash(t)
    assert embedding_hash(t).startswith("sha256:")
    assert embedding_hash("other") != embedding_hash(t)


def test_sync_only_embeds_changed_docs_and_is_idempotent() -> None:
    cosmos = InMemoryCosmosStore()
    cosmos.upsert(PRESENTED, _doc("1a"))
    cosmos.upsert(PRESENTED, _doc("2b"))
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]

    first = sync_embeddings(cosmos, fake_embed)
    assert first == {"scanned": 2, "embedded": 2}
    assert len(calls) == 1  # one batch

    # Re-run: both docs now carry the right hash → nothing re-embedded.
    calls.clear()
    second = sync_embeddings(cosmos, fake_embed)
    assert second == {"scanned": 0, "embedded": 0}
    assert calls == []

    # Content change → exactly that doc re-embeds.
    changed = cosmos.get(PRESENTED, "1a")
    assert changed is not None
    changed["industry"]["geschaeftszweig"] = "Etwas ganz anderes"
    cosmos.upsert(PRESENTED, changed)
    third = sync_embeddings(cosmos, fake_embed)
    assert third["embedded"] == 1
