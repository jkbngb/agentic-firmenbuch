"""Storage-fake tests — the offline doubles that every stage's tests rely on (§12)."""

from __future__ import annotations

from fbl_core.storage import RAW_CONTAINER, BlobDownloadLink, BlobStore, InMemoryBlobStore


def test_download_link_targets_blob_with_expiry() -> None:
    blob = InMemoryBlobStore()
    path = "012345f/2024-12-31/012345f_2024-12-31_abc1234567_jb.pdf"
    blob.put_bytes(RAW_CONTAINER, path, b"%PDF-1.7")

    link = blob.download_link(RAW_CONTAINER, path, ttl_minutes=15)
    assert isinstance(link, BlobDownloadLink)
    # Points at the exact blob, carries a matching expiry, and never streams bytes.
    assert link.url.startswith(f"memory://{RAW_CONTAINER}/{path}?")
    assert f"se={link.expires_at}" in link.url
    assert link.expires_in_seconds == 15 * 60


def test_blob_store_strips_trailing_slash_from_account_url() -> None:
    # Regression: a trailing slash on the account URL made download_link emit
    # "…net//90-raw/…" (empty container) -> Azure 400 on the SAS download. The env var
    # BLOB_ACCOUNT_URL is commonly stored with a trailing slash, so it must be normalized.
    assert BlobStore("https://acct.blob.core.windows.net/")._account_url == (
        "https://acct.blob.core.windows.net"
    )
    assert BlobStore("https://acct.blob.core.windows.net")._account_url == (
        "https://acct.blob.core.windows.net"
    )
