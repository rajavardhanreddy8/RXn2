#!/usr/bin/env python3
"""Convert source exports to the normalized catalogue JSONL boundary."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

try:
    from scripts.hybrid_storage import DEFAULT_POLICY, StoragePolicy, stage_file
except ModuleNotFoundError:
    from hybrid_storage import DEFAULT_POLICY, StoragePolicy, stage_file


def clean(value: object | None) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def value(row: dict[str, str], *names: str) -> str | None:
    fields = {normalized(key): clean(item) for key, item in row.items()}
    for name in names:
        found = fields.get(normalized(name))
        if found:
            return found
    return None


def values(value_text: str | None) -> list[str]:
    return [item.strip() for item in re.split(r"[|;]", value_text or "") if item.strip()]


def rows(path: Path) -> Iterable[dict[str, str]]:
    if path.suffix.casefold() == ".jsonl":
        with path.open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"JSONL record {line_number} must be an object")
                yield record
        return
    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            records = next(
                (
                    payload[key]
                    for key in ("data", "items", "results", "records", "medicines")
                    if isinstance(payload.get(key), list)
                ),
                None,
            )
        else:
            records = None
        if records is None:
            raise ValueError("JSON input must be an array or contain a supported record array")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("JSON record must be an object")
            yield record
        return
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(16_384)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if path.suffix.casefold() in {".tsv", ".txt"} else csv.excel
        yield from csv.DictReader(handle, dialect=dialect)


def pubchem(row: dict[str, str]) -> list[dict]:
    approval = value(row, "approved", "is_approved", "approval_status")
    if approval and normalized(approval) not in {"1", "true", "yes", "approved", "authorised", "authorized"}:
        raise LookupError("not_approved")
    molecule_type = value(row, "molecule_type", "modality")
    if molecule_type and normalized(molecule_type) not in {"small molecule", "small_molecule"}:
        raise LookupError("not_small_molecule")
    cid = value(row, "cid", "pubchem_cid")
    preferred = value(row, "preferred_name", "title", "compound_name")
    inchi_key = value(row, "inchikey", "inchi_key")
    if not cid or not preferred or not inchi_key:
        raise ValueError("PubChem row requires CID, preferred name, and InChIKey")
    if len(inchi_key) != 27:
        raise ValueError("PubChem InChIKey must contain 27 characters")
    aliases = [
        {"value": alias, "type": "synonym"}
        for alias in values(value(row, "synonyms", "aliases"))
        if normalized(alias) != normalized(preferred)
    ]
    return [
        {
            "preferred_name": preferred,
            "aliases": aliases,
            "identifiers": {"PUBCHEM_CID": cid},
            "requires_existing_drug": True,
            "compound": {
                "compound_id": f"PUBCHEM:{cid}",
                "smiles": value(row, "isomeric_smiles", "canonical_smiles", "smiles"),
                "inchi": value(row, "inchi", "standard_inchi"),
                "inchi_key": inchi_key,
                "material_form": value(row, "material_form") or "active_moiety",
            },
        }
    ]


def unichem(row: dict[str, str]) -> list[dict]:
    preferred = value(row, "preferred_name", "name")
    inchi_key = value(row, "inchikey", "inchi_key")
    source_id = value(row, "src_id", "source_id", "source")
    source_compound_id = value(row, "src_compound_id", "source_compound_id", "identifier")
    if not preferred or not inchi_key or not source_id or not source_compound_id:
        raise ValueError(
            "UniChem row requires preferred name, InChIKey, source ID, and source compound ID"
        )
    if len(inchi_key) != 27:
        raise ValueError("UniChem InChIKey must contain 27 characters")
    namespace = f"UNICHEM_SOURCE_{re.sub(r'[^A-Za-z0-9]+', '_', source_id).upper()}"
    identifiers: dict[str, object] = {namespace: source_compound_id}
    unichem_id = value(row, "unichem_id", "uci")
    if unichem_id:
        identifiers["UNICHEM"] = unichem_id
    return [
        {
            "preferred_name": preferred,
            "aliases": values(value(row, "synonyms", "aliases")),
            "identifiers": identifiers,
            "requires_existing_drug": True,
            "compound": {
                "compound_id": value(row, "compound_id") or f"UNICHEM:{inchi_key}",
                "smiles": value(row, "smiles", "canonical_smiles"),
                "inchi": value(row, "inchi", "standard_inchi"),
                "inchi_key": inchi_key,
                "material_form": value(row, "material_form") or "active_moiety",
            },
        }
    ]


def ema(row: dict[str, str]) -> list[dict]:
    category = normalized(value(row, "category") or "")
    if category and category != "human":
        raise LookupError("not_human_medicine")
    status = value(
        row, "medicine_status", "authorisation status", "authorization status", "status"
    )
    normalized_status = normalized(status or "")
    if "withdraw" in normalized_status:
        marketing_status = "withdrawn"
    elif "discontinu" in normalized_status:
        marketing_status = "discontinued"
    elif "authoris" in normalized_status or "authoriz" in normalized_status:
        marketing_status = "active"
    elif status:
        raise LookupError("unsupported_medicine_status")
    else:
        marketing_status = "unknown"
    medicine_type = normalized(value(row, "medicine type", "product type", "category") or "")
    explicit_biologic = any(
        normalized(value(row, field) or "") in {"1", "true", "yes"}
        for field in ("advanced_therapy", "biosimilar")
    )
    pharmacotherapy = normalized(value(row, "pharmacotherapeutic_group_human") or "")
    if (
        explicit_biologic
        or "vaccine" in pharmacotherapy
        or any(
            kind in medicine_type
            for kind in ("biologic", "vaccine", "cell therapy", "gene therapy")
        )
    ):
        raise LookupError("biologic_or_advanced_therapy")
    product_name = value(row, "name_of_medicine", "medicine name", "product name", "name")
    active_substances = values(value(row, "active substance", "active substances", "substance"))
    product_number = value(
        row,
        "ema_product_number",
        "product number",
        "ema product number",
        "marketing authorisation number",
    )
    if not product_name or not active_substances or not product_number:
        raise ValueError(
            "EMA row requires medicine name, active substance, and product/application number"
        )
    product = {
        "jurisdiction": "EU-EMA",
        "application_number": product_number,
        "product_number": product_number,
        "trade_name": product_name,
        "marketing_status": marketing_status,
        "approval_date": value(
            row,
            "marketing_authorisation_date",
            "european_commission_decision_date",
            "marketing authorisation date",
            "authorization date",
            "authorisation date",
        ),
        "applicant": value(
            row,
            "marketing_authorisation_developer_applicant_holder",
            "marketing authorisation holder",
            "authorization holder",
            "applicant",
        ),
        "source_url": value(row, "medicine_url", "medicine url", "url"),
    }
    return [
        {
            "preferred_name": substance,
            "aliases": [{"value": product_name, "type": "brand_name"}],
            # A product identifier can legitimately point to multiple active
            # substances. Keep it on regulatory_product instead of treating it
            # as a drug-level identity key.
            "identifiers": {},
            # EMA does not provide a structure or a dependable small-molecule
            # classifier. Enrich a structured catalogue drug; never create one.
            "requires_existing_drug": True,
            "regulatory_products": [product],
        }
        for substance in active_substances
    ]


CONVERTERS: dict[str, Callable[[dict[str, str]], list[dict]]] = {
    "pubchem": pubchem,
    "unichem": unichem,
    "ema": ema,
}


def convert(
    source: str,
    input_path: Path,
    output_path: Path,
    report_path: Path,
    artifact_path: Path | None = None,
) -> dict:
    converter = CONVERTERS[source]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    reasons: Counter[str] = Counter()
    report = {
        "source": source,
        "input": str((artifact_path or input_path).resolve()),
        "input_rows": 0,
        "accepted_rows": 0,
        "accepted_records": 0,
        "excluded_rows": 0,
        "rejected_rows": 0,
    }
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as output:
            for line_number, row in enumerate(rows(input_path), 2):
                report["input_rows"] += 1
                try:
                    records = converter(row)
                except LookupError as error:
                    report["excluded_rows"] += 1
                    reasons[str(error)] += 1
                    continue
                except (TypeError, ValueError) as error:
                    report["rejected_rows"] += 1
                    reasons[f"line_{line_number}:{error}"] += 1
                    continue
                report["accepted_rows"] += 1
                for record in records:
                    output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    report["accepted_records"] += 1
        os.replace(partial, output_path)
    finally:
        partial.unlink(missing_ok=True)
    report["reason_counts"] = dict(sorted(reasons.items()))
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=sorted(CONVERTERS))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--storage-policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args(argv)
    try:
        original_input = args.input.resolve()
        result = convert(
            args.source,
            stage_file(original_input, StoragePolicy.load(args.storage_policy.resolve())),
            args.output,
            args.report,
            original_input,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["rejected_rows"] == 0 else 2
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
