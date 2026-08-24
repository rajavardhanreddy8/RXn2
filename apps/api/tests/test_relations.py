from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from apps.api.app import db
from apps.api.app.main import app
from apps.api.app.relations import (
    RelationExtraction,
    persist_candidate,
    provider_specs,
    provisional_graph,
    request_payload,
)
from apps.api.app.seed import seed_demo


@pytest.fixture()
def relation_database(tmp_path, monkeypatch):
    path = tmp_path / "relations.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize()
    seed_demo()
    text = (
        "Demo benzoic acid starting material was charged. "
        "Demo benzamide target was obtained at 25 C in 80% yield."
    )
    digest = hashlib.sha256(text.encode()).hexdigest()
    with db.transaction() as connection:
        connection.execute(
            """INSERT INTO patent_document
               (publication_number, country_code, kind_code, source_id, raw_record_json)
               VALUES ('TEST-REL-A1', 'WO', 'A1', 'mvp_demo', '{}')"""
        )
        connection.execute(
            """INSERT INTO evidence_span
               (evidence_span_id, publication_number, source_id, artifact_sha256,
                section_type, paragraph_id, char_start, char_end, evidence_text,
                text_sha256, evidence_status, extraction_method, extractor_version,
                review_status, retrieved_at, license_code, redistribution_class)
               VALUES ('test-evidence', 'TEST-REL-A1', 'mvp_demo', ?,
                       'example', '1', 0, ?, ?, ?, 'performed', 'fixture', 'v1',
                       'unreviewed', '2026-08-22T00:00:00Z', 'CC0-1.0', 'permitted')""",
            ("a" * 64, len(text), text, digest),
        )
        connection.execute(
            """INSERT INTO extraction_job
               (extraction_job_id, provider, model, prompt_sha256, input_sha256,
                status, review_status, created_at)
               VALUES ('fixture-job', 'fixture', 'fixture', ?, ?, 'needs_review',
                       'needs_review', '2026-08-22T00:00:00Z')""",
            ("b" * 64, digest),
        )
    return path


def performed_candidate() -> RelationExtraction:
    return RelationExtraction.model_validate({
        "procedure_type": "performed",
        "materials": [
            {
                "surface_text": "Demo benzoic acid starting material",
                "role": "consumed",
                "evidence_quote": "Demo benzoic acid starting material was charged.",
                "explicit": True,
                "uncertain": False,
                "confidence": 0.99,
            },
            {
                "surface_text": "Demo benzamide target",
                "role": "produced",
                "evidence_quote": "Demo benzamide target was obtained at 25 C in 80% yield.",
                "explicit": True,
                "uncertain": False,
                "confidence": 0.99,
            },
        ],
        "facts": [
            {
                "fact_type": "condition",
                "value_text": "25 C",
                "evidence_quote": "Demo benzamide target was obtained at 25 C in 80% yield.",
                "explicit": True,
                "uncertain": False,
                "confidence": 0.95,
            }
        ],
        "conflicts": [],
    })


def test_strict_free_provider_configuration(monkeypatch):
    monkeypatch.setenv("RELATION_OPENROUTER_DATA_COLLECTION", "deny")
    payload = request_payload("openrouter", "z-ai/glm-5.2:free", "example text")
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["provider"]["require_parameters"] is True
    assert payload["provider"]["data_collection"] == "deny"
    fallback_payload = request_payload("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free", "example text")
    assert fallback_payload["response_format"] == {"type": "json_object"}

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with pytest.raises(RuntimeError, match="Paid OpenRouter models are disabled"):
        provider_specs("openrouter", "openai/gpt-5-nano")


def test_openrouter_free_model_chain_and_legacy_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("op_api_key", "test-key")
    monkeypatch.delenv("RELATION_OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("RELATION_OPENROUTER_MODEL", raising=False)
    assert provider_specs("openrouter") == [
        ("openrouter", "z-ai/glm-5.2:free"),
        ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        ("openrouter", "google/gemma-4-31b-it:free"),
    ]


def test_candidate_validation_builds_only_provisional_edges(relation_database):
    with db.connect() as connection:
        accepted_before = connection.execute(
            "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
        ).fetchone()[0]
    counts = persist_candidate("test-evidence", "fixture-job", performed_candidate())
    assert counts == {"validated": 4}

    graph = provisional_graph(publication_number="TEST-REL-A1")
    assert graph["provisional_reaction_count"] == 1
    assert any(node["type"] == "provisional_reaction" for node in graph["nodes"])
    assert {edge["type"] for edge in graph["edges"]} >= {
        "describes", "consumed", "produced", "has_condition"
    }
    with db.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
        ).fetchone()[0] == accepted_before
        assert connection.execute(
            "SELECT count(*) FROM relation_candidate WHERE review_status='accepted'"
        ).fetchone()[0] == 0


def test_invented_compound_remains_unresolved(relation_database):
    candidate = performed_candidate().model_copy(deep=True)
    candidate.materials[0].surface_text = "Invented unobtainium reagent"
    candidate.materials[0].evidence_quote = (
        "Demo benzoic acid starting material was charged."
    )
    counts = persist_candidate("test-evidence", "fixture-job", candidate)
    assert counts["rejected"] == 1
    graph = provisional_graph(publication_number="TEST-REL-A1")
    assert graph["provisional_reaction_count"] == 0


def test_relation_queue_api_is_resumable(relation_database):
    with TestClient(app) as client:
        response = client.post(
            "/api/extraction/relations",
            json={"evidence_span_ids": ["test-evidence"], "provider_mode": "auto"},
        )
        assert response.status_code == 200
        job_id = response.json()["jobs"][0]
        repeated = client.post(
            "/api/extraction/relations",
            json={"evidence_span_ids": ["test-evidence"], "provider_mode": "auto"},
        )
        assert repeated.status_code == 200
        status = client.get(f"/api/extraction/relations/{job_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "queued"
        with db.connect() as connection:
            assert connection.execute(
                "SELECT count(*) FROM pipeline_job WHERE job_type='relation_extraction'"
            ).fetchone()[0] == 1

def test_provisional_review_queue_has_exact_provenance_and_is_not_accepted(relation_database):
    persist_candidate("test-evidence", "fixture-job", performed_candidate())
    with TestClient(app) as client:
        response = client.get("/api/review-queue/provisional")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["publication_number"] == "TEST-REL-A1"
    assert item["provenance"]["paragraph_id"] == "1"
    assert item["provenance"]["text_sha256"]
    assert item["rank_components"]["explicit_resolved_product"] == 50
    assert item["automatic_acceptance"] is False
    evidence = 'Demo benzoic acid starting material was charged. Demo benzamide target was obtained at 25 C in 80% yield.'
    assert all(not relation["quote"] or relation["quote"] in evidence for relation in item["relations"])

def test_same_compound_input_output_conflict_is_not_a_reaction(relation_database):
    candidate = performed_candidate().model_copy(deep=True)
    candidate.materials[0].surface_text = "Demo benzamide target"
    candidate.materials[0].evidence_quote = (
        "Demo benzamide target was obtained at 25 C in 80% yield."
    )
    persist_candidate("test-evidence", "fixture-job", candidate)
    graph = provisional_graph(publication_number="TEST-REL-A1")
    assert graph["provisional_reaction_count"] == 0
