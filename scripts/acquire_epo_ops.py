#!/usr/bin/env python3
"""Acquire bounded EPO OPS metadata and optional native description XML."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.local_automation import load_env_file


DEFAULT_BATCH = Path("I:/My Drive/RXN2/patents/manifests/pilot-10-ocr-batch.json")
DEFAULT_OUTPUT = Path("I:/My Drive/RXN2/patents/epo-ops")
TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
API_ROOT = "https://ops.epo.org/3.2/rest-services"


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def docdb_identifier(publication: str) -> str:
    match = re.fullmatch(r"([A-Z]{2})-(\d+)-([A-Z]\d)", publication.upper())
    if not match:
        raise ValueError(f"unsupported publication number: {publication}")
    return ".".join(match.groups())


def batch_publications(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = list(payload.get("completed_ocr_publications", []))
    values.extend(record["publication_number"] for record in payload.get("queued_documents", []))
    return sorted(set(values))


def access_token(key: str, secret: str) -> str:
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        token = str(json.load(response).get("access_token") or "")
    if not token:
        raise RuntimeError("EPO OPS returned no access token")
    return token


def download_xml(url: str, token: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                if not body.lstrip().startswith(b"<"):
                    raise RuntimeError(f"EPO OPS returned non-XML content for {url}")
                return body, {
                    "content_type": response.headers.get("Content-Type", ""),
                    "throttling_control": response.headers.get("X-Throttling-Control", ""),
                }
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            time.sleep(float(error.headers.get("Retry-After", attempt + 1)))
    raise RuntimeError("unreachable")


def artifact_endpoints(identifier: str, include_description: bool = False) -> dict[str, str]:
    endpoints = {
        "bibliographic.xml": f"{API_ROOT}/published-data/publication/docdb/{identifier}/biblio",
        "family.xml": f"{API_ROOT}/family/publication/docdb/{identifier}/biblio",
    }
    if include_description:
        endpoints["description.xml"] = (
            f"{API_ROOT}/published-data/publication/docdb/{identifier}/description"
        )
    return endpoints


def acquire(
    publication: str,
    output_root: Path,
    token: str,
    include_description: bool = False,
) -> dict:
    destination = output_root / publication
    manifest_path = destination / "manifest.json"
    identifier = docdb_identifier(publication)
    endpoints = artifact_endpoints(identifier, include_description)
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = existing.get("artifacts", [])
        available = {item.get("file") for item in artifacts}
        if (
            existing.get("status") == "succeeded"
            and set(endpoints).issubset(available)
            and all(
                (destination / item["file"]).is_file()
                and sha256(destination / item["file"]) == item["sha256"]
                for item in artifacts
            )
        ):
            return {"publication": publication, "status": "skipped"}

    destination.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for filename, url in endpoints.items():
        body, response_metadata = download_xml(url, token)
        target = destination / filename
        partial = target.with_suffix(target.suffix + ".partial")
        partial.write_bytes(body)
        partial.replace(target)
        artifacts.append(
            {
                "file": filename,
                "endpoint": url,
                "size_bytes": len(body),
                "sha256": sha256(target),
                **response_metadata,
            }
        )
        time.sleep(0.5)

    manifest = {
        "status": "succeeded",
        "publication_number": publication,
        "provider": "EPO Open Patent Services",
        "license": "EPO-OPS-terms",
        "downloaded_at": now(),
        "artifacts": artifacts,
    }
    partial_manifest = manifest_path.with_suffix(".json.partial")
    partial_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial_manifest.replace(manifest_path)
    return {"publication": publication, "status": "succeeded", "artifacts": len(artifacts)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--include-description",
        action="store_true",
        help="also acquire native description XML for bounded evidence extraction",
    )
    args = parser.parse_args()
    load_env_file(ROOT / ".env")
    key = os.getenv("EPO_OPS_CONSUMER_KEY", "").strip()
    secret = os.getenv("EPO_OPS_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        raise SystemExit("EPO OPS credentials are missing")
    token = access_token(key, secret)
    results = []
    for publication in batch_publications(args.batch):
        try:
            results.append(acquire(publication, args.output, token, args.include_description))
        except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as error:
            results.append({"publication": publication, "status": "failed", "error": str(error)})
    print(json.dumps({"results": results}, indent=2))
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
