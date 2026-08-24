#!/usr/bin/env python3
"""Fetch a bounded, resumable UniChem cross-reference snapshot for RXN2 structures."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-provisional.sqlite"
API_URL = "https://www.ebi.ac.uk/unichem/api/v1/compounds"
SELECTED_SOURCES = {"chembl", "drugbank", "chebi", "fdasrs", "surechembl", "pubchem", "drugcentral"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seeds(db_path: Path, limit: int = 0) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    sql = """SELECT c.inchi_key, min(c.preferred_name) preferred_name
             FROM compound c JOIN drug_compound dc USING (compound_id)
             WHERE length(c.inchi_key)=27
             GROUP BY c.inchi_key ORDER BY c.inchi_key"""
    rows = [dict(row) for row in db.execute(sql)]
    db.close()
    return rows[:limit] if limit > 0 else rows


def fetch_one(inchi_key: str, timeout: int, retries: int = 5) -> dict:
    data = json.dumps({"type": "inchikey", "compound": inchi_key}).encode()
    for attempt in range(retries):
        request = Request(API_URL, data=data, method="POST", headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "RXN2-catalogue/1.0",
        })
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and attempt + 1 < retries:
                delay = float(error.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(delay, 60))
                continue
            raise RuntimeError(f"UniChem HTTP {error.code}") from error
        except URLError as error:
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 60))
                continue
            raise RuntimeError(f"UniChem connection failed: {error.reason}") from error
    raise RuntimeError("UniChem retry limit reached")


def iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def flatten(raw_path: Path, output: Path) -> tuple[int, int]:
    records = {}
    queried = 0
    for row in iter_jsonl(raw_path):
        queried += 1
        preferred = row["preferred_name"]
        key = row["inchi_key"]
        for compound in row.get("response", {}).get("compounds", []):
            uci = compound.get("uci")
            inchi = compound.get("inchi", {}).get("inchi")
            for source in compound.get("sources", []):
                short = str(source.get("shortName") or "").casefold()
                source_compound_id = str(source.get("compoundId") or "").strip()
                if short not in SELECTED_SOURCES or not source_compound_id:
                    continue
                identity = (key, short, source_compound_id)
                records[identity] = {
                    "preferred_name": preferred,
                    "inchikey": key,
                    "inchi": inchi,
                    "unichem_id": uci,
                    "source_id": short,
                    "source_compound_id": source_compound_id,
                    "source_url": source.get("url"),
                }
    partial = output.with_suffix(output.suffix + ".partial")
    output.parent.mkdir(parents=True, exist_ok=True)
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for identity in sorted(records):
            handle.write(json.dumps(records[identity], ensure_ascii=False, sort_keys=True) + "\n")
    partial.replace(output)
    return queried, len(records)


def acquire(db_path: Path, raw: Path, output: Path, report: Path, sleep_seconds: float, timeout: int, limit: int) -> dict:
    seed_rows = seeds(db_path, limit)
    raw.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    partial = raw.with_suffix(raw.suffix + ".partial")
    if raw.is_file() and not partial.exists():
        existing = {row["inchi_key"] for row in iter_jsonl(raw)}
    else:
        existing = {row["inchi_key"] for row in iter_jsonl(partial)}
    remaining = [row for row in seed_rows if row["inchi_key"] not in existing]
    with partial.open("a", encoding="utf-8", newline="\n") as handle:
        for index, seed in enumerate(remaining, 1):
            payload = fetch_one(seed["inchi_key"], timeout)
            handle.write(json.dumps({
                "inchi_key": seed["inchi_key"], "preferred_name": seed["preferred_name"],
                "queried_at": datetime.now(UTC).isoformat(), "response": payload,
            }, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            if index < len(remaining):
                time.sleep(sleep_seconds)
    if partial.exists():
        partial.replace(raw)
    queried, mappings = flatten(raw, output)
    not_found = sum(not row.get("response", {}).get("compounds") for row in iter_jsonl(raw))
    result = {
        "provider": "UniChem 2.0", "provider_url": API_URL,
        "acquired_at": datetime.now(UTC).isoformat(), "input_seed_records": len(seed_rows),
        "queried_records": queried, "not_found_records": not_found,
        "selected_mapping_records": mappings, "selected_sources": sorted(SELECTED_SOURCES),
        "raw_output": str(raw.resolve()), "raw_sha256": sha256_file(raw),
        "mapping_output": str(output.resolve()), "mapping_sha256": sha256_file(output),
        "license": "UniChem/underlying-source terms apply", "status": "succeeded",
    }
    temp = report.with_suffix(report.suffix + ".partial")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(acquire(args.db, args.raw, args.output, args.report, args.sleep_seconds, args.timeout, args.limit), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())