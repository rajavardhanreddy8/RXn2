from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.hybrid_storage import (
    StoragePolicy,
    ensure_capacity,
    manifest,
    stage_file,
)


def policy(tmp_path, **overrides) -> StoragePolicy:
    values = {
        "raw_root": tmp_path / "drive" / "raw",
        "cache_root": tmp_path / "cache",
        "curated_db": tmp_path / "curated" / "scaleup.sqlite",
        "cache_max_bytes": 1_000,
        "minimum_free_bytes": 100,
        "minimum_free_fraction": 0.1,
        "verify_sha256": True,
    }
    values.update(overrides)
    result = StoragePolicy(**values)
    result.validate_layout()
    return result


def test_drive_loss_fails_closed(tmp_path):
    with pytest.raises(RuntimeError, match="raw store is unavailable"):
        policy(tmp_path).require_raw_root()


def test_database_and_cache_cannot_live_on_drive(tmp_path):
    raw = tmp_path / "drive" / "raw"
    with pytest.raises(ValueError, match="SQLite database"):
        policy(tmp_path, raw_root=raw, curated_db=raw / "scaleup.sqlite")
    with pytest.raises(ValueError, match="local cache"):
        policy(tmp_path, raw_root=raw, cache_root=raw / "cache")


def test_capacity_respects_cache_cap_and_free_space_floor(tmp_path, monkeypatch):
    current = policy(tmp_path)
    current.cache_root.mkdir()
    (current.cache_root / "existing").write_bytes(b"x" * 900)
    monkeypatch.setattr(
        "scripts.hybrid_storage.shutil.disk_usage",
        lambda _: SimpleNamespace(total=10_000, used=8_000, free=2_000),
    )
    with pytest.raises(RuntimeError, match="cache limit"):
        ensure_capacity(current, 101)

    roomy_cache = policy(tmp_path, cache_max_bytes=10_000, minimum_free_bytes=1_500)
    with pytest.raises(RuntimeError, match="free-space floor"):
        ensure_capacity(roomy_cache, 600, replacing_bytes=600)


def test_stage_is_atomic_and_checksum_verified(tmp_path, monkeypatch):
    current = policy(tmp_path)
    current.raw_root.mkdir(parents=True)
    source = current.raw_root / "source" / "release" / "data.zip"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable snapshot")
    target = stage_file(source, current)
    assert target.read_bytes() == source.read_bytes()
    assert stage_file(source, current) == target

    target.unlink()

    def interrupted_copy(src, dst):
        dst.write_bytes(b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr("scripts.hybrid_storage.shutil.copy2", interrupted_copy)
    with pytest.raises(OSError, match="interrupted"):
        stage_file(source, current)
    assert not target.exists()
    assert not target.with_suffix(target.suffix + ".partial").exists()


def test_manifest_records_exact_files(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")
    result = manifest(root)
    assert result["file_count"] == 2
    assert result["total_bytes"] == 9
    assert [item["path"] for item in result["files"]] == ["a.txt", "b.txt"]
    assert all(len(item["sha256"]) == 64 for item in result["files"])


def test_cloud_only_streams_small_files_and_blocks_large_raw_inputs(tmp_path):
    current = policy(
        tmp_path,
        cloud_only=True,
        stream_max_bytes=10,
        minimum_free_bytes=0,
        minimum_free_fraction=0,
    )
    current.raw_root.mkdir(parents=True)
    small = current.raw_root / "small.json"
    small.write_bytes(b"small")
    assert stage_file(small, current) == small.resolve()
    assert not current.cache_root.exists()

    large = current.raw_root / "large.parquet"
    large.write_bytes(b"x" * 11)
    with pytest.raises(RuntimeError, match="cloud processing required"):
        stage_file(large, current)
