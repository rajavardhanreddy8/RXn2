#!/usr/bin/env python3
"""Build an unapproved performed resolution artifact for the Ropivacaine KSM."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


DRIVE = Path(r"I:\My Drive\RXN2")
SOURCE = DRIVE / "patents" / "epo-ops" / "batch-50-v1" / "WO-2009044404-A1"
OUTPUT = DRIVE / "data" / "processed" / "epo_ops" / "performed-route-ropivacaine-ksm-resolution-2026-08-17-v1"
EXPECTED_EVIDENCE_SHA256 = "a7dfb6fa120a2b86d2c6ad63643e79de67c14440973438798fe179263a106540"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable(prefix: str, *parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}:{sha256_bytes(payload.encode('utf-8'))[:24]}"


def paragraphs(path: Path) -> dict[str, str]:
    result = {}
    for element in ET.parse(path).getroot().iter():
        if element.tag.rsplit("}", 1)[-1] != "p":
            continue
        text = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
        match = re.match(r"^(\[\d{4}\])", text)
        if match:
            result[match.group(1)] = text
    return result


def derived_compound(compound_id: str, name: str, smiles: str) -> dict:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"RDKit rejected structure for {compound_id}")
    canonical = Chem.MolToSmiles(molecule, isomericSmiles=True)
    return {
        "compound_id": compound_id,
        "preferred_name": name,
        "smiles": canonical,
        "inchi": Chem.MolToInchi(molecule),
        "inchi_key": Chem.MolToInchiKey(molecule),
        "connectivity_key": Chem.MolToInchiKey(molecule).split("-", 1)[0],
        "molecular_formula": rdMolDescriptors.CalcMolFormula(molecule),
        "molecular_weight": round(Descriptors.MolWt(molecule), 3),
        "review_status": "needs_review",
        "compound_source_id": "epo_ops",
        "structure_source": "rdkit_from_unambiguous_patent_iupac_name",
        "toolkit_name": "rdkit",
        "toolkit_version": Chem.rdBase.rdkitVersion,
        "source_url": "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/WO.2009044404.A1/description",
    }


def main() -> None:
    description = SOURCE / "description.xml"
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    description_entry = next(item for item in source_manifest["artifacts"] if item["file"] == "description.xml")
    source_sha = sha256_file(description)
    if source_sha != description_entry["sha256"] or description.stat().st_size != description_entry["size_bytes"]:
        raise ValueError("native EPO description checksum or size mismatch")

    para = paragraphs(description)
    evidence_text = "\n\n".join(para[key] for key in ("[0050]", "[0051]", "[0052]"))
    evidence_sha = sha256_bytes(evidence_text.encode("utf-8"))
    if evidence_sha != EXPECTED_EVIDENCE_SHA256:
        raise ValueError(f"evidence extraction changed: {evidence_sha}")

    racemate = derived_compound(
        "DERIVED:PIPECOLOXYLIDIDE-RACEMATE",
        "N-(2,6-dimethylphenyl)piperidine-2-carboxamide (racemic pipecoloxylidide)",
        "Cc1cccc(C)c1NC(=O)C1CCCCN1",
    )
    product = derived_compound(
        "DERIVED:S-PIPECOLOXYLIDIDE",
        "(2S)-N-(2,6-dimethylphenyl)piperidine-2-carboxamide (Ropivacaine KSM)",
        "Cc1cccc(C)c1NC(=O)[C@@H]1CCCCN1",
    )
    if Chem.FindMolChiralCenters(Chem.MolFromSmiles(product["smiles"]), includeUnassigned=True) != [(11, "S")]:
        raise ValueError("derived product does not retain the patent-specified 2S center")

    route_candidate_id = stable("performed-route", "WO-2009044404-A1", "paragraphs-0050-0052")
    step_id = stable("ropivacaine-ksm-resolution-step", route_candidate_id, 1)
    source_url = "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/WO.2009044404.A1/description"
    route = {
        "route_candidate_id": route_candidate_id,
        "publication_number": "WO-2009044404-A1",
        "source_publication_number": "WO-2009044404-A1",
        "source_url": source_url,
        "drug_id": "drug:9c6328a088b21a4e3828d335",
        "catalogue_target_compound_id": "CHEMBL1077896",
        "catalogue_target_name": "ROPIVACAINE",
        "demonstrated_terminal_compound": {"compound_id": product["compound_id"], "preferred_name": product["preferred_name"]},
        "route_scope": "performed_resolution_of_racemic_pipecoloxylidide_to_the_2S_ropivacaine_KSM",
        "steps": [{
            "step_id": step_id,
            "step_order": 1,
            "label": "paragraphs-0050-0052-pipecoloxylidide-resolution-cycle",
            "evidence_char_start": 0,
            "evidence_char_end": len(evidence_text),
            "evidence_text": evidence_text,
            "evidence_text_sha256": evidence_sha,
            "source_artifact_sha256": source_sha,
            "substrate_compound": racemate,
            "product_compound": product,
            "other_participants": [
                {"compound_id": "PUBCHEM:8028", "role": "solvent", "amount_value": 7.3, "amount_unit": "L", "evidence_status": "explicit_known_THF_charges_excluding_unquantified_wash", "review_status": "unreviewed"},
                {"compound_id": "CHEMBL1098659", "role": "workup", "amount_value": 3.36, "amount_unit": "L", "evidence_status": "explicit_known_water_charges_excluding_unquantified_wash", "review_status": "unreviewed"}
            ],
            "reported": {
                "substrate_mass_g": 1000.0,
                "substrate_mol": None,
                "product_mass_g": 360.0,
                "product_mol": None,
                "yield_percent": None,
                "purity_percent": None,
                "purity_qualifier": None,
                "enantiomeric_purity_percent": 99.2,
                "enantiomeric_purity_qualifier": "=",
                "initial_salt_chiral_purity_percent": 97.0,
                "rotation_reported": 45.3,
                "melting_point_c": 129.0,
                "moisture_percent": 0.5,
                "moisture_qualifier": "<",
                "temperature_c": [5.0, 45.0],
                "duration_h": None
            },
            "derived": {
                "mass_recovery_percent": 36.0,
                "status": "derived_from_reported_input_and_product_masses_not_a_reported_yield",
                "mass_balance_flag": "not_assessed"
            },
            "validation": {
                "chain_continuity_with_previous_step": True,
                "atom_balance_status": "not_applicable_to_resolution_and_salt_break_cycle",
                "stereochemistry_status": "2S_name_and_99_2_percent_enantiomeric_purity_explicit",
                "unresolved_named_materials": [
                    "dibenzoyl tartaric acid stereoisomer not specified",
                    "intermediate (S)-pipecoloxylidide DBTA salt exact stoichiometry and stereochemistry not specified"
                ],
                "unmodeled_explicit_materials": ["activated carbon", "dilute sodium carbonate solution", "demineralized-water wash"],
                "reason": "The demonstrated resolution cycle is represented net from the unambiguous racemate to the named 2S free base; no exact DBTA salt structure is invented."
            },
            "transformation_class": "diastereomeric_resolution_and_salt_break_candidate",
            "evidence_status": "performed",
            "review_status": "needs_review",
            "accepted_chemistry": False
        }],
        "continuity": {"all_adjacent_steps_connected": True, "step_count": 1},
        "terminal_gap": {
            "status": "coverage_gap",
            "gap_kind": "upstream_precursor",
            "relationship_type": "none",
            "reason": "The example isolates the 2S pipecoloxylidide KSM, not Ropivacaine; no KSM-to-drug relationship is asserted by this artifact."
        },
        "upstream_gap": {"status": "coverage_gap", "reason": "The selected example starts from racemic pipecoloxylidide and does not demonstrate its synthesis."},
        "review_status": "needs_review",
        "accepted_route": False
    }

    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=f".{OUTPUT.name}.partial-", dir=OUTPUT.parent))
    try:
        (partial / "evidence_text.txt").write_text(evidence_text, encoding="utf-8")
        (partial / "performed_route.jsonl").write_text(json.dumps(route, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        (partial / "resolved_compounds.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in (racemate, product)), encoding="utf-8")
        files = []
        for name in ("performed_route.jsonl", "resolved_compounds.jsonl", "evidence_text.txt"):
            path = partial / name
            files.append({"file": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
        manifest = {
            "dataset": "RXN2 performed Ropivacaine KSM resolution",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extractor_version": "performed-route-ropivacaine-ksm-resolution-v1",
            "status": "succeeded",
            "counts": {"routes": 1, "performed_steps": 1, "resolved_route_compounds": 2, "accepted_routes": 0},
            "source_evidence": {"publication_number": "WO-2009044404-A1", "source_publication_number": "WO-2009044404-A1", "paragraph_id": "0050:0052", "source_artifact_sha256": source_sha, "evidence_text_sha256": evidence_sha},
            "files": files,
            "safety": {
                "accepts_route": False,
                "human_review_required": True,
                "missing_byproducts_not_inferred_as_evidence": True,
                "reported_and_derived_yields_separated": True,
                "terminal_drug_form_gap_explicit": True,
                "unresolved_DBTA_salt_not_structurally_invented": True,
                "KSM_not_mislabeled_as_Ropivacaine": True
            }
        }
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        partial.replace(OUTPUT)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    print(json.dumps({"artifact": str(OUTPUT), "source_sha256": source_sha, "evidence_sha256": evidence_sha, "accepted_routes": 0}, indent=2))


if __name__ == "__main__":
    main()
