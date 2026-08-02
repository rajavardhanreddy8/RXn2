#!/usr/bin/env python3
"""Compare a predicted route hypothesis with a patented evidence baseline."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

SCORER_VERSION = "patent-baseline-dominance-v1"
VALIDATION_CHECKS = (
    "structures_valid",
    "atom_balanced",
    "stereochemistry_preserved",
    "hazard_constraints_passed",
)
LOWER_IS_BETTER = (
    "step_count",
    "process_mass_intensity",
    "hazard_score",
    "material_cost_per_kg",
)


def _number(value: object) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("route metrics must be finite numbers")
    return result


def route_metrics(route: dict, predicted: bool = False) -> dict[str, float | None]:
    steps = route.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("route requires at least one step")
    metrics = route.get("metrics") or {}
    yield_field = "predicted_yield_percent" if predicted else "yield_percent"
    yields = [_number(step.get(yield_field)) for step in steps]
    if any(value is not None and not 0 < value <= 100 for value in yields):
        raise ValueError(f"{yield_field} must be greater than 0 and at most 100")
    cumulative_yield = (
        math.prod(value / 100 for value in yields if value is not None)
        if all(value is not None for value in yields)
        else None
    )
    return {
        "step_count": float(len(steps)),
        "cumulative_yield": cumulative_yield,
        "process_mass_intensity": _number(metrics.get("process_mass_intensity")),
        "hazard_score": _number(metrics.get("hazard_score")),
        "material_cost_per_kg": _number(metrics.get("material_cost_per_kg")),
    }


def validation_gate(route: dict) -> dict:
    validation = route.get("validation") or {}
    failed = [name for name in VALIDATION_CHECKS if validation.get(name) is False]
    missing = [name for name in VALIDATION_CHECKS if validation.get(name) is not True]
    unsupported_steps = [
        index + 1 for index, step in enumerate(route.get("steps") or [])
        if not step.get("precedent_reaction_ids")
    ]
    if failed:
        status = "rejected"
    elif missing or unsupported_steps:
        status = "needs_validation"
    else:
        status = "eligible_for_comparison"
    return {
        "status": status,
        "failed_checks": failed,
        "missing_checks": missing,
        "unsupported_steps": unsupported_steps,
    }


def compare_routes(baseline: dict, predicted: dict) -> dict:
    if baseline.get("target_compound_id") != predicted.get("target_compound_id"):
        raise ValueError("baseline and prediction must have the same target compound")
    baseline_metrics = route_metrics(baseline)
    predicted_metrics = route_metrics(predicted, predicted=True)
    gate = validation_gate(predicted)
    deltas: dict[str, float] = {}
    for name in LOWER_IS_BETTER:
        before, after = baseline_metrics[name], predicted_metrics[name]
        if before is not None and after is not None:
            deltas[name] = round(before - after, 8)
    before_yield = baseline_metrics["cumulative_yield"]
    after_yield = predicted_metrics["cumulative_yield"]
    if before_yield is not None and after_yield is not None:
        deltas["cumulative_yield"] = round(after_yield - before_yield, 8)

    improved = sorted(name for name, delta in deltas.items() if delta > 1e-9)
    worsened = sorted(name for name, delta in deltas.items() if delta < -1e-9)
    if gate["status"] != "eligible_for_comparison":
        verdict = "blocked_validation"
    elif not deltas:
        verdict = "insufficient_data"
    elif improved and not worsened:
        verdict = "improved"
    elif worsened and not improved:
        verdict = "not_improved"
    else:
        verdict = "mixed"
    return {
        "scorer_version": SCORER_VERSION,
        "target_compound_id": baseline["target_compound_id"],
        "verdict": verdict,
        "validation_gate": gate,
        "baseline_metrics": baseline_metrics,
        "predicted_metrics": predicted_metrics,
        "improved_metrics": improved,
        "worsened_metrics": worsened,
        "deltas": deltas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--predicted", type=Path, required=True)
    args = parser.parse_args()
    result = compare_routes(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.predicted.read_text(encoding="utf-8")),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
