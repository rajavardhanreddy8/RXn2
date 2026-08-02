import sqlite3

from scripts.export_training_dataset import components, export


def database(status="accepted"):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE process_step(step_id, step_order, transformation_key, operation_summary, review_status, evidence_status, route_id, evidence_span_id);
      CREATE TABLE process_route(route_id, active_moiety_id, target_compound_id, review_status);
      CREATE TABLE evidence_span(evidence_span_id, publication_number, source_id, artifact_sha256, paragraph_id, section_type, char_start, char_end, evidence_text, text_sha256, evidence_status, extraction_method, extractor_version, retrieved_at, license_code, redistribution_class, source_url, review_status);
      CREATE TABLE patent_document(publication_number, publication_date, title);
      CREATE TABLE compound(compound_id, preferred_name, smiles, inchi_key, material_form);
      CREATE TABLE patent_family_member(family_id, publication_number);
      CREATE TABLE reaction_instance(reaction_id, evidence_span_id, yield_percent, demonstrated_scale_g, confidence, review_status, is_synthetic);
      CREATE TABLE reaction_participant(reaction_id, compound_id, role, stoichiometry);
      CREATE TABLE reaction_condition(condition_id, reaction_id, condition_type, value_text, numeric_value, unit);
      CREATE TABLE scale_label(scale_label_id, step_id, scale_band, basis_kind, basis_value_g, confidence, review_status);
      CREATE TABLE curation_decision(decision_id, object_type, object_id, decision, reviewer_id, rationale, decided_at);
    """)
    db.execute("INSERT INTO process_step VALUES ('step-1', 1, 'RXN', 'run', ?, 'performed', 'route-1', 'span-1')", (status,))
    db.execute("INSERT INTO process_route VALUES ('route-1', 'moiety-1', 'compound-1', ?)", (status,))
    db.execute("INSERT INTO evidence_span VALUES ('span-1', 'WO-123-A1', 'source', ?, 'p1', 'example', 0, 4, 'text', ?, 'performed', 'human', 'v1', '2026-01-01T00:00:00Z', 'CC0', 'permitted', NULL, ?)", ('a'*64, 'b'*64, status))
    db.execute("INSERT INTO patent_document VALUES ('WO-123-A1', '2025-01-01', 'Title')")
    db.execute("INSERT INTO compound VALUES ('compound-1', 'Compound', 'C', 'AAAAAAAAAAAAAA-BBBBBBBBBB-C', 'active_moiety')")
    db.execute("INSERT INTO patent_family_member VALUES ('family-1', 'WO-123-A1')")
    db.execute("INSERT INTO reaction_instance VALUES ('reaction-1', 'span-1', 80, 10, 1, ?, 0)", (status,))
    db.execute("INSERT INTO reaction_participant VALUES ('reaction-1', 'compound-1', 'consumed', 1)")
    db.execute("INSERT INTO reaction_participant VALUES ('reaction-1', 'compound-1', 'produced', 1)")
    db.execute("INSERT INTO scale_label VALUES ('scale-1', 'step-1', 'laboratory', 'product_mass', 10, 1, ?)", (status,))
    db.execute("INSERT INTO curation_decision VALUES ('decision-1', 'process_route', 'route-1', ?, 'chemist-1', 'checked', '2026-01-02T00:00:00Z')", (status,))
    return db


def test_export_only_writes_complete_reviewed_examples(tmp_path):
    report = export(database(), tmp_path / 'examples.jsonl', tmp_path / 'report.json')
    assert report['eligible_examples'] == 1
    assert report['split_counts']
    assert '"participants"' in (tmp_path / 'examples.jsonl').read_text()


def test_export_reports_unreviewed_steps_without_creating_examples(tmp_path):
    output = tmp_path / 'examples.jsonl'
    report = export(database('needs_review'), output, tmp_path / 'report.json')
    assert report['eligible_examples'] == 0
    assert report['excluded'] == {'step_not_accepted': 1}
    assert not output.exists()

def test_components_connect_shared_family_and_active_moiety():
    examples = [
        {"example_id": "a", "patent": {"family_id": "family-1"}, "compound": {"active_moiety_id": "moiety-1"}},
        {"example_id": "b", "patent": {"family_id": "family-1"}, "compound": {"active_moiety_id": "moiety-2"}},
        {"example_id": "c", "patent": {"family_id": "family-3"}, "compound": {"active_moiety_id": "moiety-2"}},
    ]
    assert len(set(components(examples).values())) == 1
