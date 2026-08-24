from __future__ import annotations

from scripts.select_performed_route_batch import classify_candidate, procedure_metrics


def candidate(**updates):
    value = {
        "candidate_status": "participant_roles_partial",
        "reported_outcome_count": 1,
        "measurement_count": 10,
        "target_mention_count": 1,
    }
    value.update(updates)
    return value


def evidence(text: str, **updates):
    value = {
        "evidence_text": text,
        "extraction_method": "deterministic_native_xml",
        "source_id": "epo_ops",
        "evidence_status": "performed",
    }
    value.update(updates)
    return value


def test_selects_single_native_performed_synthesis_with_reported_outcome():
    text = (
        "Example 3: Preparation of target. To a solution of intermediate in ethanol, "
        "reagent was added slowly. The reaction mixture was stirred for two hours, "
        "heated to reflux, cooled, and filtered. The title compound was obtained "
        "as a solid in 82% yield and 99% purity."
    )
    status, reasons, score, metrics = classify_candidate(candidate(), evidence(text))
    assert status == "eligible"
    assert reasons == []
    assert score > 0
    assert metrics["performed_cue_count"] >= 4


def test_rejects_multi_example_block_even_when_it_has_yields():
    text = (
        "Example 9: Preparation. Starting material was charged, reagent was added, "
        "stirred, heated, cooled, filtered and product was obtained in 80% yield. "
        "Example 10: Preparation. Another material was charged and product was obtained."
    )
    status, reasons, _, metrics = classify_candidate(candidate(), evidence(text))
    assert status == "excluded"
    assert "multiple_procedures_in_evidence_span" in reasons
    assert metrics["example_heading_count"] == 2


def test_rejects_purification_or_crystalline_form_process():
    text = (
        "Example 2: Purification of crystalline form A. Crude material was charged, "
        "solvent was added, stirred, heated, cooled, filtered and obtained in 90% yield."
    )
    status, reasons, _, _ = classify_candidate(candidate(), evidence(text))
    assert status == "excluded"
    assert "purification_formulation_or_solid_form" in reasons


def test_rejects_prior_art_description_and_non_native_text():
    text = (
        "Example 1 describes preparation according to prior art. Material was charged, "
        "reagent was added, stirred, heated, cooled, filtered and obtained in 70% yield."
    )
    status, reasons, _, _ = classify_candidate(
        candidate(), evidence(text, extraction_method="ocr")
    )
    assert status == "excluded"
    assert "reference_or_prior_art_only" in reasons
    assert "not_native_xml" in reasons


def test_rejects_follow_on_reference_that_does_not_demonstrate_target():
    text = (
        "Example 25: Following the procedures outlined in Examples 1 and 2, "
        "the intermediate was reacted to obtain a different compound. "
        "The mixture was stirred, heated, cooled, filtered and the product "
        "was isolated in 80% yield."
    )
    status, reasons, _, metrics = classify_candidate(candidate(), evidence(text))
    assert status == "excluded"
    assert "reference_or_prior_art_only" in reasons
    assert metrics["reference_only_cue"] is True


def test_rejects_already_imported_evidence():
    text = (
        "Preparation of product. To a solution of starting material, reagent was added. "
        "The reaction mixture was stirred, heated, cooled and filtered. Product was "
        "obtained in 75% yield."
    )
    status, reasons, _, _ = classify_candidate(candidate(), evidence(text), True)
    assert status == "excluded"
    assert "already_in_graph" in reasons


def test_metrics_do_not_treat_one_example_heading_as_multiple():
    metrics = procedure_metrics("Example-7: Preparation of product. Product was obtained.")
    assert metrics["example_heading_count"] == 1

