from scripts.route_benchmark import compare_routes


def route(predicted=False, validated=True):
    yield_name = "predicted_yield_percent" if predicted else "yield_percent"
    result = {
        "target_compound_id": "TARGET-1",
        "steps": [
            {yield_name: 90, "precedent_reaction_ids": ["RXN-1"]},
            {yield_name: 90, "precedent_reaction_ids": ["RXN-2"]},
        ],
        "metrics": {
            "process_mass_intensity": 8,
            "hazard_score": 0.4,
            "material_cost_per_kg": 100,
        },
    }
    if predicted:
        result["validation"] = {
            "structures_valid": validated,
            "atom_balanced": validated,
            "stereochemistry_preserved": validated,
            "hazard_constraints_passed": validated,
        }
    return result


def test_improvement_requires_validated_grounded_prediction():
    baseline = route()
    predicted = route(predicted=True)
    predicted["steps"] = [
        {"predicted_yield_percent": 95, "precedent_reaction_ids": ["RXN-3"]}
    ]
    predicted["metrics"] = {
        "process_mass_intensity": 5,
        "hazard_score": 0.2,
        "material_cost_per_kg": 80,
    }
    result = compare_routes(baseline, predicted)
    assert result["verdict"] == "improved"
    assert set(result["improved_metrics"]) == {
        "cumulative_yield", "hazard_score", "material_cost_per_kg",
        "process_mass_intensity", "step_count",
    }


def test_better_numbers_cannot_bypass_validation():
    result = compare_routes(route(), route(predicted=True, validated=False))
    assert result["verdict"] == "blocked_validation"
    assert result["validation_gate"]["status"] == "rejected"


def test_tradeoffs_are_reported_as_mixed():
    baseline = route()
    predicted = route(predicted=True)
    predicted["metrics"]["process_mass_intensity"] = 6
    predicted["metrics"]["hazard_score"] = 0.8
    result = compare_routes(baseline, predicted)
    assert result["verdict"] == "mixed"
    assert result["improved_metrics"] == ["process_mass_intensity"]
    assert result["worsened_metrics"] == ["hazard_score"]
