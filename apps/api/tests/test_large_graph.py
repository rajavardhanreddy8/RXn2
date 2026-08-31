from __future__ import annotations

from apps.api.app import db
from apps.api.app.graph_projection import (
    _process_class,
    graph_neighborhood,
    graph_overview,
    graph_projection_page,
    graph_route_map,
    graph_search,
    graph_stats,
    rebuild_graph_projection,
)
from apps.api.app.seed import seed_demo


def test_route_process_classes_use_structure_layers_not_noisy_text():
    assert _process_class(
        "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    ) == "isolation_or_workup"
    assert _process_class(
        "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "LFQSCWFLJHTTHZ-ABCDEFABSA-N"
    ) == "salt_stereoisomer_or_solid_form"
    assert _process_class(
        "LFQSCWFLJHTTHZ-UHFFFAOYSA-N", "QTBSBXVTEAMEQO-UHFFFAOYSA-N"
    ) == "synthetic_transformation_candidate"


def test_large_graph_projection_is_idempotent_and_review_safe(tmp_path, monkeypatch):
    path = tmp_path / "large-graph.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize()
    seed_demo()

    first = rebuild_graph_projection()
    second = rebuild_graph_projection()
    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    assert first["nodes"] > 0
    assert first["edges"] > 0

    with db.connect() as connection:
        unsafe = connection.execute(
            """SELECT count(*) FROM graph_edge e
               JOIN reaction_instance r ON e.source_record_id LIKE r.reaction_id||'%'
               WHERE e.source_table IN ('reaction_participant','reaction_evidence_link')
                 AND e.review_status='accepted' AND r.review_status<>'accepted'"""
        ).fetchone()[0]
    assert unsafe == 0
    with db.connect() as connection:
        accepted_graph_edges = connection.execute(
            "SELECT count(*) FROM graph_edge WHERE review_status='accepted'"
        ).fetchone()[0]
        accepted_reactions = connection.execute(
            "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
        ).fetchone()[0]
    if accepted_reactions == 0:
        assert accepted_graph_edges == 0


def test_large_graph_queries_are_bounded(tmp_path, monkeypatch):
    path = tmp_path / "large-graph.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize()
    seed_demo()
    rebuild_graph_projection()

    stats = graph_stats()
    overview = graph_overview()
    compound_upstream = graph_overview("compound", {"validated", "unresolved"}, "incoming", 1)
    compound_downstream = graph_overview("compound", {"validated", "unresolved"}, "outgoing", 1)
    core_route_map = graph_route_map({"validated", "unresolved"})
    route_map = graph_route_map(
        {"validated", "unresolved"}, process_layer="candidates"
    )
    raw_route_map = graph_route_map(
        {"validated", "unresolved"}, collapsed=False, process_layer="candidates"
    )
    results = graph_search("benzamide", None, 10)
    assert stats["node_count"] > 0 and stats["edge_count"] > 0
    assert overview["nodes"] and overview["edges"]
    assert any(node["id"] == "compound" for node in compound_upstream["nodes"])
    assert any(edge["target"] == "compound" for edge in compound_upstream["edges"])
    assert any(edge["source"] == "compound" for edge in compound_downstream["edges"])
    assert not core_route_map["edges"]  # No atom mapping exists in the fixture.
    assert route_map["edges"]
    assert all(edge["predicate"] == "transforms_to" for edge in route_map["edges"])
    assert len({edge["edge_id"] for edge in route_map["edges"]}) == len(route_map["edges"])
    assert len(route_map["nodes"]) < len(raw_route_map["nodes"])
    assert all(edge["predicate"] in {"consumed", "produced"} for edge in raw_route_map["edges"])
    assert all(
        edge["source_node_id"].startswith(("compound:", "mention:"))
        and edge["target_node_id"].startswith(("procedure:", "reaction:"))
        for edge in raw_route_map["edges"] if edge["predicate"] == "consumed"
    )
    assert all(
        edge["target_node_id"].startswith(("compound:", "mention:"))
        and edge["source_node_id"].startswith(("procedure:", "reaction:"))
        for edge in raw_route_map["edges"] if edge["predicate"] == "produced"
    )
    assert results

    neighborhood = graph_neighborhood(
        results[0]["node_id"], 2, 2000, 5000,
        {"validated", "unresolved"}, "both",
    )
    assert len(neighborhood["nodes"]) <= 2000
    assert len(neighborhood["edges"]) <= 5000
    assert neighborhood["automatic_acceptance"] is False


def test_full_projection_pages_reconcile_with_stats(tmp_path, monkeypatch):
    path = tmp_path / "full-graph.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.initialize()
    seed_demo()
    rebuild_graph_projection()

    stats = graph_stats()
    nodes = graph_projection_page("nodes", 0, 10)
    edges = graph_projection_page("edges", 0, 10, {"validated", "unresolved", "rejected"})
    assert nodes["total"] == stats["node_count"]
    assert edges["total"] == stats["edge_count"]
    assert nodes["items"] == sorted(nodes["items"], key=lambda item: item["node_id"])
    assert edges["items"] == sorted(edges["items"], key=lambda item: item["edge_id"])
