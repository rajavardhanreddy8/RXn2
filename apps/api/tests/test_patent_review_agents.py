from scripts.review_patent_examples_with_llm import (
    ReviewProposal,
    comparison,
    repair_quotes,
    request_payload,
    validate_quotes,
)


def proposal(**updates):
    value = {
        "procedure_type": "performed",
        "materials": [{
            "surface_text": "Material A", "role": "consumed",
            "evidence_quote": "Material A was dissolved in ethanol.",
            "uncertain": False, "confidence": 0.95,
        }],
        "facts": [{
            "field": "other", "value_text": "ethanol",
            "evidence_quote": "Material A was dissolved in ethanol.",
            "uncertain": False, "confidence": 0.95,
        }],
        "missing_fields": ["temperature"], "issues": [],
        "recommendation": "incomplete", "rationale": "Temperature is not reported.",
    }
    value.update(updates)
    return ReviewProposal.model_validate(value)


def test_verbatim_validation_rejects_unsupported_quotes():
    source = "Material A was dissolved in ethanol."
    assert validate_quotes(source, proposal()) == []
    bad = proposal(materials=[{
        "surface_text": "Material B", "role": "consumed",
        "evidence_quote": "Material B was charged.", "uncertain": False, "confidence": 0.9,
    }])
    assert validate_quotes(source, bad) == ["material[0].evidence_quote_not_verbatim"]


def test_two_agents_disagree_when_one_misses_starting_material():
    result = comparison(proposal(), proposal(materials=[]))
    assert result["status"] == "agent_disagreement"
    assert "materials_or_roles" in result["disagreements"]
    assert result["counts_as_human_curation"] is False


def test_agent_agreement_still_requires_human_review():
    result = comparison(proposal(), proposal())
    assert result["status"] == "agent_agreement_candidate"
    assert result["human_review_required"] is True
    assert result["counts_as_human_curation"] is False


def test_groq_qwen_payload_uses_json_mode_and_hidden_reasoning():
    payload = request_payload(
        "groq", "qwen/qwen3.6-27b", "source_completeness", "Example 1"
    )
    assert payload["model"] == "qwen/qwen3.6-27b"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["reasoning_effort"] == "none"
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    assert "provider" not in payload


def test_product_form_is_a_supported_fact_field():
    value = proposal(facts=[{
        "field": "product_form", "value_text": "white solid",
        "evidence_quote": "white solid", "uncertain": False, "confidence": 0.9,
    }])
    assert value.facts[0].field == "product_form"


def test_deterministically_reanchors_only_unique_exact_value():
    source = "Material A was dissolved in ethanol.\nProduct was isolated."
    value = proposal(materials=[{
        "surface_text": "Material A", "role": "consumed",
        "evidence_quote": "Material A was used.", "uncertain": False,
        "confidence": 0.9,
    }])
    repaired, changes = repair_quotes(source, value)
    assert changes == ["materials[0].evidence_quote_reanchored"]
    assert repaired.materials[0].evidence_quote == "Material A was dissolved in ethanol."
    assert validate_quotes(source, repaired) == []


def test_does_not_reanchor_ambiguous_repeated_value():
    source = "ethanol was added. ethanol was removed."
    value = proposal(materials=[], facts=[{
        "field": "other", "value_text": "ethanol", "evidence_quote": "wrong",
        "uncertain": False, "confidence": 0.8,
    }])
    repaired, changes = repair_quotes(source, value)
    assert changes == []
    assert validate_quotes(source, repaired) == ["fact[0].evidence_quote_not_verbatim"]
