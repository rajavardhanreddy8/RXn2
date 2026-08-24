#!/usr/bin/env python3
"""Seed, enrich, and export RXN2's evidence-bounded provisional relation graph."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import db as db_module  # noqa: E402
from apps.api.app.relations import (  # noqa: E402
    insert_relation,
    process_evidence_span,
    provisional_graph,
    sha256_text,
    stable_id,
)


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed JSONL at {path}:{line_number}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def configure_database(target: Path, source: Path | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() and source:
        if source.resolve() == target.resolve():
            raise ValueError("--source-db and --db must be different files")
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
    db_module.DB_PATH = target
    # Worker processes share an already seeded database. Avoid concurrent schema DDL.
    with sqlite3.connect(target) as connection:
        ready = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='relation_candidate'"
        ).fetchone()
    if not ready:
        db_module.initialize()


def relation_record(job_id: str, evidence_span_id: str, key: str, **values) -> dict:
    return {
        "relation_candidate_id": stable_id(
            "relation-candidate", job_id, evidence_span_id, key
        ),
        "extraction_job_id": job_id,
        "evidence_span_id": evidence_span_id,
        "subject_char_start": None,
        "subject_char_end": None,
        "subject_compound_id": None,
        "object_char_start": None,
        "object_char_end": None,
        "object_compound_id": None,
        "attributes_json": "{}",
        "model_confidence": 1.0,
        "is_explicit": 1,
        "validation_status": "unresolved",
        "validation_reason": "requires_review",
        "review_status": "needs_review",
        "created_at": datetime.now(UTC).isoformat(),
        **values,
    }


def seed_graph(db_path: Path, reaction_dir: Path, output_dir: Path) -> dict:
    reactions_path = reaction_dir / "reaction_candidates.jsonl"
    participants_path = reaction_dir / "participant_candidates.jsonl"
    manifest_path = reaction_dir / "manifest.json"
    for path in (reactions_path, participants_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    reactions = list(read_jsonl(reactions_path))
    participants: dict[str, list[dict]] = defaultdict(list)
    for row in read_jsonl(participants_path):
        participants[row["reaction_candidate_id"]].append(row)

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        evidence_by_hash = {
            row["text_sha256"]: dict(row)
            for row in db.execute(
                """SELECT evidence_span_id, publication_number, evidence_text,
                          text_sha256, source_url FROM evidence_span"""
            )
        }
        compound_ids = {row[0] for row in db.execute("SELECT compound_id FROM compound")}
    finally:
        db.close()

    jobs: list[dict] = []
    relations: list[dict] = []
    failures: list[dict] = []
    queue_rows: list[dict] = []
    now = datetime.now(UTC).isoformat()
    prompt_sha = sha256_text("deterministic EPO relation seed v1")

    for reaction in reactions:
        evidence = evidence_by_hash.get(reaction["evidence_text_sha256"])
        if not evidence:
            failures.append({
                "reaction_candidate_id": reaction["reaction_candidate_id"],
                "reason": "missing_evidence_span",
            })
            continue
        evidence_span_id = evidence["evidence_span_id"]
        excluded = reaction["candidate_status"].startswith("excluded_")
        job_id = stable_id(
            "extraction-job", "deterministic", "epo-reaction-candidate-v4",
            prompt_sha, reaction["evidence_text_sha256"],
        )
        jobs.append({
            "extraction_job_id": job_id,
            "provider": "deterministic",
            "model": "epo-reaction-candidate-v4",
            "prompt_sha256": prompt_sha,
            "input_sha256": reaction["evidence_text_sha256"],
            "response_sha256": sha256_text(json.dumps(reaction, sort_keys=True)),
            "source_url": evidence["source_url"],
            "raw_response_json": json.dumps({"candidate": reaction}),
            "token_cost_json": "{}",
            "status": "needs_review",
            "review_status": "needs_review",
            "created_at": now,
            "completed_at": now,
        })
        procedure_text = f"procedure:{evidence_span_id}"
        relations.append(relation_record(
            job_id,
            evidence_span_id,
            f"describes:{reaction['reaction_candidate_id']}",
            subject_type="patent",
            subject_text=evidence["publication_number"],
            predicate="describes",
            object_type="procedure",
            object_text=procedure_text,
            attributes_json=json.dumps({
                "reaction_candidate_id": reaction["reaction_candidate_id"],
                "candidate_status": reaction["candidate_status"],
                "heading": reaction.get("heading"),
            }),
            validation_status="rejected" if excluded else "unresolved",
            validation_reason=(
                "excluded_non_experimental_heading" if excluded
                else "deterministic_procedure_candidate"
            ),
            review_status="rejected" if excluded else "needs_review",
        ))

        for index, participant in enumerate(
            participants.get(reaction["reaction_candidate_id"], [])
        ):
            start, end = participant["char_start"], participant["char_end"]
            surface = participant["surface_text"]
            offset_valid = evidence["evidence_text"][start:end] == surface
            compound_id = participant.get("canonical_compound_id")
            if compound_id not in compound_ids:
                compound_id = None
            resolution = participant.get("resolution_level", "unresolved")
            if excluded or not offset_valid:
                status = "rejected"
                reason = (
                    "excluded_non_experimental_heading" if excluded else "offset_mismatch"
                )
            elif compound_id and resolution == "exact_structure":
                status, reason = "validated", "exact_offset_and_structure"
            else:
                status, reason = "unresolved", resolution
            mention = relation_record(
                job_id,
                evidence_span_id,
                f"mention:{participant['participant_candidate_id']}",
                subject_type="procedure",
                subject_text=procedure_text,
                predicate="mentions",
                object_type="compound",
                object_text=surface,
                object_char_start=start if offset_valid else None,
                object_char_end=end if offset_valid else None,
                object_compound_id=compound_id,
                attributes_json=json.dumps({
                    "participant_candidate_id": participant["participant_candidate_id"],
                    "resolution_level": resolution,
                    "candidate_compound_ids": participant.get("candidate_compound_ids", []),
                }),
                model_confidence=1.0 if offset_valid else 0.0,
                validation_status=status,
                validation_reason=reason,
                review_status="rejected" if status == "rejected" else "needs_review",
            )
            relations.append(mention)
            selected_role = participant.get("selected_role")
            if not selected_role:
                continue
            confidence = max(
                (
                    item.get("confidence", 0)
                    for item in participant.get("role_candidates", [])
                    if item.get("role") == selected_role
                ),
                default=0,
            )
            common = {
                "predicate": selected_role,
                "attributes_json": json.dumps({
                    "participant_candidate_id": participant["participant_candidate_id"],
                    "source": "deterministic_adjacent_cue",
                }),
                "model_confidence": confidence,
                "validation_status": status,
                "validation_reason": reason,
                "review_status": "rejected" if status == "rejected" else "needs_review",
            }
            if selected_role == "produced":
                common.update({
                    "subject_type": "procedure", "subject_text": procedure_text,
                    "object_type": "compound", "object_text": surface,
                    "object_char_start": start if offset_valid else None,
                    "object_char_end": end if offset_valid else None,
                    "object_compound_id": compound_id,
                })
            else:
                common.update({
                    "subject_type": "compound", "subject_text": surface,
                    "subject_char_start": start if offset_valid else None,
                    "subject_char_end": end if offset_valid else None,
                    "subject_compound_id": compound_id,
                    "object_type": "procedure", "object_text": procedure_text,
                })
            relations.append(relation_record(
                job_id,
                evidence_span_id,
                f"role:{participant['participant_candidate_id']}:{selected_role}",
                **common,
            ))

        if not excluded:
            input_identity = f"{evidence_span_id}:auto"
            queue_rows.append({
                "pipeline_job_id": stable_id(
                    "pipeline-job", "relation_extraction", input_identity
                ),
                "input_identity": input_identity,
                "input_sha256": reaction["evidence_text_sha256"],
                "queued_at": now,
                "result_json": json.dumps({
                    "evidence_span_id": evidence_span_id,
                    "provider_mode": "auto",
                    "candidate_status": reaction["candidate_status"],
                    "reaction_candidate_id": reaction["reaction_candidate_id"],
                }),
            })

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys=ON")
    try:
        db.execute("BEGIN IMMEDIATE")
        deterministic_jobs = "SELECT extraction_job_id FROM extraction_job WHERE provider='deterministic' AND model='epo-reaction-candidate-v4'"
        db.execute(f"DELETE FROM relation_candidate WHERE extraction_job_id IN ({deterministic_jobs})")
        db.execute("DELETE FROM extraction_job WHERE provider='deterministic' AND model='epo-reaction-candidate-v4'")
        for job in jobs:
            columns = tuple(job)
            db.execute(
                f"INSERT OR IGNORE INTO extraction_job ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                tuple(job[column] for column in columns),
            )
        for relation in relations:
            insert_relation(db, relation)
        for queued in queue_rows:
            db.execute(
                """INSERT OR IGNORE INTO pipeline_job
                   (pipeline_job_id, job_type, input_identity, input_sha256, status,
                    attempt_count, queued_at, result_json)
                   VALUES (?, 'relation_extraction', ?, ?, 'queued', 0, ?, ?)""",
                (
                    queued["pipeline_job_id"], queued["input_identity"],
                    queued["input_sha256"], queued["queued_at"], queued["result_json"],
                ),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return export_graph(
        db_path,
        output_dir,
        failures,
        inputs={
            "reaction_candidates": sha256_file(reactions_path),
            "participant_candidates": sha256_file(participants_path),
            "source_manifest": sha256_file(manifest_path),
        },
    )


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(partial, path)


def write_json(path: Path, value: dict) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(
        json.dumps(value, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(partial, path)


def export_graph(
    db_path: Path,
    output_dir: Path,
    failures: list[dict] | None = None,
    inputs: dict | None = None,
) -> dict:
    if inputs is None and (output_dir / "manifest.json").is_file():
        inputs = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8")).get("inputs", {})
    db_module.DB_PATH = db_path
    graph = provisional_graph(limit=50_000)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        relations = [dict(row) for row in db.execute(
            """SELECT r.*, e.publication_number FROM relation_candidate r
               JOIN evidence_span e USING (evidence_span_id)
               ORDER BY e.publication_number, r.evidence_span_id,
                        r.relation_candidate_id"""
        )]
        counts = {
            "procedures": db.execute(
                "SELECT count(*) FROM relation_candidate WHERE predicate='describes'"
            ).fetchone()[0],
            "unique_evidence_procedures": db.execute(
                "SELECT count(DISTINCT evidence_span_id) FROM relation_candidate"
            ).fetchone()[0],
            "relations": len(relations),
            "mention_relations": db.execute(
                "SELECT count(*) FROM relation_candidate WHERE predicate='mentions'"
            ).fetchone()[0],
            "llm_jobs": db.execute(
                "SELECT count(*) FROM extraction_job WHERE provider IN ('groq','openrouter')"
            ).fetchone()[0],
            "accepted_chemistry": db.execute(
                "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
            ).fetchone()[0],
        }
        counts["validation"] = dict(db.execute(
            """SELECT validation_status, count(*) FROM relation_candidate
               GROUP BY validation_status"""
        ).fetchall())
        counts["queue"] = dict(db.execute(
            """SELECT status, count(*) FROM pipeline_job
               WHERE job_type='relation_extraction' GROUP BY status"""
        ).fetchall())
    finally:
        db.close()

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "relation_candidates.jsonl", relations)
    write_jsonl(output_dir / "graph_nodes.jsonl", graph["nodes"])
    write_jsonl(output_dir / "graph_edges.jsonl", graph["edges"])
    write_jsonl(output_dir / "failed_records.jsonl", failures or [])
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "database": str(db_path.resolve()),
        "counts": counts,
        "provisional_reaction_count": graph["provisional_reaction_count"],
        "automatic_acceptance": False,
        "accepted_chemistry_count": graph["accepted_chemistry_count"],
    }
    write_json(output_dir / "report.json", report)
    files = {}
    for name in (
        "relation_candidates.jsonl", "graph_nodes.jsonl", "graph_edges.jsonl",
        "failed_records.jsonl", "report.json",
    ):
        path = output_dir / name
        files[name] = {"size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
    manifest = {
        "dataset": "RXN2 provisional relation graph",
        "schema_version": "rxn2-relation-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "succeeded",
        "review_status": "needs_review",
        "automatic_acceptance": False,
        "inputs": inputs or {},
        "counts": counts,
        "files": files,
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


async def run_jobs(
    db_path: Path,
    output_dir: Path,
    limit: int,
    concurrency: int,
    export_output: bool = True,
    provider_mode: str | None = None,
) -> dict:
    if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("op_api_key") or os.getenv("GROQ_API_KEY") or os.getenv("HF_TOKEN")):
        raise RuntimeError("Configure OPENROUTER_API_KEY (or op_api_key) or GROQ_API_KEY before LLM enrichment")
    # Several provider workers may claim jobs at once. SQLite has one writer; wait rather than fail a valid extraction.
    db = sqlite3.connect(db_path, timeout=90)
    db.execute("PRAGMA busy_timeout=90000")
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        jobs = [dict(row) for row in db.execute(
            """SELECT p.pipeline_job_id, p.result_json FROM pipeline_job p
               JOIN evidence_span e ON e.evidence_span_id=json_extract(p.result_json, '$.evidence_span_id')
               WHERE p.job_type='relation_extraction' AND p.status='queued'
               ORDER BY CASE json_extract(p.result_json, '$.candidate_status')
                          WHEN 'participant_roles_partial' THEN 0 ELSE 1 END,
                        p.attempt_count, length(e.evidence_text), p.queued_at LIMIT ?""",
            (limit,),
        )]
        for job in jobs:
            db.execute(
                """UPDATE pipeline_job SET status='running', started_at=?,
                   attempt_count=attempt_count+1 WHERE pipeline_job_id=? AND status='queued'""",
                (datetime.now(UTC).isoformat(), job["pipeline_job_id"]),
            )
        db.commit()
    finally:
        db.close()

    semaphore = asyncio.Semaphore(concurrency)

    async def worker(job: dict):
        payload = json.loads(job["result_json"])
        async with semaphore:
            try:
                result = await process_evidence_span(
                    payload["evidence_span_id"], provider_mode or payload.get("provider_mode", "auto")
                )
                status, error = "succeeded", None
                payload["result"] = result
            except Exception as exc:
                status, error = "failed", str(exc)
            db = sqlite3.connect(db_path, timeout=90)
            db.execute("PRAGMA busy_timeout=90000")
            try:
                db.execute(
                    """UPDATE pipeline_job SET status=?, completed_at=?, result_json=?,
                       error_text=? WHERE pipeline_job_id=?""",
                    (
                        status, datetime.now(UTC).isoformat(), json.dumps(payload),
                        error, job["pipeline_job_id"],
                    ),
                )
                db.commit()
            finally:
                db.close()

    await asyncio.gather(*(worker(job) for job in jobs))
    if export_output:
        return export_graph(db_path, output_dir)
    return {"processed": len(jobs), "queue": dict(
        sqlite3.connect(db_path).execute(
            "SELECT status, count(*) FROM pipeline_job WHERE job_type='relation_extraction' GROUP BY status"
        )
    )}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed = subparsers.add_parser("seed", help="Build the complete deterministic graph")
    seed.add_argument("--source-db", type=Path)
    seed.add_argument("--db", type=Path, required=True)
    seed.add_argument("--reaction-dir", type=Path, required=True)
    seed.add_argument("--output-dir", type=Path, required=True)

    run = subparsers.add_parser("run", help="Enrich queued procedures with free LLMs")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--limit", type=int, default=318)
    run.add_argument("--concurrency", type=int, default=6)
    run.add_argument("--no-export", action="store_true", help="Do not write shared graph artifacts after this run")
    run.add_argument("--provider", choices=("auto", "openrouter", "groq", "huggingface"), default="auto")

    export = subparsers.add_parser("export", help="Refresh graph artifacts")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args()
    load_env(args.env_file)
    if args.command == "seed":
        configure_database(args.db, args.source_db)
        result = seed_graph(args.db, args.reaction_dir, args.output_dir)
    elif args.command == "run":
        configure_database(args.db)
        result = asyncio.run(
            run_jobs(args.db, args.output_dir, args.limit, args.concurrency, not args.no_export, args.provider)
        )
    else:
        configure_database(args.db)
        result = export_graph(args.db, args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
