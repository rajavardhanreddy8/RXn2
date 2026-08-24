from scripts.select_performed_route_batch import evidence_already_in_graph


def test_larger_candidate_is_covered_by_imported_split_span_from_same_patent():
    imported_step = "A performed experimental procedure with exact quantities and conditions. " * 3
    candidate = {
        "evidence_span_id": "candidate:whole-block",
        "publication_number": "WO-1-A1",
        "evidence_text": f"Example heading. {imported_step} Additional later procedure.",
    }
    assert evidence_already_in_graph(
        candidate,
        {"step:other"},
        {"WO-1-A1": [imported_step]},
    ) is True
    assert evidence_already_in_graph(
        candidate,
        {"step:other"},
        {"WO-2-A1": [imported_step]},
    ) is False
