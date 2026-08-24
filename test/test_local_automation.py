import json
import os
import sqlite3

import fitz

from scripts.annotate_catalogue import seed_periodic_table
from scripts.local_automation import extract_embedded_text, load_env_file, run_job


def test_env_file_loads_without_overriding_process(tmp_path, monkeypatch):
    monkeypatch.setenv("RXN2_EXISTING", "process")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# fixture\nRXN2_EXISTING=file\nRXN2_TEST_ENV_KEY='test-value'\n",
        encoding="utf-8",
    )
    load_env_file(env_file)
    assert os.environ["RXN2_EXISTING"] == "process"
    assert os.environ["RXN2_TEST_ENV_KEY"] == "test-value"


def test_local_job_is_idempotent(tmp_path):
    db = sqlite3.connect(tmp_path / "automation.sqlite")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE pipeline_job (
          pipeline_job_id TEXT PRIMARY KEY, job_type TEXT, input_identity TEXT,
          input_sha256 TEXT, status TEXT, attempt_count INTEGER, queued_at TEXT,
          started_at TEXT, completed_at TEXT, result_json TEXT, error_text TEXT,
          UNIQUE(job_type, input_identity))"""
    )
    calls = []
    first = run_job(db, "fixture", "same-input", lambda: calls.append(1) or {"ok": True})
    second = run_job(db, "fixture", "same-input", lambda: calls.append(2) or {"ok": False})
    assert first["status"] == "succeeded"
    assert second["status"] == "skipped"
    assert calls == [1]
    assert db.execute("SELECT attempt_count FROM pipeline_job").fetchone()[0] == 1


def test_embedded_pdf_text_keeps_page_locations(tmp_path):
    pdf = tmp_path / "WO-TEST-A1.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Example 1: product was isolated in 80 percent yield.")
    document.save(pdf)
    document.close()
    output = tmp_path / "result"
    result = extract_embedded_text(pdf, output, "WO-TEST-A1", "a" * 64)
    metadata = json.loads((output / "result.json").read_text(encoding="utf-8"))
    page_record = json.loads((output / "pages.jsonl").read_text(encoding="utf-8"))
    assert result["pages"] == 1
    assert metadata["status"] == "succeeded"
    assert page_record["page"] == 1
    assert page_record["char_start"] == 0
    assert "80 percent yield" in page_record["text"]


def test_periodic_table_seeds_all_elements(tmp_path):
    db = sqlite3.connect(tmp_path / "elements.sqlite")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE element (element_id INTEGER PRIMARY KEY, atomic_number INTEGER UNIQUE, symbol TEXT UNIQUE, name TEXT)"
    )
    assert seed_periodic_table(db) == 118
    assert db.execute("SELECT symbol FROM element WHERE atomic_number=118").fetchone()[0] == "Og"
