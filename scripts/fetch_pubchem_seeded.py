#!/usr/bin/env python3
"""Fetch a reproducible, bounded PubChem property snapshot for validated RXN2 seeds."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PROPERTY_URL = (
    "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/"
    "{keys}/property/Title,IsomericSMILES,InChI,InChIKey/JSON"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seed_keys(path: Path) -> list[str]:
    keys = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        key = str(record.get("inchi_key") or "").strip().upper()
        if len(key) != 27:
            raise ValueError(f"seed line {line_number} has no valid InChIKey")
        keys.add(key)
    return sorted(keys)


def records_from_payload(payload: dict) -> list[dict]:
    properties = payload.get("PropertyTable", {}).get("Properties", [])
    if not isinstance(properties, list):
        raise ValueError("PubChem response has no property list")
    return [
        {
            "CID": item["CID"],
            "Title": item.get("Title"),
            "IsomericSMILES": item.get("SMILES"),
            "InChI": item.get("InChI"),
            "InChIKey": item["InChIKey"],
        }
        for item in properties
        if item.get("CID") and item.get("InChIKey")
    ]


def finalize_partial(partial: Path, output: Path) -> tuple[int, int]:
    records = {}
    for line in partial.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[(str(record["CID"]), record["InChIKey"])] = record
    normalized = partial.with_suffix(partial.suffix + ".normalized")
    with normalized.open("w", encoding="utf-8", newline="\n") as handle:
        for key in sorted(records):
            handle.write(json.dumps(records[key], sort_keys=True) + "\n")
    normalized.replace(output)
    return len(records), len({record["InChIKey"] for record in records.values()})


def fetch_batch(keys: list[str], timeout: int) -> list[dict]:
    url = PROPERTY_URL.format(keys=quote(",".join(keys), safe=","))
    request = Request(url, headers={"User-Agent": "RXN2-catalogue/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return records_from_payload(json.loads(response.read().decode("utf-8")))
    except HTTPError as error:
        if error.code == 404:
            return []
        raise RuntimeError(f"PubChem HTTP {error.code}") from error
    except URLError as error:
        raise RuntimeError(f"PubChem connection failed: {error.reason}") from error


def fetch_snapshot(
    seeds: Path,
    output: Path,
    report: Path,
    batch_size: int = 25,
    sleep_seconds: float = 0.2,
    timeout: int = 30,
) -> dict:
    if batch_size < 1 or batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    keys = seed_keys(seeds)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    seen = set()
    if partial.exists():
        for line in partial.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["InChIKey"])
    remaining = [key for key in keys if key not in seen]
    with partial.open("a", encoding="utf-8", newline="\n") as handle:
        for start in range(0, len(remaining), batch_size):
            batch = remaining[start:start + batch_size]
            records = fetch_batch(batch, timeout)
            for record in records:
                if record["InChIKey"] not in seen:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                    seen.add(record["InChIKey"])
            handle.flush()
            if start + batch_size < len(remaining):
                time.sleep(sleep_seconds)
    output_records, resolved_records = finalize_partial(partial, output)
    output_sha256 = sha256_file(output)
    result = {
        "provider": "PubChem PUG-REST",
        "provider_url": PROPERTY_URL,
        "acquired_at": datetime.now(UTC).isoformat(),
        "seed_file": str(seeds.resolve()),
        "seed_sha256": sha256_file(seeds),
        "input_seed_records": len(keys),
        "resolved_records": resolved_records,
        "output_records": output_records,
        "not_found_records": len(keys) - resolved_records,
        "output": str(output.resolve()),
        "output_sha256": output_sha256,
        "batch_size": batch_size,
        "license": "PubChem contributor-specific terms apply",
    }
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(fetch_snapshot(
        args.seeds, args.output, args.report, args.batch_size, args.sleep_seconds, args.timeout
    ), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
