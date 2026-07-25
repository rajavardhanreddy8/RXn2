from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.api.app import db
from apps.api.app.costing import evaluate_route, rank_evaluated
from apps.api.app.main import app
from apps.api.app.routes import generate_routes, resolve_compound
from apps.api.app.seed import seed_demo


@pytest.fixture()
def local_database(tmp_path, monkeypatch):
    path = tmp_path / "mvp.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize()
    seed_demo()
    return path


def test_demo_graph_generates_two_deterministic_routes(local_database):
    target = resolve_compound("Demo benzamide target")
    assert target and target["compound_id"] == "DEMO-TARGET-1"
    first = generate_routes(target["compound_id"], 6, 10, [], [])
    second = generate_routes(target["compound_id"], 6, 10, [], [])
    assert [route["route_id"] for route in first] == [route["route_id"] for route in second]
    assert len(first) == 2
    assert all(route["step_count"] == 1 for route in first)
    assert all(step["is_synthetic"] for route in first for step in route["steps"])


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

        coverage = client.get("/api/catalogue/coverage")
        assert coverage.status_code == 200
        assert coverage.json()["total"] == 6
        assert all(item["status"] == "identified" for item in coverage.json()["items"])

        resolved = client.post("/api/targets/resolve", json={"query": "Demo benzamide target"})
        assert resolved.status_code == 200
        assert resolved.json()["reviewed_producing_reactions"] == 2

        generated = client.post(
            "/api/routes/generate",
            json={"compound_id": "DEMO-TARGET-1", "target_mass_g": 1000, "base_currency": "USD"},
        )
        assert generated.status_code == 200
        assert len(generated.json()["routes"]) == 2

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
        assert connection.execute("SELECT count(*) FROM kg_node WHERE node_type = 'reaction'").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM kg_edge WHERE edge_type = 'produced'").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM compound_element").fetchone()[0] > 0
        assert connection.execute("SELECT count(*) FROM compound_functional_group").fetchone()[0] > 0
        assert connection.execute("SELECT count(*) FROM kg_edge WHERE edge_type = 'contains_element'").fetchone()[0] > 0
        assert connection.execute("SELECT count(*) FROM kg_edge WHERE edge_type = 'has_functional_group'").fetchone()[0] > 0
    finally:
        connection.close()
