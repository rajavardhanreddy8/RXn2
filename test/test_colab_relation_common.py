import pytest

from scripts.colab_relation_common import RELATION_SCHEMA, job_hash, pack_jobs, validate_candidate
from apps.api.app.relations import RELATION_SCHEMA as API_RELATION_SCHEMA


def candidate():
    return {
        "procedure_type": "performed",
        "materials": [{
            "surface_text": "Benzoic acid", "role": "consumed",
            "evidence_quote": "Benzoic acid was charged.",
            "explicit": True, "uncertain": False, "confidence": 0.98,
        }],
        "facts": [{
            "fact_type": "condition", "value_text": "25 C",
            "evidence_quote": "The mixture was held at 25 C.",
            "explicit": True, "uncertain": False, "confidence": 0.95,
        }],
        "conflicts": [],
    }


def test_schema_matches_api():
    api = API_RELATION_SCHEMA
    assert RELATION_SCHEMA['properties']['procedure_type']['enum'] == api['properties']['procedure_type']['enum']
    assert RELATION_SCHEMA['properties']['materials']['items']['properties']['role']['enum'] == api['$defs']['MaterialRelation']['properties']['role']['enum']
    assert RELATION_SCHEMA['properties']['facts']['items']['properties']['fact_type']['enum'] == api['$defs']['TextFact']['properties']['fact_type']['enum']


def test_candidate_requires_controlled_roles_and_unique_quotes():
    text = "Benzoic acid was charged. The mixture was held at 25 C."
    assert validate_candidate(candidate(), text)["procedure_type"] == "performed"
    bad = candidate()
    bad["materials"][0]["role"] = "container"
    with pytest.raises(ValueError, match="material_schema_invalid"):
        validate_candidate(bad, text)


def test_candidate_rejects_missing_or_repeated_evidence():
    with pytest.raises(ValueError, match="verbatim_value_missing"):
        validate_candidate(candidate(), "Different evidence.")
    text = "Benzoic acid was charged. Benzoic acid was charged. The mixture was held at 25 C."
    with pytest.raises(ValueError, match="verbatim_value_not_unique"):
        validate_candidate(candidate(), text)


def test_pack_jobs_is_deterministic_and_bounded():
    jobs = [
        {"evidence_span_id": "b", "evidence_text": "x" * 9},
        {"evidence_span_id": "a", "evidence_text": "x" * 4},
        {"evidence_span_id": "c", "evidence_text": "x" * 7},
    ]
    batches = pack_jobs(jobs, max_items=2, max_chars=12)
    assert [[item["evidence_span_id"] for item in batch] for batch in batches] == [["a", "c"], ["b"]]
    assert job_hash(jobs[0]) == job_hash(jobs[0])
