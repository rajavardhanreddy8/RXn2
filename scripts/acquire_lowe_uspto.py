#!/usr/bin/env python3
"""Download the pinned CC0 Lowe USPTO reaction-SMILES snapshot to Drive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


ARTICLE_URL = "https://api.figshare.com/v2/articles/5104873"
ARTICLE_ID = 5104873
RELEASE_ID = "2017-06-13"
SOURCE_ID = "lowe_uspto_reactions"
ASSET_NAMES = (
    "1976_Sep2016_USPTOgrants_smiles.7z",
    "2001_Sep2016_USPTOapplications_smiles.7z",
)
CHUNK_SIZE = 8 * 1024 * 1024


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def hashes(path: Path) -> tuple[str, str]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK_SIZE):
            md5.update(block)
            sha256.update(block)
    return md5.hexdigest(), sha256.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def article_metadata(opener=urllib.request.urlopen) -> dict:
    with opener(ARTICLE_URL, timeout=60) as response:
        article = json.load(response)
    if article.get("id") != ARTICLE_ID or article.get("version") != 1:
        raise RuntimeError("unexpected Lowe Figshare article or version")
    if article.get("license", {}).get("name") != "CC0":
        raise RuntimeError("Lowe Figshare snapshot no longer reports CC0")
    files = {item.get("name"): item for item in article.get("files", [])}
    missing = [name for name in ASSET_NAMES if name not in files]
    if missing:
        raise RuntimeError(f"Lowe Figshare snapshot is missing: {', '.join(missing)}")
    return article


def download_file(asset: dict, destination: Path, opener=urllib.request.urlopen) -> dict:
    expected_size = int(asset["size"])
    expected_md5 = str(asset["supplied_md5"]).lower()
    partial = destination.with_suffix(destination.suffix + ".partial")
    if destination.is_file():
        actual_md5, actual_sha256 = hashes(destination)
        if destination.stat().st_size != expected_size or actual_md5 != expected_md5:
            raise RuntimeError(f"existing Lowe artifact does not match Figshare metadata: {destination.name}")
        return {"status": "existing", "size_bytes": expected_size, "md5": actual_md5, "sha256": actual_sha256}
    if partial.exists() and partial.stat().st_size > expected_size:
        raise RuntimeError(f"partial Lowe artifact is oversized: {partial.name}")

    def request(resume: bool):
        headers = {"Range": f"bytes={partial.stat().st_size}-"} if resume else {}
        return opener(urllib.request.Request(asset["download_url"], headers=headers), timeout=120)

    resume = partial.is_file() and partial.stat().st_size > 0
    response = request(resume)
    if resume and response.getcode() != 206:
        response.close()
        partial.unlink()
        response = request(False)
    mode = "ab" if partial.exists() and response.getcode() == 206 else "wb"
    with response, partial.open(mode) as handle:
        while block := response.read(CHUNK_SIZE):
            handle.write(block)
    if partial.stat().st_size != expected_size:
        raise RuntimeError(f"incomplete Lowe artifact: {destination.name}")
    actual_md5, actual_sha256 = hashes(partial)
    if actual_md5 != expected_md5:
        raise RuntimeError(f"Lowe MD5 mismatch: {destination.name}")
    partial.replace(destination)
    return {"status": "downloaded", "size_bytes": expected_size, "md5": actual_md5, "sha256": actual_sha256}


def download_snapshot(root: Path, *, dry_run: bool = False, opener=urllib.request.urlopen) -> dict:
    article = article_metadata(opener)
    assets = {item["name"]: item for item in article["files"]}
    snapshot = root / SOURCE_ID / RELEASE_ID
    if dry_run:
        return {
            "source_id": SOURCE_ID,
            "release_id": RELEASE_ID,
            "snapshot": str(snapshot),
            "assets": [{"name": name, "size_bytes": assets[name]["size"]} for name in ASSET_NAMES],
        }
    snapshot.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for name in ASSET_NAMES:
        asset = assets[name]
        result = download_file(asset, snapshot / name, opener)
        downloaded.append({
            "file_id": asset["id"], "name": name, "url": asset["download_url"],
            "expected_md5": asset["supplied_md5"], **result,
        })
    payload = {
        "source_id": SOURCE_ID,
        "release_id": RELEASE_ID,
        "retrieved_at": timestamp(),
        "article": {
            "id": article["id"], "version": article["version"], "doi": article.get("doi"),
            "url": "https://figshare.com/articles/dataset/Chemical_reactions_from_US_patents_1976-Sep2016_/5104873",
            "license": article["license"],
        },
        "files": downloaded,
        "purpose": "weak_reaction_pretraining_only",
        "caveats": ["Duplicates are frequent.", "Atom maps can be wrong.", "This snapshot cannot create accepted RXN2 evidence automatically."],
    }
    atomic_json(snapshot / "release-metadata.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=Path(os.getenv("RXN2_RAW_ROOT", r"I:\My Drive\RXN2\data\raw")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(download_snapshot(args.raw_root, dry_run=args.dry_run), indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
