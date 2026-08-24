from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.bulk_pipeline import connect, refresh_coverage, register_sources
from scripts.ingest_performed_route import import_artifact, validate_artifact


ROOT = Path(__file__).resolve().parents[1]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def write_manifest(directory: Path, step_count: int) -> None:
    files = []
    for name in ("performed_route.jsonl", "resolved_compounds.jsonl", "evidence_text.txt"):
        path = directory / name
        files.append(
            {
                "file": name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
        )
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "counts": {"routes": 1, "performed_steps": step_count, "accepted_routes": 0},
                "files": files,
                "safety": {
                    "accepts_route": False,
                    "human_review_required": True,
                    "reported_and_derived_yields_separated": True,
                    "missing_byproducts_not_inferred_as_evidence": True,
                    "terminal_drug_form_gap_explicit": True,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def make_artifact(directory: Path) -> Path:
    directory.mkdir()
    compounds = [
        {
            "compound_id": "PUBCHEM:1",
            "pubchem_cid": 1,
            "preferred_name": "Intermediate one",
            "smiles": "CCO",
            "inchi": "InChI=1S/C2H6O",
            "inchi_key": "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            "connectivity_key": "LFQSCWFLJHTTHZ",
            "molecular_formula": "C2H6O",
            "molecular_weight": 46.07,
            "review_status": "unreviewed",
        },
        {
            "compound_id": "PUBCHEM:2",
            "pubchem_cid": 2,
            "preferred_name": "Intermediate two",
            "smiles": "CC=O",
            "inchi": "InChI=1S/C2H4O",
            "inchi_key": "IKHGUXGNUITLKF-UHFFFAOYSA-N",
            "connectivity_key": "IKHGUXGNUITLKF",
            "molecular_formula": "C2H4O",
            "molecular_weight": 44.05,
            "review_status": "unreviewed",
        },
        {
            "compound_id": "PUBCHEM:3",
            "pubchem_cid": 3,
            "preferred_name": "Unresolved terminal salt",
            "smiles": "CC(=O)O.[Na+]",
            "inchi": "InChI=1S/C2H4O2.Na",
            "inchi_key": "VMHLLURERBWHNL-UHFFFAOYSA-N",
            "connectivity_key": "VMHLLURERBWHNL",
            "molecular_formula": "C2H3NaO2",
            "molecular_weight": 82.03,
            "review_status": "unreviewed",
        },
    ]
    write_jsonl(directory / "resolved_compounds.jsonl", compounds)
    texts = ["Example step one produced intermediate two.", "Example step two produced the terminal salt."]
    evidence = "\n\n".join(texts)
    (directory / "evidence_text.txt").write_text(evidence, encoding="utf-8")
    steps = []
    start = 0
    chain = [(compounds[0], compounds[1]), (compounds[1], compounds[2])]
    for order, ((substrate, product), text) in enumerate(zip(chain, texts), 1):
        end = start + len(text)
        steps.append(
            {
                "step_id": f"fixture-step:{order}",
                "step_order": order,
                "label": str(order),
                "evidence_char_start": start,
                "evidence_char_end": end,
                "evidence_text": text,
                "evidence_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "source_artifact_sha256": "a" * 64,
                "substrate_compound": substrate,
                "product_compound": product,
                "other_participants": [],
                "reported": {
                    "substrate_mass_g": 10.0,
                    "substrate_mol": 0.1,
                    "product_mass_g": 8.0,
                    "yield_percent": None if order == 1 else 80.0,
                    "purity_percent": 99.0 if order == 2 else None,
                    "purity_qualifier": "=" if order == 2 else None,
                    "temperature_c": [20, 30],
                    "duration_h": [1, 2],
                },
                "derived": {
                    "molar_recovery_percent_assuming_one_to_one": 81.0,
                    "status": "calculated_not_reported",
                },
                "validation": {"atom_balance_status": "incomplete_reported_participants"},
                "transformation_class": f"fixture_transformation_{order}",
                "evidence_status": "performed",
                "review_status": "needs_review",
                "accepted_chemistry": False,
            }
        )
        start = end + 2
    route = {
        "route_candidate_id": "performed-route:fixture",
        "publication_number": "WO-2-A3",
        "source_publication_number": "WO-1-A1",
        "source_url": "https://example.test/WO-1-A1",
        "drug_id": "drug:fixture",
        "catalogue_target_compound_id": "CHEMBL-FIXTURE",
        "catalogue_target_name": "Fixture drug",
        "demonstrated_terminal_compound": compounds[-1],
        "route_scope": "demonstrated_intermediate_route",
        "steps": steps,
        "continuity": {"all_adjacent_steps_connected": True},
        "terminal_gap": {
            "status": "coverage_gap",
            "reason": "Terminal form is not proven equivalent to the catalogue drug.",
        },
        "review_status": "needs_review",
        "accepted_route": False,
    }
    write_jsonl(directory / "performed_route.jsonl", [route])
    write_manifest(directory, len(steps))
    return directory


def make_database(path: Path):
    db = connect(path, ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    db.executescript(
        """
        INSERT INTO source_release
          (release_id, source_id, released_on, acquired_at, parser_version, notes)
        VALUES ('surechembl_bulk:fixture', 'surechembl_bulk', 'fixture', '2026-01-01', 'fixture', 'fixture');
        INSERT INTO patent_family
          (family_id, family_type, source_id, confidence)
        VALUES ('family:fixture', 'source_reported', 'surechembl_bulk', 1.0);
        INSERT INTO patent_document
          (publication_number, country_code, kind_code, source_id)
        VALUES ('WO-1-A1', 'WO', 'A1', 'epo_ops'), ('WO-2-A3', 'WO', 'A3', 'surechembl_bulk');
        INSERT INTO patent_family_member VALUES
          ('family:fixture', 'WO-1-A1', 'family_text_source'),
          ('family:fixture', 'WO-2-A3', 'member');
        INSERT INTO active_moiety
          (active_moiety_id, preferred_name, structure_key, structure_source, review_status)
        VALUES ('moiety:fixture', 'Fixture drug', 'FIXTURE', 'fixture', 'unreviewed');
        INSERT INTO compound
          (compound_id, preferred_name, inchi_key, connectivity_key, active_moiety_id,
           material_form, source_id, review_status)
        VALUES ('CHEMBL-FIXTURE', 'Fixture drug', 'AAAAAAAAAAAAAA-BBBBBBBBBB-C',
                'AAAAAAAAAAAAAA', 'moiety:fixture', 'active_moiety', 'chembl_snapshot', 'unreviewed');
        INSERT INTO drug_entity
          (drug_id, preferred_name, active_moiety_id, modality, review_status)
        VALUES ('drug:fixture', 'Fixture drug', 'moiety:fixture', 'small_molecule', 'unreviewed');
        INSERT INTO drug_compound VALUES
          ('drug:fixture', 'CHEMBL-FIXTURE', 'active_moiety', 'unreviewed');
        INSERT INTO patent_candidate
          (candidate_id, drug_id, compound_id, publication_number, source_release_id,
           source_compound_id, match_type, confidence, review_status, created_at)
        VALUES ('candidate:fixture', 'drug:fixture', 'CHEMBL-FIXTURE', 'WO-2-A3',
                'surechembl_bulk:fixture', 'source:fixture', 'exact_structure', 1.0,
                'needs_review', '2026-01-01');
        """
    )
    db.commit()
    return db


def test_import_is_idempotent_family_aware_and_review_only(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-performed-route")
        statuses = refresh_coverage(db)
    assert result["accepted_routes"] == 0
    assert result["new_compounds"] == 3
    assert statuses == {"examples_extracted": 1}
    coverage = db.execute(
        "SELECT status, examples_extracted, routes_under_review FROM drug_coverage WHERE drug_id='drug:fixture'"
    ).fetchone()
    assert tuple(coverage) == ("examples_extracted", 1, 0)
    assert db.execute("SELECT count(*) FROM process_route").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM process_step").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM reaction_instance").fetchone()[0] == 2
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE is_synthetic=0"
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM process_route WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE yield_percent IS NOT NULL"
    ).fetchone()[0] == 1
    relationship = db.execute("SELECT * FROM compound_relationship").fetchone()
    assert relationship["relationship_type"] == "possible_salt_or_form_of"
    assert relationship["review_status"] == "needs_review"
    assert db.execute(
        "SELECT count(*) FROM drug_compound WHERE compound_id='PUBCHEM:3'"
    ).fetchone()[0] == 0

    before = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "compound",
            "evidence_span",
            "process_route",
            "process_step",
            "reaction_instance",
            "reaction_participant",
            "reaction_condition",
            "quantity_observation",
            "outcome_observation",
        )
    }
    with db:
        rerun = import_artifact(db, validated, "fixture-performed-route")
        refresh_coverage(db)
    after = {
        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert rerun["accepted_routes"] == 0
    assert after == before
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


def test_validation_rejects_wrong_evidence_offsets(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][0]["evidence_char_end"] += 1
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    with pytest.raises(ValueError, match="evidence offset mismatch"):
        validate_artifact(artifact)


def test_validation_rejects_accepted_chemistry(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][0]["accepted_chemistry"] = True
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    with pytest.raises(ValueError, match="unsafe evidence/review status"):
        validate_artifact(artifact)


def read_jsonl_fixture(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def make_exact_terminal_artifact(directory: Path) -> Path:
    artifact = make_artifact(directory)
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    exact_target = {
        "compound_id": "CHEMBL-FIXTURE",
        "preferred_name": "Fixture drug",
    }
    route["steps"][-1]["product_compound"] = exact_target
    route["demonstrated_terminal_compound"] = exact_target
    route["terminal_gap"] = {
        "status": "not_applicable",
        "reason": "The demonstrated terminal structure is the catalogue target.",
    }
    write_jsonl(route_path, [route])

    compounds_path = artifact / "resolved_compounds.jsonl"
    compounds = [
        record
        for record in read_jsonl_fixture(compounds_path)
        if record["compound_id"] != "PUBCHEM:3"
    ]
    write_jsonl(compounds_path, compounds)
    write_manifest(artifact, 2)
    return artifact


def test_exact_terminal_target_advances_only_to_routes_under_review(tmp_path):
    artifact = make_exact_terminal_artifact(tmp_path / "artifact")
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-exact-terminal-route")
        statuses = refresh_coverage(db)

    assert result["terminal_matches_catalogue_target"] is True
    assert result["accepted_routes"] == 0
    assert statuses == {"routes_under_review": 1}
    assert db.execute("SELECT count(*) FROM compound_relationship").fetchone()[0] == 0
    coverage = db.execute(
        "SELECT status, examples_extracted, routes_under_review, complete_reviewed_route "
        "FROM drug_coverage WHERE drug_id='drug:fixture'"
    ).fetchone()
    assert tuple(coverage) == ("routes_under_review", 1, 1, 0)
    assert db.execute(
        "SELECT count(*) FROM process_route WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    db.close()


def test_validation_rejects_false_gap_for_exact_terminal_target(tmp_path):
    artifact = make_exact_terminal_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["terminal_gap"]["status"] = "coverage_gap"
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    with pytest.raises(ValueError, match="exact terminal target must declare no terminal gap"):
        validate_artifact(artifact)


def test_import_preserves_explicit_participant_amounts(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][0]["other_participants"] = [
        {
            "compound_id": "PUBCHEM:2",
            "role": "catalyst",
            "stoichiometry": 0.1,
            "amount_value": 0.01,
            "amount_unit": "mol",
            "evidence_status": "explicit_name",
            "review_status": "unreviewed",
        }
    ]
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-participant-amount")

    row = db.execute(
        "SELECT stoichiometry, amount_value, amount_unit FROM reaction_participant "
        "WHERE reaction_id=? AND compound_id='PUBCHEM:2' AND role='catalyst'",
        (result["reaction_ids"][0],),
    ).fetchone()
    assert tuple(row) == (0.1, 0.01, "mol")
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    db.close()


def test_import_preserves_explicit_product_amount(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][1]["reported"]["product_mol"] = 0.08
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-product-amount")

    row = db.execute(
        "SELECT original_value, original_unit, material_compound_id "
        "FROM quantity_observation WHERE step_id=? AND quantity_kind='product_amount'",
        (result["step_ids"][1],),
    ).fetchone()
    assert tuple(row) == (0.08, "mol", "PUBCHEM:3")
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    db.close()


def make_upstream_artifact(directory: Path) -> Path:
    artifact = make_artifact(directory)
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["route_scope"] = "demonstrated_upstream_route"
    route["terminal_gap"] = {
        "status": "coverage_gap",
        "gap_kind": "upstream_precursor",
        "relationship_type": "none",
        "reason": "The demonstrated route ends at an upstream precursor.",
    }
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    return artifact


def test_upstream_precursor_does_not_create_false_form_relationship(tmp_path):
    artifact = make_upstream_artifact(tmp_path / "artifact")
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-upstream-route")
        statuses = refresh_coverage(db)

    assert result["terminal_matches_catalogue_target"] is False
    assert result["accepted_routes"] == 0
    assert statuses == {"examples_extracted": 1}
    assert db.execute("SELECT count(*) FROM compound_relationship").fetchone()[0] == 0
    route = db.execute(
        "SELECT target_compound_id, review_status FROM process_route WHERE route_id=?",
        (result["route_id"],),
    ).fetchone()
    assert tuple(route) == ("PUBCHEM:3", "needs_review")
    db.close()


def test_validation_rejects_relationship_free_non_upstream_gap(tmp_path):
    artifact = make_upstream_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["terminal_gap"]["gap_kind"] = "unresolved_drug_form"
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    with pytest.raises(
        ValueError, match="relationship-free terminal gap must be an upstream precursor"
    ):
        validate_artifact(artifact)


def test_import_supports_additional_consumed_reactant(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][0]["other_participants"] = [
        {
            "compound_id": "PUBCHEM:3",
            "role": "consumed",
            "stoichiometry": 1.1,
            "amount_value": 0.11,
            "amount_unit": "mol",
            "evidence_status": "explicit_name_and_amount",
            "review_status": "unreviewed",
        }
    ]
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-additional-consumed")

    row = db.execute(
        "SELECT stoichiometry, amount_value, amount_unit FROM reaction_participant "
        "WHERE reaction_id=? AND compound_id='PUBCHEM:3' AND role='consumed'",
        (result["reaction_ids"][0],),
    ).fetchone()
    assert tuple(row) == (1.1, 0.11, "mol")
    assert db.execute(
        "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
    ).fetchone()[0] == 0
    db.close()


def test_named_material_mass_is_not_assigned_to_normalized_compound(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][0]["reported"]["substrate_mass_g"] = None
    route["steps"][0]["reported"]["named_starting_material_mass_g"] = 4900.0
    route["steps"][0]["reported"]["named_starting_material_name"] = (
        "Patent-named salt with unresolved ionic representation"
    )
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        result = import_artifact(db, validated, "fixture-named-material-mass")

    row = db.execute(
        "SELECT original_value, original_unit, material_compound_id "
        "FROM quantity_observation WHERE step_id=? "
        "AND quantity_kind='named_starting_material_mass'",
        (result["step_ids"][0],),
    ).fetchone()
    assert tuple(row) == (4900.0, "g", None)
    assert db.execute(
        "SELECT count(*) FROM quantity_observation WHERE step_id=? "
        "AND quantity_kind='substrate_mass'",
        (result["step_ids"][0],),
    ).fetchone()[0] == 0
    db.close()


def test_derived_compound_retains_non_pubchem_provenance(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    compounds_path = artifact / "resolved_compounds.jsonl"
    compounds = read_jsonl_fixture(compounds_path)
    compounds[0].pop("pubchem_cid")
    compounds[0]["compound_id"] = "DERIVED:fixture-components"
    compounds[0]["compound_source_id"] = "epo_ops"
    compounds[0]["structure_source"] = "rdkit_disconnected_named_components"
    compounds[0]["toolkit_name"] = "rdkit"
    compounds[0]["toolkit_version"] = "fixture"
    write_jsonl(compounds_path, compounds)

    route_path = artifact / "performed_route.jsonl"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    route["steps"][0]["substrate_compound"]["compound_id"] = "DERIVED:fixture-components"
    write_jsonl(route_path, [route])
    write_manifest(artifact, 2)
    validated = validate_artifact(artifact)
    db = make_database(tmp_path / "rxn2.sqlite")

    with db:
        import_artifact(db, validated, "fixture-derived-provenance")

    compound = db.execute(
        "SELECT source_id, active_moiety_id FROM compound "
        "WHERE compound_id='DERIVED:fixture-components'"
    ).fetchone()
    assert compound["source_id"] == "epo_ops"
    assert compound["active_moiety_id"].startswith("derived-moiety:")
    assert db.execute(
        "SELECT structure_source FROM active_moiety WHERE active_moiety_id=?",
        (compound["active_moiety_id"],),
    ).fetchone()[0] == "rdkit_disconnected_named_components"
    assert tuple(db.execute(
        "SELECT toolkit_name, toolkit_version FROM compound_property "
        "WHERE compound_id='DERIVED:fixture-components'"
    ).fetchone()) == ("rdkit", "fixture")
    db.close()
