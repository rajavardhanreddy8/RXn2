#!/usr/bin/env python3
"""Apply explicit two-reviewer route curation decisions; dry-run by default."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DECISIONS_SCHEMA = "rxn2-route-curation-decisions-v1"
ALLOWED = {"accepted", "rejected", "needs_review"}


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def route_is_eligible(db: sqlite3.Connection, route_id: str) -> tuple[bool, list[str]]:
    steps = db.execute("SELECT step_id,evidence_status FROM process_step WHERE route_id=? ORDER BY step_order", (route_id,)).fetchall()
    if not steps:
        return False, ["route_has_no_steps"]
    reasons = []
    if any(row[1] != "performed" for row in steps):
        reasons.append("step_not_performed")
    ids = [row[0] for row in steps]
    marks = ",".join("?" for _ in ids)
    product_mass_steps = db.execute(
        f"""SELECT DISTINCT step_id FROM quantity_observation
            WHERE step_id IN ({marks}) AND quantity_kind='product_mass'
              AND normalized_value IS NOT NULL AND normalized_unit='g'""",
        ids,
    ).fetchall()
    if len(product_mass_steps) != len(ids):
        reasons.append("missing_normalized_product_mass")
    participant_steps = db.execute(
        f"""SELECT ps.step_id FROM process_step ps JOIN reaction_instance ri
                ON ri.evidence_span_id=ps.evidence_span_id
             JOIN reaction_participant rp ON rp.reaction_id=ri.reaction_id
             WHERE ps.step_id IN ({marks}) AND rp.role IN ('consumed','produced')
             GROUP BY ps.step_id HAVING count(DISTINCT rp.role)=2""",
        ids,
    ).fetchall()
    if len(participant_steps) != len(ids):
        reasons.append("missing_consumed_or_produced_participant")
    return not reasons, reasons


def validate(payload: dict, db: sqlite3.Connection) -> list[dict]:
    if payload.get("schema_version") != DECISIONS_SCHEMA:
        raise ValueError("unexpected decision template schema")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("decision file has no records")
    seen = set()
    validated = []
    for record in records:
        route_id = record.get("route_id")
        if not route_id or route_id in seen:
            raise ValueError(f"missing or duplicate route_id: {route_id!r}")
        seen.add(route_id)
        reviews = record.get("reviews")
        if not isinstance(reviews, list) or len(reviews) != 2:
            raise ValueError(f"{route_id}: exactly two reviews are required")
        reviewer_ids = [str(review.get("reviewer_id", "")).strip() for review in reviews]
        if not all(reviewer_ids) or len(set(reviewer_ids)) != 2:
            raise ValueError(f"{route_id}: two distinct reviewer_id values are required")
        decisions = [review.get("decision") for review in reviews]
        if any(decision not in ALLOWED for decision in decisions):
            raise ValueError(f"{route_id}: unsupported decision")
        if any(not str(review.get("rationale", "")).strip() for review in reviews):
            raise ValueError(f"{route_id}: every reviewer needs a rationale")
        exists = db.execute("SELECT 1 FROM process_route WHERE route_id=?", (route_id,)).fetchone()
        if not exists:
            raise ValueError(f"{route_id}: route not found")
        final = "accepted" if decisions == ["accepted", "accepted"] else ("rejected" if "rejected" in decisions else "needs_review")
        eligible, reasons = route_is_eligible(db, route_id)
        if final == "accepted" and not eligible:
            raise ValueError(f"{route_id}: cannot accept: {', '.join(reasons)}")
        validated.append({"route_id": route_id, "reviews": reviews, "final": final, "eligible": eligible, "reasons": reasons})
    return validated


def apply(db: sqlite3.Connection, decisions: list[dict]) -> None:
    now = timestamp()
    for item in decisions:
        route_id, final = item["route_id"], item["final"]
        candidate_id = f"route-curation:{route_id}"
        target = db.execute("SELECT target_compound_id FROM process_route WHERE route_id=?", (route_id,)).fetchone()[0]
        db.execute(
            """INSERT OR REPLACE INTO link_candidate
               (candidate_id,subject_type,subject_id,object_type,object_id,relationship_type,score,method,model_version,features_json,created_at)
               VALUES (?, 'process_route', ?, 'compound', ?, 'curation_review', 1.0, 'human_review', 'route-curation-v1', '{}', ?)""",
            (candidate_id, route_id, target, now),
        )
        for number, review in enumerate(item["reviews"], 1):
            decision_id = f"decision:{route_id}:{number}:{now[:19]}"
            db.execute(
                """INSERT INTO curation_decision
                   (decision_id,candidate_id,object_type,object_id,decision,reviewer_id,rationale,decided_at)
                   VALUES (?, ?, 'process_route', ?, ?, ?, ?, ?)""",
                (decision_id, candidate_id, route_id, review["decision"], review["reviewer_id"], review["rationale"], now),
            )
        db.execute("UPDATE process_route SET review_status=? WHERE route_id=?", (final, route_id))
        db.execute("UPDATE process_step SET review_status=? WHERE route_id=?", (final, route_id))
        db.execute(
            """UPDATE reaction_instance SET review_status=? WHERE evidence_span_id IN
               (SELECT evidence_span_id FROM process_step WHERE route_id=?)""",
            (final, route_id),
        )
        db.execute(
            """UPDATE evidence_span SET review_status=? WHERE evidence_span_id IN
               (SELECT evidence_span_id FROM process_step WHERE route_id=?)""",
            (final, route_id),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="Apply after validation; otherwise perform a dry run")
    parser.add_argument("--backup", type=Path, help="Required when --apply; destination must not exist")
    args = parser.parse_args()
    if args.apply and not args.backup:
        raise SystemExit("--backup is required with --apply")
    if args.backup and args.backup.exists():
        raise SystemExit(f"backup already exists: {args.backup}")
    try:
        payload = json.loads(args.decisions.read_text(encoding="utf-8"))
        with sqlite3.connect(args.db) as db:
            decisions = validate(payload, db)
            if args.apply:
                args.backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(args.db, args.backup)
                apply(db, decisions)
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("database integrity check failed")
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("database foreign-key check failed")
                db.commit()
    except (OSError, ValueError, sqlite3.Error) as error:
        raise SystemExit(f"ERROR: {error}") from error
    print(json.dumps({"status": "applied" if args.apply else "validated_dry_run", "decisions": [{key: item[key] for key in ('route_id','final','eligible','reasons')} for item in decisions]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
