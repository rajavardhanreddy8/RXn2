#!/usr/bin/env python3
"""Resolve bounded EPO failures through recorded full-text patent family members."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.acquire_epo_ops as epo
from scripts.import_epo_ops import parse_family
from scripts.local_automation import load_env_file


KIND_PRIORITY = {"A1": 0, "A2": 1, "B1": 2, "B2": 3}
COUNTRY_PRIORITY = {"EP": 0, "WO": 1, "US": 2, "GB": 3, "ES": 4, "CA": 5, "AU": 6}


def flexible_docdb_identifier(publication: str) -> str:
    match = re.fullmatch(r"([A-Z]{2})-(\d+)-([A-Z]\d?)", publication.upper())
    if not match:
        raise ValueError(f"unsupported publication number: {publication}")
    return ".".join(match.groups())


def candidate_rank(member: dict) -> tuple:
    kind = member["kind_code"].upper()
    country = member["country_code"].upper()
    return (
        KIND_PRIORITY.get(kind, 50),
        COUNTRY_PRIORITY.get(country, 50),
        member.get("publication_date") or "9999-99-99",
        member["publication_number"],
    )


def fulltext_candidates(family_xml: Path, queried_publication: str, limit: int) -> list[str]:
    members = [
        member for member in parse_family(family_xml)
        if member["publication_number"] != queried_publication
        and member["kind_code"].upper() in KIND_PRIORITY
    ]
    return [member["publication_number"] for member in sorted(members, key=candidate_rank)[:limit]]


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--max-family-candidates", type=int, default=5)
    parser.add_argument("--state-output", type=Path)
    args = parser.parse_args()
    if args.max_family_candidates < 1 or args.max_family_candidates > 20:
        raise SystemExit("max family candidates must be between 1 and 20")
    load_env_file(args.env_file)
    key = os.getenv("EPO_OPS_CONSUMER_KEY", "").strip()
    secret = os.getenv("EPO_OPS_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        raise SystemExit("EPO OPS credentials are missing")
    seeds = epo.batch_publications(args.batch)
    if not seeds:
        raise SystemExit("The bounded batch contains no publications")
    state_path = args.state_output or args.output / "family-fallback-agent-state.json"
    epo.docdb_identifier = flexible_docdb_identifier
    token = epo.access_token(key, secret)
    records = []

    for seed in seeds:
        attempts = []
        selected = None
        seed_dir = args.output / seed
        manifest = seed_dir / "manifest.json"
        if manifest.is_file():
            selected = seed
        else:
            family_xml = seed_dir / "family.xml"
            if not family_xml.is_file():
                try:
                    result = epo.acquire(seed, args.output, token, include_description=True)
                    attempts.append(result)
                    if result["status"] in {"succeeded", "skipped"}:
                        selected = seed
                except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as error:
                    attempts.append({"publication": seed, "status": "failed", "error": str(error)})
            if selected is None and family_xml.is_file():
                for candidate in fulltext_candidates(family_xml, seed, args.max_family_candidates):
                    try:
                        result = epo.acquire(candidate, args.output, token, include_description=True)
                        attempts.append(result)
                        if result["status"] in {"succeeded", "skipped"}:
                            selected = candidate
                            break
                    except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as error:
                        attempts.append({"publication": candidate, "status": "failed", "error": str(error)})
        records.append(
            {
                "seed_publication": seed,
                "selected_fulltext_publication": selected,
                "status": "succeeded" if selected else "unavailable",
                "attempts": attempts,
            }
        )
        write_state(
            state_path,
            {
                "schema_version": "rxn2-epo-family-fallback-agent-state-v1",
                "updated_at": datetime.now(UTC).isoformat(),
                "bounded_seed_publications": seeds,
                "records": records,
                "safety": {
                    "open_ended_discovery": False,
                    "family_metadata_required": True,
                    "maximum_candidates_per_family": args.max_family_candidates,
                },
            },
        )
    succeeded = sum(record["status"] == "succeeded" for record in records)
    print(json.dumps({"seed_publications": len(seeds), "fulltext_resolved": succeeded, "records": records}, indent=2))
    return 1 if succeeded != len(seeds) else 0


if __name__ == "__main__":
    raise SystemExit(main())
