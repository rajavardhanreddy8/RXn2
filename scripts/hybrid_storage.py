#!/usr/bin/env python3
"""Bounded local cache for an authoritative Google Drive raw-data store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "configs" / "storage_policy.json"


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class StoragePolicy:
    raw_root: Path
    cache_root: Path
    curated_db: Path
    cache_max_bytes: int
    minimum_free_bytes: int
    minimum_free_fraction: float
    verify_sha256: bool = True
    cloud_only: bool = False
    stream_max_bytes: int = 512 * 1024 * 1024

    @classmethod
    def load(cls, path: Path = DEFAULT_POLICY) -> "StoragePolicy":
        values = json.loads(path.read_text(encoding="utf-8"))

        def resolved(env_name: str, key: str) -> Path:
            value = os.getenv(env_name, values[key])
            result = Path(value)
            return result.resolve() if result.is_absolute() else (ROOT / result).resolve()

        policy = cls(
            raw_root=resolved("RXN2_RAW_ROOT", "raw_root"),
            cache_root=resolved("RXN2_CACHE_ROOT", "cache_root"),
            curated_db=resolved("RXN2_DB_PATH", "curated_db"),
            cache_max_bytes=int(os.getenv("RXN2_CACHE_MAX_BYTES", values["cache_max_bytes"])),
            minimum_free_bytes=int(os.getenv("RXN2_MINIMUM_FREE_BYTES", values["minimum_free_bytes"])),
            minimum_free_fraction=float(
                os.getenv("RXN2_MINIMUM_FREE_FRACTION", values["minimum_free_fraction"])
            ),
            verify_sha256=bool(values.get("verify_sha256", True)),
            cloud_only=bool(values.get("cloud_only", False)),
            stream_max_bytes=int(
                os.getenv("RXN2_STREAM_MAX_BYTES", values.get("stream_max_bytes", 536870912))
            ),
        )
        policy.validate_layout()
        return policy

    def validate_layout(self) -> None:
        if self.cache_max_bytes <= 0:
            raise ValueError("cache_max_bytes must be positive")
        if not 0 <= self.minimum_free_fraction < 1:
            raise ValueError("minimum_free_fraction must be between 0 and 1")
        if self.stream_max_bytes <= 0:
            raise ValueError("stream_max_bytes must be positive")
        if is_relative_to(self.curated_db, self.raw_root):
            raise ValueError("curated SQLite database must not live inside the Drive raw root")
        if is_relative_to(self.cache_root, self.raw_root):
            raise ValueError("local cache must not live inside the Drive raw root")

    def require_raw_root(self) -> Path:
        if not self.raw_root.is_dir():
            raise RuntimeError(f"authoritative raw store is unavailable: {self.raw_root}")
        return self.raw_root

    def reserve_bytes(self, disk_total: int) -> int:
        return max(self.minimum_free_bytes, int(disk_total * self.minimum_free_fraction))


def cache_bytes(cache_root: Path) -> int:
    if not cache_root.exists():
        return 0
    return sum(path.stat().st_size for path in cache_root.rglob("*") if path.is_file())


def ensure_capacity(policy: StoragePolicy, incoming_bytes: int, replacing_bytes: int = 0) -> None:
    policy.cache_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(policy.cache_root)
    reserve = policy.reserve_bytes(usage.total)
    projected_cache = cache_bytes(policy.cache_root) - replacing_bytes + incoming_bytes
    if projected_cache > policy.cache_max_bytes:
        raise RuntimeError(
            f"local cache limit exceeded: {projected_cache} > {policy.cache_max_bytes} bytes"
        )
    # A replacement is copied to a sibling partial file before os.replace, so
    # the whole incoming artifact must fit temporarily alongside the old one.
    if usage.free - incoming_bytes < reserve:
        raise RuntimeError(
            f"local free-space floor would be crossed: reserve {reserve} bytes, "
            f"available {usage.free} bytes"
        )


def stage_file(source: Path, policy: StoragePolicy) -> Path:
    raw_root = policy.raw_root.resolve()
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not is_relative_to(source, raw_root):
        return source
    policy.require_raw_root()
    if policy.cloud_only:
        if source.stat().st_size > policy.stream_max_bytes:
            raise RuntimeError(
                f"cloud processing required for {source.name}: "
                f"{source.stat().st_size} bytes exceeds the "
                f"{policy.stream_max_bytes}-byte direct-stream limit"
            )
        return source

    target = policy.cache_root / source.relative_to(raw_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    source_size = source.stat().st_size
    existing_size = target.stat().st_size if target.exists() else 0
    ensure_capacity(policy, 0)
    if not target.is_file() or existing_size != source_size:
        ensure_capacity(policy, source_size, existing_size)
    source_hash = sha256_file(source) if policy.verify_sha256 else None
    if target.is_file() and existing_size == source_size:
        if not policy.verify_sha256 or sha256_file(target) == source_hash:
            return target

    ensure_capacity(policy, source_size, existing_size)
    partial = target.with_suffix(target.suffix + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        shutil.copy2(source, partial)
        if partial.stat().st_size != source_size:
            raise RuntimeError(f"staged size mismatch for {source.name}")
        if policy.verify_sha256 and sha256_file(partial) != source_hash:
            raise RuntimeError(f"staged SHA-256 mismatch for {source.name}")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def stage_snapshot(snapshot: Path, names: tuple[str, ...], policy: StoragePolicy) -> Path:
    snapshot = snapshot.resolve()
    files = [snapshot / name for name in names]
    missing = [path.name for path in files if not path.is_file()]
    if missing:
        raise ValueError(f"snapshot is missing: {', '.join(missing)}")
    staged = [stage_file(path, policy) for path in files]
    parents = {path.parent for path in staged}
    if len(parents) != 1:
        raise RuntimeError("staged snapshot files did not resolve to one directory")
    return parents.pop()


def manifest(root: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "root": str(root),
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    stage = commands.add_parser("stage")
    stage.add_argument("path", type=Path)
    describe = commands.add_parser("manifest")
    describe.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        policy = StoragePolicy.load(args.policy)
        if args.command == "check":
            policy.require_raw_root()
            ensure_capacity(policy, 0)
            usage = shutil.disk_usage(policy.cache_root)
            result = {
                "raw_root": str(policy.raw_root),
                "cache_root": str(policy.cache_root),
                "curated_db": str(policy.curated_db),
                "cache_bytes": cache_bytes(policy.cache_root),
                "cache_max_bytes": policy.cache_max_bytes,
                "cloud_only": policy.cloud_only,
                "stream_max_bytes": policy.stream_max_bytes,
                "available_bytes": usage.free,
                "required_free_bytes": policy.reserve_bytes(usage.total),
            }
        elif args.command == "stage":
            result = {"staged_path": str(stage_file(args.path, policy))}
        else:
            result = manifest(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
