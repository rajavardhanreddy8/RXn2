import sqlite3

from scripts.build_pilot_queue import build_queue


def test_pilot_queue_prefers_target_specific_process_title():
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE drug_entity (drug_id TEXT PRIMARY KEY, preferred_name TEXT);
        CREATE TABLE drug_alias (drug_id TEXT, alias TEXT);
        CREATE TABLE compound (compound_id TEXT PRIMARY KEY, inchi_key TEXT);
        CREATE TABLE patent_document (
          publication_number TEXT PRIMARY KEY, title TEXT,
          publication_date TEXT, country_code TEXT
        );
        CREATE TABLE patent_family_member (family_id TEXT, publication_number TEXT);
        CREATE TABLE patent_candidate (
          drug_id TEXT, compound_id TEXT, publication_number TEXT,
          source_field_name TEXT, match_type TEXT, confidence REAL,
          review_status TEXT
        );
        INSERT INTO drug_entity VALUES ('drug:test', 'EXAMPLEDRUG');
        INSERT INTO compound VALUES ('compound:test', 'AAAAAAAAAAAAAA-UHFFFAOYSA-N');
        INSERT INTO patent_document VALUES
          ('WO-1-A1', 'Process for preparing EXAMPLEDRUG', '2020-01-01', 'WO'),
          ('WO-2-A1', 'New process synthesis intermediate manufacturing', '2026-01-01', 'WO');
        INSERT INTO patent_family_member VALUES ('surechembl:-1', 'WO-1-A1'), ('family:2', 'WO-2-A1');
        INSERT INTO patent_candidate VALUES
          ('drug:test', 'compound:test', 'WO-1-A1', 'ttl', 'exact_structure', .98, 'needs_review'),
          ('drug:test', 'compound:test', 'WO-2-A1', 'clms', 'exact_structure', .98, 'needs_review');
        """
    )

    queue = build_queue(db, (("drug:test", "test rationale"),))

    assert queue[0]["publication_number"] == "WO-1-A1"
    assert queue[0]["status"] == "candidate_only"
    assert queue[0]["direct_synthesis_title"] is True
    assert queue[0]["negative_title_hits"] == 0
