#!/usr/bin/env python3
"""Build the performed Bosentan-to-monohydrate process artifact from native EPO XML."""

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
SOURCE = DRIVE / "patents" / "epo-ops" / "batch-50-v1" / "WO-2013136110-A1"
OUTPUT = DRIVE / "data" / "processed" / "epo_ops" / "performed-route-bosentan-2026-08-17-v1"
EXPECTED_EVIDENCE_SHA256 = "067445b3f022c95614461fc34e30c6595e6e4d0c949ecfc99d295bc8027723a4"
SOURCE_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/WO.2013136110.A1/description"


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


def paragraph_map(path: Path) -> dict[str, str]:
    result = {}
    for element in ET.parse(path).getroot().iter():
        if element.tag.rsplit("}", 1)[-1] != "p":
            continue
        text = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
        match = re.match(r"^(\[\d{4}\])", text)
        if match:
            result[match.group(1)] = text
    return result


def derived_compound(compound_id: str, name: str, smiles: str, material_form: str, parent: str | None = None) -> dict:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"RDKit rejected structure for {compound_id}")
    inchi_key = Chem.MolToInchiKey(molecule)
    record = {
        "compound_id": compound_id,
        "preferred_name": name,
        "smiles": Chem.MolToSmiles(molecule, isomericSmiles=True),
        "inchi": Chem.MolToInchi(molecule),
        "inchi_key": inchi_key,
        "connectivity_key": inchi_key.split("-", 1)[0],
        "molecular_formula": rdMolDescriptors.CalcMolFormula(molecule),
        "molecular_weight": round(Descriptors.MolWt(molecule), 3),
        "material_form": material_form,
        "review_status": "needs_review",
        "compound_source_id": "epo_ops",
        "structure_source": "rdkit_from_unambiguous_patent_name_and_bosentan_scaffold",
        "toolkit_name": "rdkit",
        "toolkit_version": Chem.rdBase.rdkitVersion,
        "source_url": SOURCE_URL,
    }
    if parent:
        record["active_moiety_compound_id"] = parent
    return record


def pubchem_record(cid: int, title: str, smiles: str, inchi: str, inchi_key: str, formula: str, weight: float) -> dict:
    return {
        "compound_id": f"PUBCHEM:{cid}",
        "pubchem_cid": cid,
        "preferred_name": title,
        "smiles": smiles,
        "inchi": inchi,
        "inchi_key": inchi_key,
        "connectivity_key": inchi_key.split("-", 1)[0],
        "molecular_formula": formula,
        "molecular_weight": weight,
        "review_status": "unreviewed",
        "compound_source_id": "pubchem_bulk",
        "structure_source": "pubchem_pug_rest",
        "toolkit_name": "source_reported",
        "toolkit_version": "pubchem-pug-rest",
        "source_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}",
    }


def main() -> None:
    description = SOURCE / "description.xml"
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    description_item = next(item for item in manifest["artifacts"] if item["file"] == "description.xml")
    source_sha = sha256_file(description)
    if source_sha != description_item["sha256"] or description.stat().st_size != description_item["size_bytes"]:
        raise ValueError("Bosentan native XML checksum or size mismatch")

    paragraphs = paragraph_map(description)
    required = tuple(f"[{number:04d}]" for number in range(85, 95))
    missing = [key for key in required if key not in paragraphs]
    if missing:
        raise ValueError(f"missing Bosentan evidence paragraphs: {missing}")
    whole_evidence = "\n\n".join(paragraphs[key] for key in required)
    if sha256_bytes(whole_evidence.encode("utf-8")) != EXPECTED_EVIDENCE_SHA256:
        raise ValueError("Bosentan evidence block changed from the selected candidate")

    step_texts = [
        "\n\n".join(paragraphs[f"[{number:04d}]"] for number in range(85, 89)),
        "\n\n".join(paragraphs[f"[{number:04d}]"] for number in range(89, 92)),
        "\n\n".join(paragraphs[f"[{number:04d}]"] for number in range(92, 95)),
    ]
    evidence_text = "\n\n".join(step_texts)
    if evidence_text != whole_evidence:
        raise ValueError("step segmentation failed to preserve the complete evidence block")
    offsets = []
    cursor = 0
    for text in step_texts:
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text) + 2

    starting_material = derived_compound(
        "DERIVED:BOSENTAN-DICHLORO-BIPYRIMIDINE",
        "4,6-dichloro-5-(2-methoxyphenoxy)-2-(pyrimidin-2-yl)pyrimidine",
        "COc1ccccc1Oc1c(Cl)nc(-c2ncccn2)nc1Cl",
        "active_moiety",
    )
    sulfonamide = derived_compound(
        "DERIVED:4-TERT-BUTYLBENZENESULFONAMIDE",
        "4-tert-butylbenzenesulfonamide",
        "CC(C)(C)c1ccc(S(N)(=O)=O)cc1",
        "active_moiety",
    )
    neutral_intermediate = derived_compound(
        "DERIVED:BOSENTAN-CHLORO-INTERMEDIATE",
        "Bosentan chlorinated sulfonamide intermediate",
        "COc1ccccc1Oc1c(NS(=O)(=O)c2ccc(C(C)(C)C)cc2)nc(-c2ncccn2)nc1Cl",
        "active_moiety",
    )
    potassium_intermediate = derived_compound(
        "DERIVED:BOSENTAN-CHLORO-INTERMEDIATE-POTASSIUM-SALT",
        "Bosentan chlorinated sulfonamide intermediate potassium salt",
        "COc1ccccc1Oc1c([N-]S(=O)(=O)c2ccc(C(C)(C)C)cc2)nc(-c2ncccn2)nc1Cl.[K+]",
        "salt",
        neutral_intermediate["compound_id"],
    )
    hydrate = derived_compound(
        "DERIVED:BOSENTAN-MONOHYDRATE",
        "Bosentan monohydrate",
        "COc1ccccc1Oc1c(NS(=O)(=O)c2ccc(C(C)(C)C)cc2)nc(-c2ncccn2)nc1OCCO.O",
        "hydrate",
        "CHEMBL957",
    )
    anisole = pubchem_record(7519, "Anisole", "COC1=CC=CC=C1", "InChI=1S/C7H8O/c1-8-7-5-3-2-4-6-7/h2-6H,1H3", "RDOXTESZEPMUJZ-UHFFFAOYSA-N", "C7H8O", 108.14)
    ethylene_glycol = pubchem_record(174, "Ethylene Glycol", "C(CO)O", "InChI=1S/C2H6O2/c3-1-2-4/h3-4H,1-2H2", "LYCAIKOWRPUZTN-UHFFFAOYSA-N", "C2H6O2", 62.07)

    route_candidate_id = stable("performed-route", "WO-2013136110-A1", "paragraphs-0085-0094")
    step_ids = [stable("bosentan-step", route_candidate_id, order) for order in (1, 2, 3)]

    def evidence_fields(index: int, label: str) -> dict:
        text = step_texts[index]
        start, end = offsets[index]
        return {
            "step_id": step_ids[index],
            "step_order": index + 1,
            "label": label,
            "evidence_char_start": start,
            "evidence_char_end": end,
            "evidence_text": text,
            "evidence_text_sha256": sha256_bytes(text.encode("utf-8")),
            "source_artifact_sha256": source_sha,
        }

    steps = [
        {
            **evidence_fields(0, "example-1-step-A-paragraphs-0085-0088"),
            "substrate_compound": starting_material,
            "product_compound": potassium_intermediate,
            "other_participants": [
                {"compound_id": sulfonamide["compound_id"], "role": "consumed", "amount_value": 213.0, "amount_unit": "g", "evidence_status": "explicit_mass_with_internally_inconsistent_reported_moles", "review_status": "needs_review"},
                {"compound_id": "PUBCHEM:11430", "role": "base", "amount_value": 79.0, "amount_unit": "g", "evidence_status": "explicit_name_and_amount", "review_status": "unreviewed"},
                {"compound_id": anisole["compound_id"], "role": "solvent", "amount_value": 1.25, "amount_unit": "L", "evidence_status": "explicit_name_and_amount", "review_status": "unreviewed"},
            ],
            "reported": {"substrate_mass_g": 100.0, "substrate_mol": 0.286, "product_mass_g": 161.0, "product_mol": None, "yield_percent": 96.2, "purity_percent": 99.7, "purity_qualifier": "=", "temperature_c": [140.0, 140.0], "duration_h": [3.0, 3.0]},
            "derived": {"status": "reported_mass_mole_yield_values_not_reconciled", "mass_balance_flag": "manual_review_required"},
            "validation": {
                "chain_continuity_with_previous_step": True,
                "atom_balance_status": "candidate_structures_connect_by_sulfonamide_substitution",
                "reported_quantity_conflicts": ["213 g sulfonamide is inconsistent with the simultaneously reported 0.286 mol for molecular weight 213.302", "161 g isolated potassium intermediate is not numerically consistent with the reported 96.2 percent yield for the derived 1:1 potassium salt"],
                "source_text_ambiguity": "The native text contains benzyl/phenoxy and pyridinyl/pyrimidinyl spelling artifacts; the derived scaffold remains needs_review.",
            },
            "transformation_class": "sulfonamide_nucleophilic_aromatic_substitution_candidate",
            "evidence_status": "performed", "review_status": "needs_review", "accepted_chemistry": False,
        },
        {
            **evidence_fields(1, "example-1-step-B-paragraphs-0089-0091"),
            "substrate_compound": potassium_intermediate,
            "product_compound": {"compound_id": "CHEMBL957", "preferred_name": "BOSENTAN"},
            "other_participants": [
                {"compound_id": ethylene_glycol["compound_id"], "role": "consumed", "amount_value": 550.6, "amount_unit": "g", "evidence_status": "explicit_name_mass_and_moles", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:14798", "role": "base", "amount_value": 14.9, "amount_unit": "g", "evidence_status": "explicit_name_mass_and_moles", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:6342", "role": "workup", "evidence_status": "explicit_name_without_amount", "review_status": "unreviewed"},
                {"compound_id": "CHEMBL1098659", "role": "workup", "evidence_status": "explicit_name_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:313", "role": "workup", "evidence_status": "explicit_concentrated_acid_without_amount", "review_status": "unreviewed"},
            ],
            "reported": {"substrate_mass_g": 50.0, "substrate_mol": 0.088, "product_mass_g": 42.0, "product_mol": None, "yield_percent": 83.1, "purity_percent": 97.6, "purity_qualifier": "=", "temperature_c": [90.0, 95.0], "duration_h": [3.0, 3.0]},
            "derived": {"status": "not_calculated_without_reported_product_moles", "mass_balance_flag": "not_assessed"},
            "validation": {"chain_continuity_with_previous_step": True, "atom_balance_status": "incomplete_reported_workup_participants", "reason": "The chlorinated potassium intermediate is converted to neutral Bosentan after ethylene-glycol substitution and acidification."},
            "transformation_class": "hydroxyethoxy_substitution_and_free_base_isolation_candidate",
            "evidence_status": "performed", "review_status": "needs_review", "accepted_chemistry": False,
        },
        {
            **evidence_fields(2, "example-1-steps-C-D-paragraphs-0092-0094"),
            "substrate_compound": {"compound_id": "CHEMBL957", "preferred_name": "BOSENTAN"},
            "product_compound": hydrate,
            "other_participants": [
                {"compound_id": "PUBCHEM:14797", "role": "base", "amount_value": 6.0, "amount_unit": "g", "evidence_status": "explicit_name_mass_and_moles", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:6342", "role": "solvent", "amount_value": 0.596, "amount_unit": "L", "evidence_status": "explicit_known_charge_and_recrystallization_volumes_excluding_wash", "review_status": "unreviewed"},
                {"compound_id": "CHEMBL1098659", "role": "workup", "amount_value": 0.0205, "amount_unit": "L", "evidence_status": "explicit_initial_KOH_solution_volume_additional_water_unquantified", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:313", "role": "workup", "evidence_status": "explicit_concentrated_acid_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:8857", "role": "crystallization_solvent", "evidence_status": "explicit_name_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:8028", "role": "crystallization_solvent", "evidence_status": "explicit_name_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:887", "role": "crystallization_solvent", "evidence_status": "explicit_name_without_amount", "review_status": "unreviewed"},
            ],
            "reported": {"substrate_mass_g": 41.0, "substrate_mol": 0.072, "product_mass_g": 25.6, "product_mol": None, "yield_percent": None, "purity_percent": 99.85, "purity_qualifier": "=", "intermediate_potassium_salt_mass_g": 38.0, "intermediate_potassium_salt_purity_percent": 99.75, "temperature_c": [20.0, 60.0], "duration_h": [2.5, 2.5]},
            "derived": {"mass_recovery_percent": 62.439, "status": "derived_from_reported_input_and_product_masses_not_a_reported_yield", "mass_balance_flag": "not_assessed"},
            "validation": {"chain_continuity_with_previous_step": True, "atom_balance_status": "not_applicable_to_salt_purification_and_hydration_cycle", "unresolved_intermediate": "Bosentan potassium salt is explicitly isolated but exact ionic representation is not asserted in this net purification step.", "reason": "The net demonstrated material operation starts with Bosentan and isolates explicitly named Bosentan monohydrate."},
            "transformation_class": "potassium_salt_purification_free_base_recovery_and_monohydrate_crystallization_candidate",
            "evidence_status": "performed", "review_status": "needs_review", "accepted_chemistry": False,
        },
    ]

    route = {
        "route_candidate_id": route_candidate_id,
        "publication_number": "WO-2013136110-A1",
        "source_publication_number": "WO-2013136110-A1",
        "source_url": SOURCE_URL,
        "drug_id": "drug:9c7993e5ea9167a181f92d61",
        "catalogue_target_compound_id": "CHEMBL957",
        "catalogue_target_name": "BOSENTAN",
        "demonstrated_terminal_compound": {"compound_id": hydrate["compound_id"], "preferred_name": hydrate["preferred_name"]},
        "route_scope": "performed_three_segment_process_from_dichlorobipyrimidine_to_bosentan_monohydrate",
        "steps": steps,
        "continuity": {"all_adjacent_steps_connected": True, "step_count": 3},
        "terminal_gap": {"status": "coverage_gap", "relationship_type": "possible_salt_or_form_of", "reason": "The patent isolates Bosentan monohydrate; CHEMBL957 is the normalized anhydrous active-moiety record."},
        "upstream_gap": {"status": "coverage_gap", "reason": "Example 1 starts from the dichlorobipyrimidine intermediate and does not demonstrate its synthesis."},
        "review_status": "needs_review",
        "accepted_route": False,
    }

    resolved = [starting_material, sulfonamide, neutral_intermediate, potassium_intermediate, hydrate, anisole, ethylene_glycol]
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=f".{OUTPUT.name}.partial-", dir=OUTPUT.parent))
    try:
        (partial / "evidence_text.txt").write_text(evidence_text, encoding="utf-8")
        (partial / "performed_route.jsonl").write_text(json.dumps(route, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        (partial / "resolved_compounds.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in resolved), encoding="utf-8")
        files = []
        for name in ("performed_route.jsonl", "resolved_compounds.jsonl", "evidence_text.txt"):
            path = partial / name
            files.append({"file": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
        output_manifest = {
            "dataset": "RXN2 performed Bosentan monohydrate process",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extractor_version": "performed-route-bosentan-v1",
            "status": "succeeded",
            "counts": {"routes": 1, "performed_steps": 3, "resolved_route_compounds": len(resolved), "accepted_routes": 0},
            "source_evidence": {"publication_number": "WO-2013136110-A1", "source_publication_number": "WO-2013136110-A1", "paragraph_id": "0085:0094", "source_artifact_sha256": source_sha, "evidence_text_sha256": EXPECTED_EVIDENCE_SHA256},
            "files": files,
            "safety": {
                "accepts_route": False,
                "human_review_required": True,
                "missing_byproducts_not_inferred_as_evidence": True,
                "reported_and_derived_yields_separated": True,
                "terminal_drug_form_gap_explicit": True,
                "hydrate_not_collapsed_to_anhydrous_active_moiety": True,
                "reported_quantity_conflicts_preserved": True,
                "unresolved_potassium_purification_intermediate_not_invented": True,
            },
        }
        (partial / "manifest.json").write_text(json.dumps(output_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        partial.replace(OUTPUT)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    print(json.dumps({"artifact": str(OUTPUT), "source_sha256": source_sha, "steps": 3, "resolved_compounds": len(resolved), "accepted_routes": 0}, indent=2))


if __name__ == "__main__":
    main()
