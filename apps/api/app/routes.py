from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from typing import Any

from .db import connect


ALGORITHM_VERSION = "evidence-and-or-v1"


@dataclass
class Branch:
    steps: list[dict[str, Any]]


def resolve_compound(query: str) -> dict[str, Any] | None:
    value = " ".join(query.casefold().split())
    normalized_alias = re.sub(r"[^a-z0-9]+", " ", value).strip()
    with connect() as db:
        row = db.execute(
            """SELECT c.compound_id, c.preferred_name, c.smiles, c.inchi_key,
                      p.standardized_smiles, p.molecular_formula, p.molecular_weight
               FROM compound c LEFT JOIN compound_property p USING (compound_id)
               WHERE lower(c.compound_id) = ? OR lower(c.preferred_name) = ?
               ORDER BY CASE WHEN lower(c.preferred_name) = ? THEN 0 ELSE 1 END LIMIT 1""",
            (value, value, value),
        ).fetchone()
        if not row:
            row = db.execute(
                """SELECT c.compound_id, c.preferred_name, c.smiles, c.inchi_key,
                          p.standardized_smiles, p.molecular_formula, p.molecular_weight
                   FROM drug_alias a JOIN drug_compound dc USING (drug_id)
                   JOIN compound c USING (compound_id)
                   LEFT JOIN compound_property p USING (compound_id)
                   WHERE a.normalized_alias = ?
                   ORDER BY CASE dc.relationship_type WHEN 'active_moiety' THEN 0 ELSE 1 END,
                            c.compound_id LIMIT 1""",
                (normalized_alias,),
            ).fetchone()
        if not row:
            row = db.execute(
                """SELECT c.compound_id, c.preferred_name, c.smiles, c.inchi_key,
                          p.standardized_smiles, p.molecular_formula, p.molecular_weight
                   FROM compound c LEFT JOIN compound_property p USING (compound_id)
                   WHERE lower(c.preferred_name) LIKE ? ORDER BY length(c.preferred_name) LIMIT 1""",
                (f"%{value}%",),
            ).fetchone()
        return dict(row) if row else None


def compound_by_id(compound_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            """SELECT c.compound_id, c.preferred_name, c.smiles, c.inchi_key,
                      p.standardized_smiles, p.molecular_formula, p.molecular_weight
               FROM compound c LEFT JOIN compound_property p USING (compound_id)
               WHERE c.compound_id = ?""",
            (compound_id,),
        ).fetchone()
        return dict(row) if row else None


def _is_starting_material(db, compound_id: str) -> bool:
    row = db.execute(
        """SELECT is_starting_material FROM material_availability
           WHERE compound_id = ? AND review_status = 'accepted'""",
        (compound_id,),
    ).fetchone()
    return bool(row and row[0])


def _reactions_producing(db, compound_id: str, excluded_hazards: list[str]) -> list[dict]:
    excluded_clause = ""
    parameters: list[Any] = [compound_id]
    if excluded_hazards:
        placeholders = ",".join("?" for _ in excluded_hazards)
        excluded_clause = f"""AND NOT EXISTS (
          SELECT 1 FROM reaction_participant xp
          JOIN hazard_classification h ON h.compound_id = xp.compound_id
          WHERE xp.reaction_id = r.reaction_id AND h.hazard_code IN ({placeholders})
          AND h.review_status = 'accepted')"""
        parameters.extend(excluded_hazards)
    rows = db.execute(
        f"""SELECT r.*, e.publication_number, e.source_url, e.evidence_status
            FROM reaction_instance r
            JOIN reaction_participant p ON p.reaction_id = r.reaction_id
            LEFT JOIN evidence_span e ON e.evidence_span_id = r.evidence_span_id
            WHERE p.compound_id = ? AND p.role = 'produced'
              AND r.review_status = 'accepted'
              AND (r.is_synthetic = 1 OR r.evidence_span_id IS NOT NULL)
              {excluded_clause}
            ORDER BY r.is_synthetic, r.confidence DESC, r.demonstrated_scale_g DESC,
                     r.reaction_id""",
        parameters,
    ).fetchall()
    return [dict(row) for row in rows]


def _inputs(db, reaction_id: str) -> list[dict]:
    rows = db.execute(
        """SELECT p.compound_id, p.role, p.stoichiometry, c.preferred_name,
                  cp.molecular_weight
           FROM reaction_participant p JOIN compound c USING (compound_id)
           LEFT JOIN compound_property cp USING (compound_id)
           WHERE p.reaction_id = ? AND p.role = 'consumed'
           ORDER BY p.compound_id""",
        (reaction_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _expand(db, compound_id: str, remaining: int, seen: frozenset[str],
            excluded_compounds: set[str], excluded_hazards: list[str]) -> list[Branch]:
    if compound_id in excluded_compounds or compound_id in seen:
        return []
    if _is_starting_material(db, compound_id):
        return [Branch([])]
    if remaining <= 0:
        return []
    branches: list[Branch] = []
    for reaction in _reactions_producing(db, compound_id, excluded_hazards):
        inputs = _inputs(db, reaction["reaction_id"])
        if not inputs:
            continue
        input_options = []
        for item in inputs:
            options = _expand(
                db, item["compound_id"], remaining - 1, seen | {compound_id},
                excluded_compounds, excluded_hazards,
            )
            if not options:
                input_options = []
                break
            input_options.append(options)
        for combination in itertools.product(*input_options) if input_options else []:
            prior_steps: list[dict] = []
            for option in combination:
                prior_steps.extend(option.steps)
            step = {
                "reaction_id": reaction["reaction_id"],
                "reaction_name": reaction["reaction_name"],
                "transformation_key": reaction["transformation_key"],
                "product_compound_id": compound_id,
                "yield_percent": reaction["yield_percent"],
                "demonstrated_scale_g": reaction["demonstrated_scale_g"],
                "confidence": reaction["confidence"],
                "is_synthetic": bool(reaction["is_synthetic"]),
                "evidence": {
                    "publication_number": reaction["publication_number"],
                    "source_url": reaction["source_url"],
                    "evidence_status": reaction["evidence_status"],
                    "label": "Synthetic fixture — not scientific evidence" if reaction["is_synthetic"] else reaction["publication_number"],
                },
                "inputs": inputs,
            }
            deduplicated = {item["reaction_id"]: item for item in [*prior_steps, step]}
            ordered = list(deduplicated.values())
            if len(ordered) <= remaining:
                branches.append(Branch(ordered))
    unique: dict[str, Branch] = {}
    for branch in branches:
        key = "|".join(step["reaction_id"] for step in branch.steps)
        unique[key] = branch
    return list(unique.values())


def generate_routes(target_compound_id: str, max_steps: int, max_routes: int,
                    excluded_compounds: list[str], excluded_hazards: list[str]) -> list[dict]:
    with connect() as db:
        branches = _expand(
            db, target_compound_id, max_steps, frozenset(), set(excluded_compounds),
            excluded_hazards,
        )
    routes = []
    for branch in branches:
        signature = json.dumps(branch.steps, sort_keys=True, separators=(",", ":"))
        route_id = "route-" + hashlib.sha256(signature.encode()).hexdigest()[:16]
        routes.append({
            "route_id": route_id,
            "target_compound_id": target_compound_id,
            "step_count": len(branch.steps),
            "steps": branch.steps,
            "algorithm_version": ALGORITHM_VERSION,
        })
    routes.sort(key=lambda route: (
        route["step_count"],
        -sum(step["confidence"] for step in route["steps"]),
        route["route_id"],
    ))
    return routes[:max_routes]
