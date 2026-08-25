"""Resumably look up queued material names in PubChem without auto-merging them.

Raw provider responses are appended to Drive.  The compact local JSONL output
is a review queue: a PubChem hit is evidence for a candidate identity, never a
licence to rewrite RXN2's curated compound graph.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROPERTIES = "Title,SMILES,ConnectivitySMILES,InChIKey,MolecularFormula,MolecularWeight,IUPACName"
SOURCE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            records[item["query_sha256"]] = item
    return records


def pubchem_lookup(name: str, timeout: int) -> tuple[int, dict]:
    url = f"{SOURCE}/{quote(name, safe='')}/property/{PROPERTIES}/JSON"
    request = Request(url, headers={"User-Agent": "RXN2-public-evidence-graph/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
        except (TimeoutError, OSError):
            body = str(error)
        return error.code, {"error": body}
    except (URLError, TimeoutError, OSError) as error:
        return 0, {"error": str(getattr(error, "reason", error))}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/processed/audits/identity-resolution-queue.json"))
    parser.add_argument("--raw-cache", type=Path,
                        default=Path(r"I:\My Drive\RXN2\data\raw\pubchem\name-resolution.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/processed/enrichment/pubchem-name-candidates.jsonl"))
    parser.add_argument("--limit", type=int, default=0, help="0 means every eligible queued name")
    parser.add_argument("--workers", type=int, default=3, help="Bounded parallel public requests")
    parser.add_argument("--seconds-between-requests", type=float, default=0.25)
    parser.add_argument("--timeout", type=int, default=10)
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"Missing identity queue: {args.input}")
    try:
        args.raw_cache.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SystemExit(f"Drive raw cache is unavailable; refusing a local raw-data fallback: {error}") from error

    queue = json.loads(args.input.read_text(encoding="utf-8"))["items"]
    candidates = [item for item in queue if item["queue_lane"] == "structure_enrichment"]
    candidates.sort(key=lambda item: (not item["intermediate_candidate"], -item["procedure_count"], item["normalized_name"]))
    if args.limit:
        candidates = candidates[:args.limit]
    cached = read_jsonl(args.raw_cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def fetch(item: dict) -> tuple[dict, dict, bool]:
        query_sha256 = digest(item["material_name"].casefold().strip())
        record = cached.get(query_sha256)
        if record is not None:
            return item, record, False
        status, payload = pubchem_lookup(item["material_name"], args.timeout)
        record = {
            "query_sha256": query_sha256, "query_name": item["material_name"], "provider": "PubChem PUG REST",
            "provider_url": f"{SOURCE}/{quote(item['material_name'], safe='')}/property/{PROPERTIES}/JSON",
            "requested_at": datetime.now(UTC).isoformat(), "http_status": status, "payload": payload,
        }
        time.sleep(args.seconds_between_requests)
        return item, record, True

    completed = 0
    with args.raw_cache.open("a", encoding="utf-8") as raw, args.output.open("w", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
            for item, record, is_new in pool.map(fetch, candidates):
                query_sha256 = record["query_sha256"]
                if is_new:
                    raw.write(json.dumps(record, sort_keys=True) + "\n"); raw.flush()
                    cached[query_sha256] = record
                properties = record.get("payload", {}).get("PropertyTable", {}).get("Properties", [])
                output.write(json.dumps({
                    "material_name": item["material_name"], "normalized_name": item["normalized_name"],
                    "graph_role": item["graph_role"], "intermediate_candidate": item["intermediate_candidate"],
                    "procedure_count": item["procedure_count"], "patent_count": item["patent_count"],
                    "pubchem_query_sha256": query_sha256, "pubchem_http_status": record["http_status"],
                    "pubchem_properties": properties, "candidate_state": "needs_review",
                    "automatic_merge_allowed": False,
                }, sort_keys=True) + "\n")
                output.flush()
                completed += 1
                if completed % 25 == 0:
                    print(json.dumps({"completed": completed, "total": len(candidates)}, sort_keys=True), flush=True)
    print(json.dumps({"completed": completed, "raw_cache": str(args.raw_cache), "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
