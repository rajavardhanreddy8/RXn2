from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any

from .db import connect


SCORER_VERSION = "balanced-cost-feasibility-v1"


def _grams(value: float, unit: str) -> float:
    factors = {"mg": 0.001, "g": 1.0, "kg": 1000.0}
    if unit not in factors:
        raise ValueError(f"unsupported mass unit: {unit}")
    return value * factors[unit]


def _compound_mw(db, compound_id: str) -> float | None:
    row = db.execute(
        "SELECT molecular_weight FROM compound_property WHERE compound_id = ?",
        (compound_id,),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def _leaf_requirements(db, route: dict, target_mass_g: float) -> tuple[dict[str, float], list[str]]:
    by_product = {step["product_compound_id"]: step for step in route["steps"]}
    leaves: dict[str, float] = defaultdict(float)
    warnings: list[str] = []

    def visit(compound_id: str, required_mass_g: float) -> None:
        step = by_product.get(compound_id)
        if not step:
            leaves[compound_id] += required_mass_g
            return
        product_mw = _compound_mw(db, compound_id)
        reaction_yield = step.get("yield_percent")
        if not product_mw or not reaction_yield:
            warnings.append(f"Cannot calculate quantities for {step['reaction_id']}: missing molecular weight or yield.")
            return
        product_moles = required_mass_g / product_mw
        for item in step["inputs"]:
            input_mw = item.get("molecular_weight") or _compound_mw(db, item["compound_id"])
            stoich = item.get("stoichiometry")
            if not input_mw or not stoich:
                warnings.append(f"Cannot calculate {item['compound_id']}: missing molecular weight or stoichiometry.")
                continue
            input_moles = product_moles * float(stoich) / (float(reaction_yield) / 100)
            visit(item["compound_id"], input_moles * input_mw)

    visit(route["target_compound_id"], target_mass_g)
    return dict(leaves), warnings


def _quote_cost(db, compound_id: str, mass_g: float, currency: str,
                fx_date: str | None) -> dict[str, Any] | None:
    rows = db.execute(
        """SELECT * FROM material_quote WHERE compound_id = ? AND review_status = 'accepted'
           ORDER BY observed_at DESC, price ASC""",
        (compound_id,),
    ).fetchall()
    candidates = []
    for row in rows:
        quote = dict(row)
        pack_g = _grams(float(quote["pack_size_value"]), quote["pack_size_unit"])
        converted_price = float(quote["price"])
        if quote["currency"] != currency:
            rate_day = fx_date or date.today().isoformat()
            rate = db.execute(
                """SELECT rate FROM exchange_rate WHERE rate_date <= ? AND base_currency = ?
                   AND quote_currency = ? ORDER BY rate_date DESC LIMIT 1""",
                (rate_day, quote["currency"], currency),
            ).fetchone()
            if not rate:
                continue
            converted_price *= float(rate[0])
        packs = math.ceil(mass_g / pack_g)
        candidates.append({
            "quote_id": quote["quote_id"],
            "supplier_id": quote["supplier_id"],
            "required_mass_g": round(mass_g, 6),
            "pack_size_g": pack_g,
            "packs": packs,
            "package_cost": round(packs * converted_price, 4),
            "continuous_cost": round((mass_g / pack_g) * converted_price, 4),
            "currency": currency,
            "observed_at": quote["observed_at"],
            "source_url": quote["source_url"],
        })
    return min(candidates, key=lambda item: (item["package_cost"], item["quote_id"])) if candidates else None


def evaluate_route(route: dict, target_mass_g: float, currency: str,
                   fx_date: str | None = None) -> dict[str, Any]:
    with connect() as db:
        requirements, warnings = _leaf_requirements(db, route, target_mass_g)
        quote_lines = []
        unpriced = []
        priced_mass = 0.0
        total_mass = sum(requirements.values())
        for compound_id, mass_g in sorted(requirements.items()):
            quote = _quote_cost(db, compound_id, mass_g, currency, fx_date)
            if quote:
                priced_mass += mass_g
                quote_lines.append({"compound_id": compound_id, **quote})
            else:
                unpriced.append({"compound_id": compound_id, "required_mass_g": round(mass_g, 6)})
        hazard_rows = db.execute(
            """SELECT AVG(h.severity) FROM hazard_classification h
               JOIN reaction_participant p USING (compound_id)
               WHERE p.reaction_id IN ({}) AND h.review_status = 'accepted'""".format(
                ",".join("?" for _ in route["steps"]) or "NULL"
            ),
            [step["reaction_id"] for step in route["steps"]],
        ).fetchone()
        hazard_burden = float(hazard_rows[0] or 0) * 100

    coverage = priced_mass / total_mass if total_mass else 0.0
    actual_cost = sum(item["package_cost"] for item in quote_lines) if coverage >= 0.8 else None
    material_intensity = total_mass / target_mass_g if target_mass_g else 0
    yields = [float(step["yield_percent"]) / 100 for step in route["steps"] if step.get("yield_percent")]
    cumulative_yield = math.prod(yields) if len(yields) == len(route["steps"]) else 0
    evidence_complete = sum(
        1 for step in route["steps"] if step["evidence"].get("publication_number") and not step["is_synthetic"]
    ) / len(route["steps"]) if route["steps"] else 0
    scale_score = min(1.0, max((step.get("demonstrated_scale_g") or 0) / target_mass_g for step in route["steps"])) if route["steps"] else 0

    relative_cost_index = 100 * (
        0.30 * min(material_intensity / 5, 1)
        + 0.25 * (1 - cumulative_yield)
        + 0.15 * min(len(route["steps"]) / 12, 1)
        + 0.15 * 0  # Solvent burden is zero only until reviewed solvent amounts exist.
        + 0.10 * (hazard_burden / 100)
        + 0.05 * (1 - evidence_complete)
    )
    feasibility = 100 * (
        0.25 * cumulative_yield
        + 0.15 * (1 - min(len(route["steps"]) / 12, 1))
        + 0.20 * (1 - hazard_burden / 100)
        + 0.15 * 1  # No solvent penalty until reviewed solvent amounts exist.
        + 0.15 * scale_score
        + 0.10 * evidence_complete
    )
    return {
        "actual_material_cost": round(actual_cost, 4) if actual_cost is not None else None,
        "actual_cost_label": "Estimated raw-material cost only; excludes labor, utilities, equipment, waste treatment, QA and capex.",
        "actual_cost_coverage": round(coverage, 4),
        "relative_cost_index": round(relative_cost_index, 2),
        "feasibility_score": round(feasibility, 2),
        "rank_tier": "cost_complete" if actual_cost is not None else "cost_incomplete",
        "rank_score": None,
        "currency": currency,
        "quote_lines": quote_lines,
        "unpriced_materials": unpriced,
        "leaf_requirements_g": requirements,
        "warnings": warnings,
        "scorer_version": SCORER_VERSION,
    }


def rank_evaluated(routes: list[dict]) -> list[dict]:
    complete_costs = [r["evaluation"]["actual_material_cost"] for r in routes if r["evaluation"]["actual_material_cost"] is not None]
    low = min(complete_costs) if complete_costs else None
    high = max(complete_costs) if complete_costs else None
    for route in routes:
        evaluation = route["evaluation"]
        if evaluation["actual_material_cost"] is None:
            evaluation["rank_score"] = round(100 - evaluation["feasibility_score"], 2)
            continue
        cost = evaluation["actual_material_cost"]
        normalized_cost = 0 if high == low else 100 * (cost - low) / (high - low)
        evaluation["rank_score"] = round(0.5 * normalized_cost + 0.5 * (100 - evaluation["feasibility_score"]), 2)
    routes.sort(key=lambda r: (
        0 if r["evaluation"]["rank_tier"] == "cost_complete" else 1,
        r["evaluation"]["rank_score"],
        r["route_id"],
    ))
    for index, route in enumerate(routes, 1):
        route["rank"] = index
    return routes

