"""F05: LocalDiskObjectStore is interface-equivalent to the R2 store against the true
put/get port, and the factory selects it on STORAGE_MODE. Fully offline — no cloud creds,
no Docker — so it never threatens the CI-offline invariant.
"""

from __future__ import annotations

import uuid

import pytest

from app.config.settings import Settings
from app.services.storage import (
    LocalDiskObjectStore,
    build_artifact_key,
    build_storage_key,
    get_object_store,
)


async def test_local_store_round_trip_write_then_read(tmp_path) -> None:
    store = LocalDiskObjectStore(str(tmp_path))
    key = build_storage_key(uuid.uuid4(), uuid.uuid4(), "report.pdf")
    data = b"\x00binary\xffblob\x01"

    await store.put(key, data, "application/pdf")
    got = await store.get(key)

    assert got == data  # byte-identical round-trip, the property the parse->embed walk needs


async def test_local_store_creates_nested_key_dirs(tmp_path) -> None:
    # Keys carry org/{uuid}/doc/{uuid}/artifacts/{stage}.json — the '/'-separated segments
    # must materialize as nested directories under the root, created on put.
    store = LocalDiskObjectStore(str(tmp_path))
    key = build_artifact_key(uuid.uuid4(), uuid.uuid4(), "parsing")

    await store.put(key, b"{}", "application/json")

    written = tmp_path / key
    assert written.is_file()
    assert written.read_bytes() == b"{}"


async def test_local_store_get_missing_key_raises(tmp_path) -> None:
    # Parity with R2 (boto3 raises ClientError on a missing key): the local store must also
    # RAISE on an absent key, not return None/empty. Ingestion's resumable-parse path catches
    # `except Exception` broadly to mean "artifact not found -> parse fresh", so FileNotFoundError
    # and ClientError take the same branch. A returned-empty here would silently change that.
    store = LocalDiskObjectStore(str(tmp_path))

    with pytest.raises(Exception):  # noqa: B017 — the contract is "raises", any Exception subclass
        await store.get("org/none/doc/none/source.pdf")


def test_factory_selects_local_when_mode_local(monkeypatch) -> None:
    monkeypatch.setattr("app.services.storage.settings.STORAGE_MODE", "local")
    assert isinstance(get_object_store(), LocalDiskObjectStore)


def test_storage_mode_defaults_to_r2() -> None:
    # The default is r2 — production/CI behaviour is unchanged by this feature; local is
    # strictly opt-in. Asserted against pure config, not by constructing a boto3 client
    # (which would be environment-fragile: an empty R2_ENDPOINT_URL raises at construction).
    assert Settings().STORAGE_MODE == "r2"


def test_factory_selects_r2_branch(monkeypatch) -> None:
    # Verify the r2 branch returns the R2 store without building a live boto3 client.
    sentinel = object()
    monkeypatch.setattr("app.services.storage.R2ObjectStore", lambda: sentinel)
    monkeypatch.setattr("app.services.storage.settings.STORAGE_MODE", "r2")
    assert get_object_store() is sentinel


def test_factory_rejects_unknown_mode(monkeypatch) -> None:
    monkeypatch.setattr("app.services.storage.settings.STORAGE_MODE", "s3-direct")
    with pytest.raises(ValueError, match="unknown STORAGE_MODE"):
        get_object_store()
