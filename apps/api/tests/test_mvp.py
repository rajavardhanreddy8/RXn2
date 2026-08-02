from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.api.app import db
from apps.api.app.costing import evaluate_route, rank_evaluated
from apps.api.app.main import app
from apps.api.app.routes import generate_routes, resolve_compound
from apps.api.app.seed import seed_demo
from scripts.annotate_catalogue import annotate_catalogue


@pytest.fixture()
def local_database(tmp_path, monkeypatch):
    path = tmp_path / "mvp.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize()
    seed_demo()
    return path


def test_production_startup_does_not_seed_demo_data(tmp_path, monkeypatch):
    path = tmp_path / "production.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.delenv("RXN2_SEED_DEMO", raising=False)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["counts"]["reactions"] == 0
        assert health.json()["counts"]["drugs"] == 0
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM source WHERE source_id='mvp_demo'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_demo_graph_generates_two_deterministic_routes(local_database):
    target = resolve_compound("Demo benzamide target")
    assert target and target["compound_id"] == "DEMO-TARGET-1"
    first = generate_routes(target["compound_id"], 6, 10, [], [])
    second = generate_routes(target["compound_id"], 6, 10, [], [])
    assert [route["route_id"] for route in first] == [route["route_id"] for route in second]
    assert len(first) == 2
    assert all(route["step_count"] == 1 for route in first)
    assert all(step["is_synthetic"] for route in first for step in route["steps"])


def test_catalogue_annotation_accounts_for_every_compound(local_database):
    with db.connect() as connection:
        result = annotate_catalogue(connection)
    assert result == {
        "input": 9, "annotated": 4, "missing_structure": 5, "invalid_structure": 0
    }


def test_costing_uses_package_rounding_and_balanced_ranking(local_database):
    routes = generate_routes("DEMO-TARGET-1", 6, 10, [], [])
    for route in routes:
        route["evaluation"] = evaluate_route(route, 1000, "USD")
    ranked = rank_evaluated(routes)
    assert all(route["evaluation"]["actual_cost_coverage"] == 1 for route in ranked)
    assert all(route["evaluation"]["actual_material_cost"] is not None for route in ranked)
    assert ranked[0]["evaluation"]["actual_material_cost"] < ranked[1]["evaluation"]["actual_material_cost"]
    assert all(line["packs"] == int(line["packs"]) for route in ranked for line in route["evaluation"]["quote_lines"])


def test_exclusions_never_fall_back_to_invented_steps(local_database):
    routes = generate_routes("DEMO-TARGET-1", 6, 10, ["DEMO-START-A", "DEMO-START-B"], [])
    assert routes == []


def test_http_mvp_and_honest_benchmark_gap(local_database):
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["counts"]["reactions"] == 2
        assert health.json()["counts"]["drugs"] == 6
        automation = client.get("/api/automation/status")
        assert automation.status_code == 200
        assert automation.json()["automatic_acceptance"] is False

        queue = client.get("/api/review-queue")
        assert queue.status_code == 200
        assert queue.json()["automatic_acceptance"] is False


        coverage = client.get("/api/catalogue/coverage")
        assert coverage.status_code == 200
        assert coverage.json()["total"] == 6
        assert all(item["status"] == "identified" for item in coverage.json()["items"])
        assert all(item["product_count"] == 0 for item in coverage.json()["items"])
        assert client.get("/api/catalogue/releases").json() == {"total": 0, "items": []}

        resolved = client.post("/api/targets/resolve", json={"query": "Demo benzamide target"})
        assert resolved.status_code == 200
        assert resolved.json()["reviewed_producing_reactions"] == 2

        generated = client.post(
            "/api/routes/generate",
            json={"compound_id": "DEMO-TARGET-1", "target_mass_g": 1000, "base_currency": "USD"},
        )
        assert generated.status_code == 200
        assert len(generated.json()["routes"]) == 2

        graph = client.get("/api/graph/drugs/demo-drug:demo-target-1?depth=2")
        assert graph.status_code == 200
        payload = graph.json()
        assert any(node["type"] == "drug" for node in payload["nodes"])
        assert any(node["id"] == "drug:demo-drug:demo-target-1" for node in payload["nodes"])
        assert any(node["type"] == "reaction" for node in payload["nodes"])
        assert any(
            edge["type"] == "contains_element" and edge.get("atom_count") == 7
            for edge in payload["edges"]
            if edge["source"] == "compound:DEMO-TARGET-1"
        )
        assert payload["coverage_gaps"]

        neighbors = client.get(
            "/api/graph/neighbors/compound:DEMO-TARGET-1?direction=both"
        )
        assert neighbors.status_code == 200
        neighborhood = neighbors.json()
        assert {edge["traversed_from"] for edge in neighborhood["edges"]} == {
            "incoming", "outgoing"
        }
        assert any(edge["type"] == "produced" for edge in neighborhood["edges"])
        assert any(edge["type"] == "contains_element" for edge in neighborhood["edges"])

        benchmark = client.post(
            "/api/routes/generate",
            json={"compound_id": "BENCH-APIXABAN", "target_mass_g": 1000, "base_currency": "USD"},
        )
        assert benchmark.status_code == 200
        assert benchmark.json()["coverage_gap"] is True
        assert benchmark.json()["routes"] == []

        disabled_qroq = client.post(
            "/api/extraction/qroq",
            json={"source_text": "This public source text is long enough to validate the disabled adapter path."},
        )
        assert disabled_qroq.status_code == 503
        proprietary_without_consent = client.post(
            "/api/extraction/qroq",
            json={
                "source_text": "Confidential process text that must not leave the local workspace by default.",
                "data_classification": "proprietary",
            },
        )
        assert proprietary_without_consent.status_code == 422


def test_graph_views_are_queryable(local_database):
    connection = sqlite3.connect(local_database)
    try:
        connection.execute(
            """INSERT INTO patent_document
               (publication_number, country_code, kind_code, source_id, raw_record_json)
               VALUES ('DEMO-PATENT-A1', 'WO', 'A1', 'synthetic_fixture', '{}')"""
        )
        for suffix in ("primary", "setup"):
            connection.execute(
                """INSERT INTO evidence_span
                   (evidence_span_id, publication_number, source_id, artifact_sha256,
                    section_type, paragraph_id, char_start, char_end, evidence_text,
                    text_sha256, evidence_status, extraction_method, review_status,
                    retrieved_at, license_code, redistribution_class)
                   VALUES (?, 'DEMO-PATENT-A1', 'synthetic_fixture', ?, 'example', ?,
                           0, 4, 'demo', ?, 'performed', 'fixture', 'unreviewed',
                           '2026-07-31T00:00:00Z', 'CC0-1.0', 'permitted')""",
                (f"DEMO-EVIDENCE-{suffix}", suffix[0] * 64, suffix, suffix[-1] * 64),
            )
        connection.executemany(
            """INSERT INTO reaction_evidence_link
               (reaction_id, evidence_span_id, relationship_type, review_status, created_at)
               VALUES ('DEMO-RXN-A', ?, ?, 'unreviewed', '2026-07-31T00:00:00Z')""",
            [
                ("DEMO-EVIDENCE-primary", "primary_example"),
                ("DEMO-EVIDENCE-setup", "referenced_setup"),
            ],
        )
        assert connection.execute("SELECT count(*) FROM kg_node WHERE node_type = 'reaction'").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM kg_edge WHERE edge_type = 'produced'").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM compound_element").fetchone()[0] > 0
        assert connection.execute("SELECT count(*) FROM compound_functional_group").fetchone()[0] > 0
        assert connection.execute("SELECT count(*) FROM kg_edge WHERE edge_type = 'contains_element'").fetchone()[0] > 0
        assert connection.execute("SELECT count(*) FROM kg_edge WHERE edge_type = 'has_functional_group'").fetchone()[0] > 0
        assert connection.execute(
            "SELECT count(*) FROM kg_edge WHERE edge_type IN ('primary_example', 'referenced_setup')"
        ).fetchone()[0] == 2
    finally:
        connection.close()
