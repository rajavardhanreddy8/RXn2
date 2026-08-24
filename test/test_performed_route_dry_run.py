from __future__ import annotations

import hashlib
import sqlite3

from scripts.ingest_performed_route import main
from test_ingest_performed_route import make_artifact, make_database


def test_cli_dry_run_rolls_back_all_route_records(tmp_path):
    artifact = make_artifact(tmp_path / "artifact")
    database = tmp_path / "rxn2.sqlite"
    make_database(database).close()
    before_hash = hashlib.sha256(database.read_bytes()).hexdigest()

    assert main([str(artifact), "--db", str(database), "--release", "dry-run", "--dry-run"]) == 0
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_hash

    db = sqlite3.connect(database)
    try:
        for table in (
            "compound_relationship",
            "evidence_span",
            "process_route",
            "process_step",
            "reaction_instance",
            "reaction_participant",
            "reaction_condition",
            "quantity_observation",
            "outcome_observation",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM source_release WHERE release_id='epo_ops:dry-run'"
        ).fetchone()[0] == 0
    finally:
        db.close()
