#!/usr/bin/env python3
"""Import a validated, demonstrated multi-step route artifact into RXN2.

The importer is deliberately conservative: it never accepts chemistry, never
promotes a derived yield to a reported yield, and never links an unresolved
terminal salt or stereoisomer directly to the catalogue drug.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_SCHEMA = ROOT / "sql" / "schema.sql"
DEFAULT_SOURCES = ROOT / "configs" / "sources.json"
PARSER_VERSION = "performed-route-artifact-v1"
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
INCHI_KEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

try:
    from scripts.bulk_pipeline import (
        connect,
        json_text,
        now,
        record_ingestion_run,
        refresh_coverage,
        register_release,
        register_sources,
        stable_id,
    )
except ModuleNotFoundError:
    from bulk_pipeline import (
        connect,
        json_text,
        now,
        record_ingestion_run,
        refresh_coverage,
        register_release,
        register_sources,
        stable_id,
    )


ROLE_MAP = {
    "base": "reagent",
    "catalyst": "catalyst",
    "consumed": "consumed",
    "crystallization_solvent": "solvent",
    "extraction_solvent": "solvent",
    "reagent": "reagent",
    "solvent": "solvent",
    "solvent_reagent": "reagent",
    "wash_solvent": "solvent",
    "workup": "workup",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        records.append(value)
    return records


def validate_artifact(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "succeeded":
        raise ValueError("artifact manifest status must be succeeded")
    safety = manifest.get("safety") or {}
    if safety.get("accepts_route") is not False:
        raise ValueError("performed-route artifact must not accept the route")
    if not safety.get("human_review_required"):
        raise ValueError("performed-route artifact must require human review")
    if not safety.get("reported_and_derived_yields_separated"):
        raise ValueError("reported and derived yields must be separated")

    manifest_files: dict[str, dict[str, Any]] = {}
    for item in manifest.get("files", []):
        name = item.get("file")
        if not name or Path(name).name != name:
            raise ValueError(f"unsafe or missing manifest file name: {name!r}")
        if name in manifest_files:
            raise ValueError(f"duplicate manifest file entry: {name}")
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size != item.get("size_bytes"):
            raise ValueError(f"size mismatch: {path}")
        checksum = sha256_file(path)
        if checksum != item.get("sha256"):
            raise ValueError(f"checksum mismatch: {path}")
        manifest_files[name] = item

    required = {"performed_route.jsonl", "resolved_compounds.jsonl", "evidence_text.txt"}
    missing = required - manifest_files.keys()
    if missing:
        raise ValueError(f"manifest missing required files: {sorted(missing)}")

    routes = read_jsonl(directory / "performed_route.jsonl")
    if len(routes) != 1:
        raise ValueError(f"expected one route record, found {len(routes)}")
    route = routes[0]
    if route.get("accepted_route") is not False or route.get("review_status") != "needs_review":
        raise ValueError("route must be unaccepted and needs_review")
    terminal_id = route.get("demonstrated_terminal_compound", {}).get("compound_id")
    target_id = route.get("catalogue_target_compound_id")
    terminal_gap_status = route.get("terminal_gap", {}).get("status")
    if terminal_id == target_id:
        if terminal_gap_status != "not_applicable":
            raise ValueError("exact terminal target must declare no terminal gap")
    else:
        terminal_gap = route.get("terminal_gap", {})
        if terminal_gap_status != "coverage_gap":
            raise ValueError("terminal drug-form gap must remain explicit")
        relationship_type = terminal_gap.get("relationship_type", "possible_salt_or_form_of")
        if relationship_type not in {"possible_salt_or_form_of", "none"}:
            raise ValueError(f"unsupported terminal relationship type: {relationship_type}")
        if relationship_type == "none" and terminal_gap.get("gap_kind") != "upstream_precursor":
            raise ValueError("relationship-free terminal gap must be an upstream precursor")

    compounds = read_jsonl(directory / "resolved_compounds.jsonl")
    compound_ids = [record.get("compound_id") for record in compounds]
    if any(not value for value in compound_ids) or len(set(compound_ids)) != len(compound_ids):
        raise ValueError("resolved compound IDs must be present and unique")
    for record in compounds:
        if not INCHI_KEY.match(record.get("inchi_key") or ""):
            raise ValueError(f"invalid InChIKey value for {record['compound_id']}")
        if not record.get("smiles") or not record.get("inchi") or not record.get("connectivity_key"):
            raise ValueError(f"incomplete structure for {record['compound_id']}")
        if record.get("review_status") not in {"unreviewed", "needs_review"}:
            raise ValueError(f"unsafe compound review status for {record['compound_id']}")

    evidence_text = (directory / "evidence_text.txt").read_text(encoding="utf-8")
    steps = route.get("steps") or []
    if not steps or len(steps) != manifest.get("counts", {}).get("performed_steps"):
        raise ValueError("performed step count does not match the manifest")
    if [step.get("step_order") for step in steps] != list(range(1, len(steps) + 1)):
        raise ValueError("step order must be contiguous and one-based")
    if len({step.get("step_id") for step in steps}) != len(steps):
        raise ValueError("step IDs must be unique")

    for index, step in enumerate(steps):
        if (
            step.get("evidence_status") != "performed"
            or step.get("review_status") != "needs_review"
            or step.get("accepted_chemistry") is not False
        ):
            raise ValueError(f"unsafe evidence/review status at step {index + 1}")
        start = step.get("evidence_char_start")
        end = step.get("evidence_char_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            raise ValueError(f"invalid evidence offsets at step {index + 1}")
        text = step.get("evidence_text") or ""
        if evidence_text[start:end] != text:
            raise ValueError(f"evidence offset mismatch at step {index + 1}")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != step.get("evidence_text_sha256"):
            raise ValueError(f"evidence hash mismatch at step {index + 1}")
        if not HEX_64.match(step.get("source_artifact_sha256") or ""):
            raise ValueError(f"invalid source artifact hash at step {index + 1}")
        if index and (
            steps[index - 1]["product_compound"]["compound_id"]
            != step["substrate_compound"]["compound_id"]
        ):
            raise ValueError(f"broken route continuity before step {index + 1}")

    continuity = route.get("continuity", {})
    connected = continuity.get("all_adjacent_steps_connected", continuity.get("all_steps_connected"))
    if connected is not True:
        raise ValueError("artifact does not assert complete step continuity")
    if route["demonstrated_terminal_compound"]["compound_id"] != steps[-1]["product_compound"]["compound_id"]:
        raise ValueError("terminal compound does not match the final demonstrated product")

    return {
        "directory": directory,
        "manifest": manifest,
        "manifest_files": manifest_files,
        "route": route,
        "compounds": compounds,
        "evidence_text": evidence_text,
    }


def canonical_compound_id(db: sqlite3.Connection, record: dict[str, Any]) -> tuple[str, bool]:
    compound_id = record["compound_id"]
    existing = db.execute(
        "SELECT compound_id, inchi_key FROM compound WHERE compound_id = ?", (compound_id,)
    ).fetchone()
    if existing:
        if existing["inchi_key"] != record["inchi_key"]:
            raise ValueError(f"compound ID structure conflict: {compound_id}")
        return compound_id, False

    exact = db.execute(
        """SELECT compound_id FROM compound WHERE inchi_key = ?
           ORDER BY CASE
             WHEN review_status = 'accepted' THEN 0
             WHEN compound_id LIKE 'CHEMBL%' THEN 1
             WHEN compound_id LIKE 'PUBCHEM:%' THEN 2
             ELSE 3 END, compound_id LIMIT 1""",
        (record["inchi_key"],),
    ).fetchone()
    if exact:
        return exact["compound_id"], False

    parent_compound_id = record.get("active_moiety_compound_id")
    if parent_compound_id:
        parent_row = db.execute(
            "SELECT active_moiety_id FROM compound WHERE compound_id = ?",
            (parent_compound_id,),
        ).fetchone()
        if not parent_row:
            raise ValueError(f"unknown active-moiety compound: {parent_compound_id}")
        moiety_id = parent_row["active_moiety_id"]
        insert_new_moiety = False
    else:
        pubchem_cid = record.get("pubchem_cid")
        if pubchem_cid is not None:
            moiety_id = f"pubchem-moiety:{pubchem_cid}"
        else:
            moiety_id = f"derived-moiety:{hashlib.sha256(record['inchi_key'].encode()).hexdigest()[:24]}"
        insert_new_moiety = True
    structure_source = record.get("structure_source", "pubchem_pug_rest")
    compound_source_id = record.get("compound_source_id", "pubchem_bulk")
    toolkit_name = record.get("toolkit_name", "source_reported")
    toolkit_version = record.get("toolkit_version", "pubchem-pug-rest")
    if insert_new_moiety:
        db.execute(
            """INSERT INTO active_moiety
               (active_moiety_id, preferred_name, structure_key, structure_source, review_status)
               VALUES (?, ?, ?, ?, 'unreviewed')""",
            (moiety_id, record.get("preferred_name"), record.get("inchi_key"), structure_source),
        )
    material_form = record.get("material_form") or (
        "salt" if "." in (record.get("smiles") or "") else "unknown"
    )
    if material_form not in {
        "active_moiety",
        "co_crystal",
        "hydrate",
        "mixture",
        "salt",
        "solvate",
        "unknown",
    }:
        raise ValueError(f"unsupported material form: {material_form}")
    db.execute(
        """INSERT INTO compound
           (compound_id, preferred_name, smiles, inchi, inchi_key, connectivity_key,
            active_moiety_id, material_form, source_id, review_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed')""",
        (
            compound_id,
            record.get("preferred_name"),
            record.get("smiles"),
            record.get("inchi"),
            record.get("inchi_key"),
            record.get("connectivity_key"),
            moiety_id,
            material_form,
            compound_source_id,
        ),
    )
    db.execute(
        """INSERT INTO compound_property
           (compound_id, standardized_smiles, molecular_formula, molecular_weight,
            structure_hash, toolkit_name, toolkit_version, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            compound_id,
            record.get("smiles"),
            record.get("molecular_formula"),
            record.get("molecular_weight"),
            hashlib.sha256(record["inchi_key"].encode()).hexdigest(),
            toolkit_name,
            toolkit_version,
            now(),
        ),
    )
    return compound_id, True


def require_document_and_family(db: sqlite3.Connection, route: dict[str, Any]) -> None:
    source_publication = route["source_publication_number"]
    indexed_publication = route["publication_number"]
    for publication in (source_publication, indexed_publication):
        if not db.execute(
            "SELECT 1 FROM patent_document WHERE publication_number = ?", (publication,)
        ).fetchone():
            raise ValueError(f"patent document is not registered: {publication}")
    linked = db.execute(
        """SELECT 1 FROM patent_family_member source_member
           JOIN patent_family_member indexed_member USING (family_id)
           WHERE source_member.publication_number = ?
             AND indexed_member.publication_number = ?""",
        (source_publication, indexed_publication),
    ).fetchone()
    if not linked:
        raise ValueError("source and indexed publications are not in the same patent family")


def add_condition(
    db: sqlite3.Connection,
    reaction_id: str,
    evidence_span_id: str,
    condition_type: str,
    value: float,
    unit: str,
) -> None:
    condition_id = stable_id("reaction-condition", reaction_id, condition_type, value, unit)
    db.execute(
        """INSERT OR IGNORE INTO reaction_condition
           (condition_id, reaction_id, condition_type, value_text, numeric_value, unit, evidence_span_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (condition_id, reaction_id, condition_type, f"{value:g} {unit}", value, unit, evidence_span_id),
    )


def add_range_conditions(
    db: sqlite3.Connection,
    reaction_id: str,
    evidence_span_id: str,
    name: str,
    values: list[float] | None,
    unit: str,
) -> None:
    if not values:
        return
    low, high = float(values[0]), float(values[-1])
    if low == high:
        add_condition(db, reaction_id, evidence_span_id, name, low, unit)
    else:
        add_condition(db, reaction_id, evidence_span_id, f"{name}_min", low, unit)
        add_condition(db, reaction_id, evidence_span_id, f"{name}_max", high, unit)


def add_quantity(
    db: sqlite3.Connection,
    step_id: str,
    kind: str,
    value: float | None,
    unit: str,
    compound_id: str,
) -> None:
    if value is None:
        return
    quantity_id = stable_id("quantity-observation", step_id, kind, value, unit, compound_id)
    db.execute(
        """INSERT OR IGNORE INTO quantity_observation
           (quantity_id, step_id, quantity_kind, original_value, original_unit,
            normalized_value, normalized_unit, material_compound_id, is_range, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0.99)""",
        (quantity_id, step_id, kind, value, unit, value, unit, compound_id),
    )


def import_artifact(
    db: sqlite3.Connection,
    validated: dict[str, Any],
    release_name: str,
) -> dict[str, Any]:
    route = validated["route"]
    compounds = validated["compounds"]
    directory = validated["directory"]
    require_document_and_family(db, route)
    if not db.execute("SELECT 1 FROM drug_entity WHERE drug_id = ?", (route["drug_id"],)).fetchone():
        raise ValueError(f"unknown drug: {route['drug_id']}")
    if not db.execute(
        "SELECT 1 FROM compound WHERE compound_id = ?", (route["catalogue_target_compound_id"],)
    ).fetchone():
        raise ValueError(f"unknown catalogue target: {route['catalogue_target_compound_id']}")

    release_id, artifacts = register_release(
        db,
        "epo_ops",
        release_name,
        [directory / item["file"] for item in validated["manifest"]["files"]],
        PARSER_VERSION,
    )
    evidence_artifact_sha = validated["manifest_files"]["evidence_text.txt"]["sha256"]

    compound_map: dict[str, str] = {}
    new_compounds = 0
    reconciled_compounds = 0
    for record in compounds:
        canonical_id, inserted = canonical_compound_id(db, record)
        compound_map[record["compound_id"]] = canonical_id
        new_compounds += int(inserted)
        reconciled_compounds += int(canonical_id != record["compound_id"])

    all_references = set()
    for step in route["steps"]:
        all_references.add(step["substrate_compound"]["compound_id"])
        all_references.add(step["product_compound"]["compound_id"])
        all_references.update(item["compound_id"] for item in step.get("other_participants", []))
    for compound_id in sorted(all_references):
        if compound_id not in compound_map:
            if not db.execute("SELECT 1 FROM compound WHERE compound_id = ?", (compound_id,)).fetchone():
                raise ValueError(f"unresolved participant compound: {compound_id}")
            compound_map[compound_id] = compound_id

    terminal_id = compound_map[route["demonstrated_terminal_compound"]["compound_id"]]
    terminal_row = db.execute(
        "SELECT active_moiety_id FROM compound WHERE compound_id = ?", (terminal_id,)
    ).fetchone()
    route_fingerprint = hashlib.sha256(
        json_text(
            {
                "source_publication": route["source_publication_number"],
                "chain": [
                    [
                        compound_map[step["substrate_compound"]["compound_id"]],
                        compound_map[step["product_compound"]["compound_id"]],
                        step["evidence_text_sha256"],
                    ]
                    for step in route["steps"]
                ],
            }
        ).encode()
    ).hexdigest()
    route_id = stable_id(
        "process-route", route["route_candidate_id"], route["source_publication_number"]
    )
    existing_route = db.execute(
        "SELECT route_fingerprint FROM process_route WHERE route_id = ?", (route_id,)
    ).fetchone()
    if existing_route and existing_route["route_fingerprint"] != route_fingerprint:
        raise ValueError(f"route fingerprint conflict: {route_id}")
    db.execute(
        """INSERT OR IGNORE INTO process_route
           (route_id, active_moiety_id, target_compound_id, route_fingerprint, review_status)
           VALUES (?, ?, ?, ?, 'needs_review')""",
        (route_id, terminal_row["active_moiety_id"], terminal_id, route_fingerprint),
    )

    created_at = now()
    step_ids: list[str] = []
    reaction_ids: list[str] = []
    evidence_span_ids: list[str] = []
    for step in route["steps"]:
        source_id = route["source_publication_number"]
        evidence_span_id = stable_id(
            "evidence-span",
            source_id,
            evidence_artifact_sha,
            step["evidence_char_start"],
            step["evidence_char_end"],
            step["evidence_text_sha256"],
        )
        db.execute(
            """INSERT OR IGNORE INTO evidence_span
               (evidence_span_id, publication_number, source_id, artifact_sha256,
                section_type, paragraph_id, char_start, char_end, evidence_text,
                text_sha256, evidence_status, extraction_method, extractor_version,
                review_status, source_url, retrieved_at, license_code, redistribution_class)
               VALUES (?, ?, 'epo_ops', ?, 'example', ?, ?, ?, ?, ?, 'performed',
                       'deterministic_native_xml', ?, 'needs_review', ?, ?,
                       'EPO-OPS-terms', 'derived_products_only_under_terms')""",
            (
                evidence_span_id,
                source_id,
                evidence_artifact_sha,
                f"step-{step['label']}",
                step["evidence_char_start"],
                step["evidence_char_end"],
                step["evidence_text"],
                step["evidence_text_sha256"],
                PARSER_VERSION,
                route.get("source_url"),
                created_at,
            ),
        )

        step_id = stable_id("process-step", route_id, step["step_order"], step["label"])
        reaction_id = stable_id("reaction-instance", step_id)
        substrate_id = compound_map[step["substrate_compound"]["compound_id"]]
        product_id = compound_map[step["product_compound"]["compound_id"]]
        transformation = step["transformation_class"]
        summary = (
            f"{step['substrate_compound']['preferred_name']} to "
            f"{step['product_compound']['preferred_name']}"
        )
        db.execute(
            """INSERT OR IGNORE INTO process_step
               (step_id, route_id, evidence_span_id, step_order, transformation_key,
                product_compound_id, operation_summary, evidence_status, review_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'performed', 'needs_review')""",
            (
                step_id,
                route_id,
                evidence_span_id,
                step["step_order"],
                transformation,
                product_id,
                summary,
            ),
        )
        reported = step["reported"]
        db.execute(
            """INSERT INTO reaction_instance
               (reaction_id, reaction_name, transformation_key, evidence_span_id,
                yield_percent, demonstrated_scale_g, confidence, review_status,
                is_synthetic, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 0.90, 'needs_review', 0, ?)
               ON CONFLICT(reaction_id) DO UPDATE SET is_synthetic=excluded.is_synthetic""",
            # is_synthetic marks generated demo fixtures in the legacy API. A
            # performed patent example is real evidence, so it must remain 0.
            (
                reaction_id,
                summary,
                transformation,
                evidence_span_id,
                reported.get("yield_percent"),
                reported.get("product_mass_g"),
                created_at,
            ),
        )
        db.execute(
            """INSERT OR IGNORE INTO reaction_evidence_link
               (reaction_id, evidence_span_id, relationship_type, review_status, created_at)
               VALUES (?, ?, 'primary_example', 'needs_review', ?)""",
            (reaction_id, evidence_span_id, created_at),
        )
        db.execute(
            """INSERT OR IGNORE INTO reaction_participant
               (reaction_id, compound_id, role, stoichiometry, amount_value, amount_unit)
               VALUES (?, ?, 'consumed', NULL, ?, 'g')""",
            (reaction_id, substrate_id, reported.get("substrate_mass_g")),
        )
        db.execute(
            """INSERT OR IGNORE INTO reaction_participant
               (reaction_id, compound_id, role, stoichiometry, amount_value, amount_unit)
               VALUES (?, ?, 'produced', NULL, ?, 'g')""",
            (reaction_id, product_id, reported.get("product_mass_g")),
        )
        for participant in step.get("other_participants", []):
            role = ROLE_MAP.get(participant.get("role"))
            if not role:
                raise ValueError(f"unsupported participant role: {participant.get('role')}")
            db.execute(
                """INSERT OR IGNORE INTO reaction_participant
                   (reaction_id, compound_id, role, stoichiometry, amount_value, amount_unit)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    reaction_id,
                    compound_map[participant["compound_id"]],
                    role,
                    participant.get("stoichiometry"),
                    participant.get("amount_value"),
                    participant.get("amount_unit"),
                ),
            )

        add_range_conditions(
            db,
            reaction_id,
            evidence_span_id,
            "reaction_temperature",
            reported.get("temperature_c"),
            "degC",
        )
        add_range_conditions(
            db,
            reaction_id,
            evidence_span_id,
            "reaction_duration",
            reported.get("duration_h"),
            "h",
        )
        add_quantity(db, step_id, "substrate_mass", reported.get("substrate_mass_g"), "g", substrate_id)
        add_quantity(db, step_id, "substrate_amount", reported.get("substrate_mol"), "mol", substrate_id)
        add_quantity(db, step_id, "product_mass", reported.get("product_mass_g"), "g", product_id)
        add_quantity(db, step_id, "product_amount", reported.get("product_mol"), "mol", product_id)
        # Some patents report a mass for a named salt/material whose exact
        # molecular representation is unresolved. Preserve the observation
        # without falsely assigning that mass to the normalized active moiety.
        add_quantity(
            db,
            step_id,
            "named_starting_material_mass",
            reported.get("named_starting_material_mass_g"),
            "g",
            None,
        )
        if reported.get("yield_percent") is not None or reported.get("purity_percent") is not None:
            outcome_id = stable_id(
                "outcome-observation",
                step_id,
                reported.get("yield_percent"),
                reported.get("purity_percent"),
            )
            reported_parts = []
            if reported.get("yield_percent") is not None:
                reported_parts.append(f"reported yield {reported['yield_percent']:g}%")
            if reported.get("purity_percent") is not None:
                qualifier = reported.get("purity_qualifier") or ""
                reported_parts.append(
                    f"reported purity {qualifier}{reported['purity_percent']:g}%"
                )
            db.execute(
                """INSERT OR IGNORE INTO outcome_observation
                   (outcome_id, step_id, yield_percent, purity_percent, outcome_type,
                    original_text, confidence)
                   VALUES (?, ?, ?, ?, 'reported_patent_outcome', ?, 0.99)""",
                (
                    outcome_id,
                    step_id,
                    reported.get("yield_percent"),
                    reported.get("purity_percent"),
                    "; ".join(reported_parts),
                ),
            )
        step_ids.append(step_id)
        reaction_ids.append(reaction_id)
        evidence_span_ids.append(evidence_span_id)

    target_id = route["catalogue_target_compound_id"]
    terminal_matches_target = terminal_id == target_id
    terminal_relationship_type = route["terminal_gap"].get(
        "relationship_type", "possible_salt_or_form_of"
    )
    if not terminal_matches_target and terminal_relationship_type == "possible_salt_or_form_of":
        relationship_id = stable_id(
            "compound-relationship",
            terminal_id,
            target_id,
            terminal_relationship_type,
        )
        db.execute(
            """INSERT OR IGNORE INTO compound_relationship
               (relationship_id, subject_compound_id, object_compound_id,
                relationship_type, confidence, review_status, evidence_span_id)
               VALUES (?, ?, ?, ?, 0.50, 'needs_review', ?)""",
            (
                relationship_id,
                terminal_id,
                target_id,
                terminal_relationship_type,
                evidence_span_ids[-1],
            ),
        )
    elif not terminal_matches_target and terminal_relationship_type != "none":
        raise ValueError(f"unsupported terminal relationship type: {terminal_relationship_type}")

    counts = {
        "input_rows": 1 + len(compounds),
        "accepted_rows": 1 + len(compounds),
        "excluded_rows": 0,
        "rejected_rows": 0,
        "reason_counts": {},
        "route_records_imported_for_review": 1,
        "performed_steps_imported_for_review": len(step_ids),
        "chemistry_accepted_routes": 0,
        "new_compounds": new_compounds,
        "exact_structure_reconciliations": reconciled_compounds,
        "terminal_gap": route["terminal_gap"],
        "artifact_ids": artifacts,
    }
    record_ingestion_run(db, release_id, "epo_ops", PARSER_VERSION, counts, created_at)
    return {
        "release_id": release_id,
        "route_id": route_id,
        "step_ids": step_ids,
        "reaction_ids": reaction_ids,
        "evidence_span_ids": evidence_span_ids,
        "terminal_compound_id": terminal_id,
        "terminal_matches_catalogue_target": terminal_matches_target,
        "new_compounds": new_compounds,
        "reconciled_compounds": reconciled_compounds,
        "accepted_routes": 0,
    }


def backup_database(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    source_db = sqlite3.connect(source)
    destination_db = sqlite3.connect(destination)
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
        source_db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    parser.add_argument("--release")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    validated = validate_artifact(args.artifact_dir)
    release_name = args.release or args.artifact_dir.name
    source_db_path = args.db.resolve()
    temporary_dir = None
    working_db_path = source_db_path
    if args.dry_run:
        temporary_dir = tempfile.TemporaryDirectory(prefix="rxn2-route-dry-run-")
        working_db_path = Path(temporary_dir.name) / source_db_path.name
        backup_database(source_db_path, working_db_path)
    elif args.backup:
        backup_database(source_db_path, args.backup.resolve())

    db = connect(working_db_path, args.schema.resolve())
    try:
        register_sources(db, args.sources.resolve())
        db.execute("BEGIN IMMEDIATE")
        result = import_artifact(db, validated, release_name)
        result["coverage"] = refresh_coverage(db, commit=False)
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(
                f"database verification failed: integrity={integrity}, foreign_keys={len(foreign_keys)}"
            )
        if args.dry_run:
            db.rollback()
            result["status"] = "validated_dry_run"
        else:
            db.commit()
            result["status"] = "succeeded"
        print(json.dumps(result, indent=2, sort_keys=True))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        if temporary_dir:
            temporary_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
