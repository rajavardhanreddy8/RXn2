#!/usr/bin/env python3
"""Build an evidence-locked performed Paliperidone route artifact."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors


DRIVE = Path(r"I:\My Drive\RXN2")
SOURCE = DRIVE / "patents" / "epo-ops" / "batch-50-v1" / "WO-2009130710-A2"
EXAMPLES = (
    DRIVE
    / "data"
    / "processed"
    / "epo_ops"
    / "batch-50-native-xml-2026-08-17"
    / "example_blocks.jsonl"
)
OUTPUT = (
    DRIVE
    / "data"
    / "processed"
    / "epo_ops"
    / "performed-route-paliperidone-2026-08-17-v1"
)
PUBLICATION = "WO-2009130710-A2"
SOURCE_URL = (
    "https://ops.epo.org/3.2/rest-services/published-data/publication/"
    "docdb/WO.2009130710.A2/description"
)
EXPECTED_SOURCE_SHA256 = "28b3e6b88aece8da5e7d8e42a5c1a5f7629e5f57a3739f23619ee080eb8ba0ae"
EXPECTED_EVIDENCE_SHA256 = "7c45ac23276f5f2524948e6102a62edb2695bbfaa2b943406c3faf1ce0d81bb7"


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


def derived_compound(
    compound_id: str,
    name: str,
    smiles: str,
    material_form: str = "active_moiety",
    parent: str | None = None,
) -> dict:
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
        "structure_source": "rdkit_from_unambiguous_patent_name_and_paliperidone_scaffold",
        "toolkit_name": "rdkit",
        "toolkit_version": Chem.rdBase.rdkitVersion,
        "source_url": SOURCE_URL,
    }
    if parent:
        record["active_moiety_compound_id"] = parent
    return record


def load_evidence() -> str:
    for line in EXAMPLES.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if (
            record.get("publication_number") == PUBLICATION
            and record.get("start_paragraph_id") == "0129"
            and record.get("end_paragraph_id") == "0136"
        ):
            evidence = record["text"]
            if record.get("text_sha256") != EXPECTED_EVIDENCE_SHA256:
                raise ValueError("selected Paliperidone evidence manifest hash changed")
            if sha256_bytes(evidence.encode("utf-8")) != EXPECTED_EVIDENCE_SHA256:
                raise ValueError("selected Paliperidone evidence bytes changed")
            return evidence
    raise ValueError("selected Paliperidone evidence block was not found")


def main() -> None:
    description = SOURCE / "description.xml"
    source_manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    source_item = next(item for item in source_manifest["artifacts"] if item["file"] == "description.xml")
    source_sha = sha256_file(description)
    if (
        source_sha != EXPECTED_SOURCE_SHA256
        or source_sha != source_item["sha256"]
        or description.stat().st_size != source_item["size_bytes"]
    ):
        raise ValueError("Paliperidone native XML checksum or size mismatch")

    evidence_text = load_evidence()
    coupling_marker = "The residue was dissolved in methanol"
    purification_marker = "[0131] PURIFICATION PROCESS:"
    coupling_start = evidence_text.index(coupling_marker)
    purification_start = evidence_text.index(purification_marker)
    step_texts = [
        evidence_text[:coupling_start],
        evidence_text[coupling_start:purification_start],
        evidence_text[purification_start:],
    ]
    joined = "".join(step_texts)
    if joined != evidence_text:
        raise ValueError("step segmentation failed to preserve the selected evidence block")
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for text in step_texts:
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text)

    benzyloxy = derived_compound(
        "DERIVED:PALIPERIDONE-BENZYLOXY-CHLOROETHYL-PRECURSOR",
        "3-(2-chloroethyl)-9-benzyloxy-2-methyl-4H-pyrido[1,2-a]pyrimidin-4-one",
        "Cc1nc2n(c(=O)c1CCCl)CCCC2OCc1ccccc1",
    )
    hydroxy = derived_compound(
        "DERIVED:PALIPERIDONE-HYDROXY-CHLOROETHYL-INTERMEDIATE",
        "3-(2-chloroethyl)-9-hydroxy-6,7,8,9-tetrahydro-2-methyl-4H-pyrido[1,2-a]pyrimidin-4-one",
        "Cc1nc2n(c(=O)c1CCCl)CCCC2O",
    )
    piperidine = derived_compound(
        "DERIVED:PALIPERIDONE-BENZISOXAZOLE-PIPERIDINE",
        "6-fluoro-3-(4-piperidinyl)-1,2-benzisoxazole",
        "Fc1ccc2c(c1)onc2C1CCNCC1",
    )
    piperidine_hcl = derived_compound(
        "DERIVED:PALIPERIDONE-BENZISOXAZOLE-PIPERIDINE-HYDROCHLORIDE",
        "6-fluoro-3-(4-piperidinyl)-1,2-benzisoxazole hydrochloride",
        "Fc1ccc2c(c1)onc2C1CC[NH2+]CC1.[Cl-]",
        "salt",
        piperidine["compound_id"],
    )
    dipea = derived_compound(
        "DERIVED:DIISOPROPYLETHYLAMINE",
        "N,N-diisopropylethylamine",
        "CCN(C(C)C)C(C)C",
    )

    target = Chem.MolFromSmiles("Cc1nc2n(c(=O)c1CCN1CCC(c3noc4cc(F)ccc34)CC1)CCCC2O")
    if Chem.MolToInchiKey(target) != "PMXMIIMHBWHSKN-UHFFFAOYSA-N":
        raise ValueError("constructed terminal structure does not match catalogue Paliperidone")

    route_candidate_id = stable("performed-route", PUBLICATION, "paragraphs-0129-0136")
    step_ids = [stable("paliperidone-step", route_candidate_id, order) for order in (1, 2, 3)]

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
            **evidence_fields(0, "example-4-in-situ-hydrogenolysis"),
            "substrate_compound": benzyloxy,
            "product_compound": hydroxy,
            "other_participants": [
                {"compound_id": "CHEMBL1098659", "role": "solvent", "amount_value": 100.0, "amount_unit": "mL", "evidence_status": "explicit_name_and_volume", "review_status": "unreviewed"},
                {"compound_id": "CHEMBL1187", "role": "reagent", "amount_value": 100.0, "amount_unit": "mL", "evidence_status": "explicit_name_and_volume", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:783", "role": "reagent", "evidence_status": "explicit_pressure_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:1140", "role": "extraction_solvent", "amount_value": 850.0, "amount_unit": "mL", "evidence_status": "explicit_name_and_volume", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:14798", "role": "workup", "evidence_status": "explicit_50_percent_solution_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:313", "role": "workup", "evidence_status": "explicit_18_to_20_percent_solution_without_pure_acid_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:3776", "role": "solvent", "evidence_status": "carrier_for_40_mL_hydrogen_chloride_solution_not_pure_solvent_volume", "review_status": "unreviewed"},
            ],
            "reported": {"substrate_mass_g": 100.0, "substrate_mol": None, "product_mass_g": None, "product_mol": None, "yield_percent": None, "purity_percent": None, "temperature_c": [25.0, 30.0], "duration_h": None, "hydrogen_pressure_kg_cm2": [3.0, 4.0], "hcl_isopropanol_solution_volume_ml": 40.0, "hcl_treatment_duration_min": 15.0, "workup_concentration_temperature_c": [40.0, 45.0]},
            "derived": {"status": "no_yield_or_isolated_intermediate_mass_calculated", "mass_balance_flag": "not_assessed"},
            "validation": {"chain_continuity_with_previous_step": True, "atom_balance_status": "hydrogenolysis_candidate_with_unreported_benzyl_byproduct", "unresolved_named_materials": [{"name": "activated charcoal", "reported_mass_g": 7.0}, {"name": "palladium on charcoal", "reported_mass_g": 20.0, "reported_wet_percent": 50.0}, {"name": "celite", "reported_amount": None}], "material_state_uncertainty": "The hydroxy intermediate is carried forward after HCl/isopropanol treatment; the patent does not explicitly identify its protonation or isolation state, so the neutral named intermediate remains needs_review."},
            "transformation_class": "benzyl_ether_hydrogenolysis_candidate",
            "evidence_status": "performed", "review_status": "needs_review", "accepted_chemistry": False,
        },
        {
            **evidence_fields(1, "example-4-paliperidone-coupling-and-crude-isolation"),
            "substrate_compound": hydroxy,
            "product_compound": {"compound_id": "CHEMBL1621", "preferred_name": "PALIPERIDONE"},
            "other_participants": [
                {"compound_id": "PUBCHEM:887", "role": "solvent", "amount_value": 235.0, "amount_unit": "mL", "evidence_status": "explicit_name_and_volume", "review_status": "unreviewed"},
                {"compound_id": piperidine_hcl["compound_id"], "role": "consumed", "amount_value": 44.6, "amount_unit": "g", "evidence_status": "explicit_name_and_mass", "review_status": "needs_review"},
                {"compound_id": dipea["compound_id"], "role": "base", "amount_value": 112.0, "amount_unit": "g", "evidence_status": "explicit_name_and_mass", "review_status": "needs_review"},
            ],
            "reported": {"substrate_mass_g": None, "named_starting_material_mass_g": None, "substrate_mol": None, "product_mass_g": 50.0, "product_mol": None, "yield_percent": None, "purity_percent": 94.0, "purity_qualifier": "=", "temperature_c": [55.0, 60.0], "duration_h": None, "deschloro_impurity_percent": 0.005, "deschloro_impurity_qualifier": "below_detection_limit"},
            "derived": {"status": "yield_not_calculated_because_the_in_situ_intermediate_amount_is_unreported", "mass_balance_flag": "not_assessed"},
            "validation": {"chain_continuity_with_previous_step": True, "atom_balance_status": "candidate_C_N_alkylation_connects_the_two_named_scaffolds", "unreported_species": "Counterion and base salts are not enumerated in the patent example.", "reported_name_normalization": "The native XML renders 1,2-benzisoxazole characters imperfectly; the hydrochloride structure is derived from the unambiguous formula-V name and remains needs_review."},
            "transformation_class": "piperidine_N_alkylation_candidate",
            "evidence_status": "performed", "review_status": "needs_review", "accepted_chemistry": False,
        },
        {
            **evidence_fields(2, "example-4-paliperidone-acid-base-purification"),
            "substrate_compound": {"compound_id": "CHEMBL1621", "preferred_name": "PALIPERIDONE"},
            "product_compound": {"compound_id": "CHEMBL1621", "preferred_name": "PALIPERIDONE"},
            "other_participants": [
                {"compound_id": "CHEMBL1098659", "role": "solvent", "amount_value": 117.0, "amount_unit": "mL", "evidence_status": "explicit_combined_75_mL_and_42_mL_water_charges", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:313", "role": "workup", "evidence_status": "explicit_34_percent_solution_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:8028", "role": "solvent", "amount_value": 250.0, "amount_unit": "mL", "evidence_status": "explicit_name_and_volume", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:222", "role": "workup", "evidence_status": "explicit_aqueous_solution_without_amount", "review_status": "unreviewed"},
                {"compound_id": "PUBCHEM:3776", "role": "crystallization_solvent", "amount_value": 168.5, "amount_unit": "mL", "evidence_status": "explicit_name_and_volume", "review_status": "unreviewed"},
            ],
            "reported": {"substrate_mass_g": 50.0, "substrate_mol": None, "product_mass_g": 27.5, "product_mol": None, "yield_percent": None, "purity_percent": 99.8, "purity_qualifier": "=", "temperature_c": [20.0, 25.0], "duration_h": None, "first_drying_temperature_c": [50.0, 55.0], "first_drying_duration_h": 10.0, "final_drying_temperature_c": 55.0, "deschloro_impurity_percent": 0.002, "deschloro_impurity_qualifier": "below_detection_limit"},
            "derived": {"mass_recovery_percent": 55.0, "status": "derived_from_reported_crude_and_purified_masses_not_a_reported_yield", "mass_balance_flag": "not_applicable_to_purification"},
            "validation": {"chain_continuity_with_previous_step": True, "atom_balance_status": "not_applicable_to_acid_base_purification", "split_source_text": "The EPO XML splits the second ammonia adjustment and final drying temperature across a page boundary; the normalized source artifact preserves those lines."},
            "transformation_class": "acid_base_purification_and_recrystallization",
            "evidence_status": "performed", "review_status": "needs_review", "accepted_chemistry": False,
        },
    ]

    route = {
        "route_candidate_id": route_candidate_id,
        "publication_number": PUBLICATION,
        "source_publication_number": PUBLICATION,
        "source_url": SOURCE_URL,
        "drug_id": "drug:65c1af52c979a5aae171aa3e",
        "catalogue_target_compound_id": "CHEMBL1621",
        "catalogue_target_name": "PALIPERIDONE",
        "demonstrated_terminal_compound": {"compound_id": "CHEMBL1621", "preferred_name": "PALIPERIDONE"},
        "route_scope": "performed_in_situ_hydrogenolysis_coupling_and_two_stage_paliperidone_purification",
        "steps": steps,
        "continuity": {"all_adjacent_steps_connected": True, "step_count": 3},
        "terminal_gap": {"status": "not_applicable", "reason": "The final demonstrated compound has the exact catalogue Paliperidone InChIKey."},
        "upstream_gap": {"status": "coverage_gap", "reason": "Example 4 begins with the 100 g benzyloxy chloroethyl precursor and does not demonstrate its synthesis."},
        "review_status": "needs_review",
        "accepted_route": False,
    }

    resolved = [benzyloxy, hydroxy, piperidine, piperidine_hcl, dipea]
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
        manifest = {
            "dataset": "RXN2 performed Paliperidone route",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "extractor_version": "performed-route-paliperidone-v1",
            "status": "succeeded",
            "counts": {"routes": 1, "performed_steps": 3, "resolved_route_compounds": len(resolved), "accepted_routes": 0},
            "source_evidence": {"publication_number": PUBLICATION, "source_publication_number": PUBLICATION, "paragraph_id": "0129:0136", "source_artifact_sha256": source_sha, "evidence_text_sha256": EXPECTED_EVIDENCE_SHA256, "selected_evidence_span_id": "epo-example:f953d70c70f02c5e29f7a1e6", "reaction_candidate_id": "reaction-candidate:91dcfcc80bf005dc74f55ba2"},
            "files": files,
            "safety": {"accepts_route": False, "human_review_required": True, "reported_and_derived_yields_separated": True, "terminal_exact_structure_verified": True, "in_situ_intermediate_material_state_not_invented": True, "unresolved_catalyst_and_filter_aid_not_invented_as_compounds": True, "purification_mass_recovery_not_stored_as_yield": True, "upstream_coverage_gap_explicit": True},
        }
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        partial.replace(OUTPUT)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise

    print(json.dumps({"artifact": str(OUTPUT), "source_sha256": source_sha, "evidence_sha256": EXPECTED_EVIDENCE_SHA256, "steps": 3, "resolved_compounds": len(resolved), "terminal_compound_id": "CHEMBL1621", "accepted_routes": 0}, indent=2))


if __name__ == "__main__":
    main()
