import hashlib
import json
import sqlite3

from scripts.split_oversized_relation_jobs import split_failed


def test_split_queued_oversized_job_preserves_provenance(tmp_path):
    path = tmp_path / "split.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE evidence_span (
          evidence_span_id TEXT PRIMARY KEY, publication_number TEXT, source_id TEXT,
          artifact_sha256 TEXT, section_type TEXT, paragraph_id TEXT,
          char_start INTEGER, char_end INTEGER, evidence_text TEXT, text_sha256 TEXT,
          evidence_status TEXT, extraction_method TEXT, extractor_version TEXT,
          review_status TEXT, source_url TEXT, retrieved_at TEXT, license_code TEXT,
          redistribution_class TEXT
        );
        CREATE TABLE pipeline_job (
          pipeline_job_id TEXT PRIMARY KEY, job_type TEXT, input_identity TEXT,
          input_sha256 TEXT, status TEXT, attempt_count INTEGER, queued_at TEXT,
          completed_at TEXT, result_json TEXT, error_text TEXT
        );
    """)
    text = "Performed procedure. " * 300
    digest = hashlib.sha256(text.encode()).hexdigest()
    db.execute("INSERT INTO evidence_span VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "evidence-parent", "WO-TEST-A1", "fixture", "a" * 64, "example", "p1",
        100, 100 + len(text), text, digest, "performed", "fixture", "v1",
        "needs_review", "https://example.test", "2026-08-23T00:00:00Z", "test", "permitted",
    ))
    db.execute("INSERT INTO pipeline_job VALUES (?,?,?,?,?,?,?,?,?,?)", (
        "job-parent", "relation_extraction", "parent:auto", digest, "queued", 0,
        "2026-08-23T00:00:00Z", None,
        json.dumps({"evidence_span_id": "evidence-parent", "candidate_status": "evidence_only"}), None,
    ))
    db.commit(); db.close()
    report = split_failed(path, maximum=2000, apply=True)
    assert report["oversized_jobs"] == 1
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT status FROM pipeline_job WHERE pipeline_job_id='job-parent'").fetchone()[0] == "skipped"
        children = check.execute("SELECT count(*) FROM pipeline_job WHERE pipeline_job_id<>'job-parent' AND status='queued'").fetchone()[0]
        assert children == report["chunks"]
        first = check.execute("SELECT min(char_start),max(char_end) FROM evidence_span WHERE evidence_span_id<>'evidence-parent'").fetchone()
        assert first == (100, 100 + len(text))
