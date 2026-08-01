#!/usr/bin/env python3
"""Fetch approved large chemically defined drugs from the ChEMBL API."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://www.ebi.ac.uk/chembl/api/data/molecule.json"
MODALITIES = {
    "Oligonucleotide": "oligonucleotide",
    "Oligosaccharide": "oligosaccharide",
}
FIELDS = ",".join((
    "molecule_chembl_id", "molecule_type", "pref_name", "molecule_structures",
    "molecule_hierarchy", "molecule_synonyms",
))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request_type(molecule_type: str, timeout: int = 60) -> list[dict]:
    url = API + "?" + urlencode({
        "max_phase": 4,
        "molecule_type": molecule_type,
        "limit": 100,
        "only": FIELDS,
    })
    request = Request(url, headers={"User-Agent": "RXN2-catalogue/1.0"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            records = payload.get("molecules")
            if not isinstance(records, list):
                raise ValueError("ChEMBL response has no molecule list")
            return records
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(f"ChEMBL HTTP {error.code}") from error
        except URLError as error:
            if attempt == 2:
                raise RuntimeError(f"ChEMBL connection failed: {error.reason}") from error
        time.sleep(2 ** attempt)
    raise RuntimeError("ChEMBL request failed")


def normalize(item: dict) -> dict:
    chembl_id = item.get("molecule_chembl_id")
    molecule_type = item.get("molecule_type")
    if not chembl_id or molecule_type not in MODALITIES:
        raise ValueError("ChEMBL record lacks a supported ID or modality")
    hierarchy = item.get("molecule_hierarchy") or {}
    parent = hierarchy.get("parent_chembl_id") or chembl_id
    structures = item.get("molecule_structures") or {}
    preferred_name = item.get("pref_name") or chembl_id
    aliases = []
    seen = {preferred_name.casefold()}
    for synonym in item.get("molecule_synonyms") or []:
        value = str(synonym.get("molecule_synonym") or synonym.get("synonyms") or "").strip()
        if value and value.casefold() not in seen:
            aliases.append({"value": value, "type": synonym.get("syn_type") or "synonym"})
            seen.add(value.casefold())
    return {
        "preferred_name": preferred_name,
        "modality": MODALITIES[molecule_type],
        "aliases": aliases,
        "identifiers": {"CHEMBL": parent},
        "active_moiety_id": f"chembl-moiety:{parent}",
        "compound": {
            "compound_id": chembl_id,
            "smiles": structures.get("canonical_smiles"),
            "inchi": structures.get("standard_inchi"),
            "inchi_key": structures.get("standard_inchi_key"),
            "material_form": "active_moiety" if chembl_id == parent else "salt_or_form",
            "relationship_type": "active_moiety" if chembl_id == parent else "salt_or_form",
        },
    }


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    partial.replace(path)


def fetch_snapshot(raw: Path, output: Path, report: Path) -> dict:
    source_records = []
    for molecule_type in MODALITIES:
        source_records.extend(request_type(molecule_type))
    source_records.sort(key=lambda item: item["molecule_chembl_id"])
    normalized = [normalize(item) for item in source_records]
    write_jsonl(raw, source_records)
    write_jsonl(output, normalized)
    result = {
        "provider": "ChEMBL API",
        "provider_url": API,
        "release": "CHEMBL37",
        "acquired_at": datetime.now(UTC).isoformat(),
        "input_records": len(source_records),
        "accepted_records": len(normalized),
        "missing_structures": sum(
            not record["compound"].get("inchi_key") for record in normalized
        ),
        "modality_counts": {
            modality: sum(record["modality"] == modality for record in normalized)
            for modality in sorted(MODALITIES.values())
        },
        "raw_sha256": sha256_file(raw),
        "output_sha256": sha256_file(output),
        "license": "ChEMBL data license and terms apply",
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    partial = report.with_suffix(report.suffix + ".partial")
    partial.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fetch_snapshot(args.raw, args.output, args.report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
