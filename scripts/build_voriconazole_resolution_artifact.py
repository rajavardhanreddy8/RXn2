#!/usr/bin/env python3
"""Build the evidence-backed Voriconazole racemate-resolution route artifact."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


DRIVE = Path(r"I:\My Drive\RXN2")
SOURCE_DIR = DRIVE / "patents" / "epo-ops-voriconazole-resolution-2026-08-17" / "WO-2009024214-A1"
OUT = DRIVE / "data" / "processed" / "epo_ops" / "performed-route-voriconazole-resolution-2026-08-17-v1"
RACEMATE_ARTIFACT = DRIVE / "data" / "processed" / "epo_ops" / "performed-route-voriconazole-racemate-2026-08-17-v1"
SALT_ARTIFACT = DRIVE / "data" / "processed" / "epo_ops" / "performed-route-voriconazole-salt-cycle-2026-08-17-v1"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stable(prefix: str, *parts: object) -> str:
    body = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}:{sha256_bytes(body.encode('utf-8'))[:24]}"


def paragraph_map(path: Path) -> dict[str, str]:
    paragraphs: dict[str, str] = {}
    for element in ET.parse(path).getroot().iter():
        if element.tag.rsplit("}", 1)[-1] != "p":
            continue
        text = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
        match = re.match(r"^(\[\d{4}\])", text)
        if match:
            paragraphs[match.group(1)] = text
    return paragraphs


def compound(records: list[dict], compound_id: str) -> dict:
    return next(record for record in records if record["compound_id"] == compound_id)


def main() -> None:
    description = SOURCE_DIR / "description.xml"
    source_manifest = json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    artifact = next(item for item in source_manifest["artifacts"] if item["file"] == "description.xml")
    source_sha = sha256_file(description)
    if source_sha != artifact["sha256"] or description.stat().st_size != artifact["size_bytes"]:
        raise ValueError("EPO native-description checksum or size mismatch")

    paragraphs = paragraph_map(description)
    required = ("[0081]", "[0082]", "[0087]", "[0088]")
    missing = [marker for marker in required if marker not in paragraphs]
    if missing:
        raise ValueError(f"missing required evidence paragraphs: {missing}")

    step_1_text = f"{paragraphs['[0081]']}\n\n{paragraphs['[0082]']}"
    step_2_text = f"{paragraphs['[0087]']}\n\n{paragraphs['[0088]']}"
    evidence_text = f"{step_1_text}\n\n{step_2_text}"
    step_1_start = 0
    step_1_end = len(step_1_text)
    step_2_start = step_1_end + 2
    step_2_end = len(evidence_text)

    racemate_records = jsonl(RACEMATE_ARTIFACT / "resolved_compounds.jsonl")
    salt_records = jsonl(SALT_ARTIFACT / "resolved_compounds.jsonl")
    racemate = compound(racemate_records, "PUBCHEM:5231054")
    salt = compound(salt_records, "DERIVED:VORICONAZOLE-R-MINUS-10-CAMPHORSULFONATE-1-1")
    resolving_agent = compound(salt_records, "PUBCHEM:5771688")

    source_url = "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/WO.2009024214.A1/description"
    route_id = stable("performed-route", "WO-2009024214-A1", "paragraphs-0081-0082-0087-0088")
    step_1_id = stable("voriconazole-resolution-step", route_id, 1)
    step_2_id = stable("voriconazole-resolution-step", route_id, 2)

    route = {
        "route_candidate_id": route_id,
        "publication_number": "WO-2009024214-A1",
        "source_publication_number": "WO-2009024214-A1",
        "source_url": source_url,
        "drug_id": "drug:25bb361f7e0e3ae72e8d485a",
        "catalogue_target_compound_id": "CHEMBL638",
        "catalogue_target_name": "VORICONAZOLE",
        "demonstrated_terminal_compound": {"compound_id": "CHEMBL638", "preferred_name": "VORICONAZOLE"},
        "route_scope": "performed_kilogram_scale_resolution_of_racemic_voriconazole_via_R_camphorsulfonate_and_free_base_recovery",
        "steps": [
            {
                "step_id": step_1_id,
                "step_order": 1,
                "label": "example-17-paragraphs-0081-0082",
                "evidence_char_start": step_1_start,
                "evidence_char_end": step_1_end,
                "evidence_text": step_1_text,
                "evidence_text_sha256": sha256_bytes(step_1_text.encode("utf-8")),
                "source_artifact_sha256": source_sha,
                "substrate_compound": racemate,
                "product_compound": salt,
                "other_participants": [
                    {"compound_id": "PUBCHEM:5771688", "role": "consumed", "amount_value": 475.0, "amount_unit": "g", "evidence_status": "explicit_name_stereoisomer_and_amount", "review_status": "unreviewed"},
                    {"compound_id": "PUBCHEM:8857", "role": "solvent", "amount_value": 5.0, "amount_unit": "L", "evidence_status": "explicit_aggregate_charge_and_wash", "review_status": "unreviewed"},
                    {"compound_id": "PUBCHEM:180", "role": "crystallization_solvent", "amount_value": 16.2, "amount_unit": "L", "evidence_status": "explicit_aggregate_charge_and_washes", "review_status": "unreviewed"},
                    {"compound_id": "CHEMBL545", "role": "solvent", "amount_value": 4.9, "amount_unit": "L", "evidence_status": "explicit_aggregate_charge_and_recrystallization", "review_status": "unreviewed"},
                    {"compound_id": "DERIVED:VORICONAZOLE-R-MINUS-10-CAMPHORSULFONATE-1-1", "role": "catalyst", "amount_value": 0.1, "amount_unit": "g", "evidence_status": "explicit_seed_mass", "review_status": "needs_review"}
                ],
                "reported": {
                    "substrate_mass_g": 1100.0,
                    "substrate_mol": 3.15,
                    "product_mass_g": 656.0,
                    "product_mol": None,
                    "yield_percent": 41.0,
                    "purity_percent": None,
                    "purity_qualifier": None,
                    "optical_purity_percent": 99.6,
                    "optical_purity_qualifier": "=",
                    "temperature_c": [-5.0, 50.0],
                    "duration_h": [19.0, 19.0]
                },
                "derived": {"molar_recovery_percent_assuming_one_to_one": None, "status": "not_calculated_without_reported_product_moles", "mass_balance_flag": "not_assessed"},
                "validation": {
                    "chain_continuity_with_previous_step": True,
                    "atom_balance_status": "not_applicable_to_diastereomeric_salt_resolution",
                    "stereochemistry_status": "racemic_substrate_and_optically_enriched_named_salt_explicit_in_patent",
                    "structure_representation_status": "unreviewed_disconnected_named_components",
                    "unmodeled_explicit_materials": ["filter media"],
                    "reason": "The patent reports optical purity for the isolated salt; the neutral disconnected representation does not assert an ionic protonation site."
                },
                "transformation_class": "diastereomeric_camphorsulfonate_resolution_candidate",
                "evidence_status": "performed",
                "review_status": "needs_review",
                "accepted_chemistry": False
            },
            {
                "step_id": step_2_id,
                "step_order": 2,
                "label": "example-19-paragraphs-0087-0088",
                "evidence_char_start": step_2_start,
                "evidence_char_end": step_2_end,
                "evidence_text": step_2_text,
                "evidence_text_sha256": sha256_bytes(step_2_text.encode("utf-8")),
                "source_artifact_sha256": source_sha,
                "substrate_compound": salt,
                "product_compound": {"compound_id": "CHEMBL638", "preferred_name": "VORICONAZOLE"},
                "other_participants": [
                    {"compound_id": "PUBCHEM:6344", "role": "extraction_solvent", "amount_value": 4.0, "amount_unit": "L", "evidence_status": "explicit_aggregate_charge_and_extraction", "review_status": "unreviewed"},
                    {"compound_id": "CHEMBL1098659", "role": "workup", "amount_value": 7.5, "amount_unit": "L", "evidence_status": "explicit_aggregate_charge_and_washes", "review_status": "unreviewed"},
                    {"compound_id": "PUBCHEM:14798", "role": "base", "amount_value": 0.13, "amount_unit": "L", "evidence_status": "explicit_40_percent_solution_volume_without_density_basis", "review_status": "unreviewed"},
                    {"compound_id": "CHEMBL582", "role": "crystallization_solvent", "amount_value": 1.6, "amount_unit": "L", "evidence_status": "explicit_aggregate_solvent_exchange_and_wash", "review_status": "unreviewed"}
                ],
                "reported": {
                    "substrate_mass_g": 656.0,
                    "substrate_mol": 1.13,
                    "product_mass_g": 285.0,
                    "product_mol": None,
                    "yield_percent": 72.0,
                    "purity_percent": 99.8,
                    "purity_qualifier": "=",
                    "optical_purity_percent": 99.9,
                    "optical_purity_qualifier": ">",
                    "temperature_c": [-5.0, 20.0],
                    "duration_h": [4.5, 4.5]
                },
                "derived": {"molar_recovery_percent_assuming_one_to_one": None, "status": "not_calculated_without_reported_product_moles", "mass_balance_flag": "not_assessed"},
                "validation": {
                    "chain_continuity_with_previous_step": True,
                    "atom_balance_status": "not_applicable_to_salt_break_and_crystallization",
                    "stereochemistry_status": "exact_voriconazole_name_and_greater_than_99_9_percent_optical_purity_explicit",
                    "reason": "No bond-forming transformation is asserted; the operation recovers the free base from the isolated resolving salt."
                },
                "transformation_class": "resolved_salt_break_free_base_recovery_candidate",
                "evidence_status": "performed",
                "review_status": "needs_review",
                "accepted_chemistry": False
            }
        ],
        "continuity": {"all_adjacent_steps_connected": True, "step_count": 2},
        "terminal_gap": {"status": "not_applicable", "reason": "Example 19 isolates exact target CHEMBL638 with reported chemical and optical purity."},
        "upstream_gap": {"status": "not_applicable", "reason": "Example 17 starts from exact racemate PUBCHEM:5231054, the terminal compound of the existing performed racemate route segment."},
        "stereochemical_gap": {
            "status": "performed_evidence_found",
            "from_compound_id": "PUBCHEM:5231054",
            "to_compound_id": "CHEMBL638",
            "operation": "diastereomeric_resolution_via_R_camphorsulfonate",
            "supporting_steps": [step_1_id, step_2_id]
        },
        "review_status": "needs_review",
        "accepted_route": False
    }

    if OUT.exists():
        raise FileExistsError(OUT)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{OUT.name}.partial-", dir=OUT.parent))
    try:
        evidence_path = temp / "evidence_text.txt"
        route_path = temp / "performed_route.jsonl"
        compounds_path = temp / "resolved_compounds.jsonl"
        evidence_path.write_text(evidence_text, encoding="utf-8")
        route_path.write_text(json.dumps(route, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        compounds_path.write_text("".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in (racemate, salt, resolving_agent)), encoding="utf-8")

        files = []
        for path in (route_path, compounds_path, evidence_path):
            files.append({"file": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
        manifest = {
            "dataset": "RXN2 performed Voriconazole stereochemical-resolution route",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extractor_version": "performed-route-voriconazole-resolution-v1",
            "status": "succeeded",
            "counts": {"routes": 1, "performed_steps": 2, "resolved_route_compounds": 3, "accepted_routes": 0},
            "source_evidence": {
                "publication_number": "WO-2009024214-A1",
                "source_publication_number": "WO-2009024214-A1",
                "paragraph_id": "0081:0082,0087:0088",
                "source_artifact_sha256": source_sha,
                "evidence_text_sha256": sha256_file(evidence_path)
            },
            "files": files,
            "safety": {
                "accepts_route": False,
                "human_review_required": True,
                "missing_byproducts_not_inferred_as_evidence": True,
                "reported_and_derived_yields_separated": True,
                "terminal_drug_form_gap_explicit": True,
                "upstream_resolution_gap_explicit": False,
                "derived_salt_provenance_explicit": True,
                "ionic_protonation_not_asserted": True,
                "optical_purity_preserved_separately_from_chemical_purity": True
            }
        }
        (temp / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(OUT)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    print(json.dumps({"artifact": str(OUT), "source_sha256": source_sha, "steps": 2, "accepted_routes": 0}, indent=2))


if __name__ == "__main__":
    main()
