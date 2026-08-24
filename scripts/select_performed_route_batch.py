#!/usr/bin/env python3
"""Select a conservative batch of native-text performed synthesis examples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_CANDIDATES = Path(
    r"I:\My Drive\RXN2\data\processed\epo_ops\reaction-candidates-2026-08-17-v4"
)
DEFAULT_OUTPUT = Path(
    r"I:\My Drive\RXN2\data\processed\epo_ops\performed-route-batch-selection-2026-08-17-v2"
)
SELECTOR_VERSION = "performed-route-batch-selector-v2"
NATIVE_EXTRACTION_METHODS = {
    "deterministic_native_xml",
    "epo_ops_native_xml",
    "epo_ops_family_native_xml",
}

PERFORMED_CUES = {
    "charged": re.compile(r"\b(?:was|were) charged\b|\bcharged with\b", re.I),
    "added": re.compile(r"\b(?:was|were) added\b|\badded (?:to|dropwise|slowly)\b", re.I),
    "stirred": re.compile(r"\bstirred\b", re.I),
    "heated": re.compile(r"\bheated\b|\breflux(?:ed)?\b", re.I),
    "cooled": re.compile(r"\bcooled\b", re.I),
    "filtered": re.compile(r"\bfiltered\b", re.I),
    "isolated": re.compile(r"\b(?:obtained|gave|yielded|isolated|precipitated)\b", re.I),
}
INPUT_CUE = re.compile(
    r"\b(?:to a (?:solution|suspension)|was taken|were taken|was charged|were charged|charged with)\b",
    re.I,
)
OUTCOME_CUE = re.compile(
    r"\b(?:yield|purity|obtained|gave|yielded|isolated|precipitated|title compound)\b",
    re.I,
)
SYNTHESIS_CUE = re.compile(r"\b(?:preparation|prepare|synthesis|reaction mixture)\b", re.I)
MULTI_EXAMPLE = re.compile(r"\bexample\s*[-:]?\s*\d+\b", re.I)
MULTI_METHOD = re.compile(r"\bmethod\s+[A-Z]\b", re.I)
REFERENCE_ONLY = re.compile(
    r"\b(?:describes|according to|reported in|prepared as described|literature example|"
    r"following the procedures? outlined|following the procedures? described|"
    r"as outlined in (?:example|examples)|as described in (?:example|examples))\b",
    re.I,
)
NON_SYNTHETIC = re.compile(
    r"\b(?:purification|crystalline form|polymorph|formulation|tablet|capsule|"
    r"suspension formulation|experimental table|preparation of conjugate)\b",
    re.I,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected object at {path}:{line_number}")
        records.append(value)
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def procedure_metrics(text: str) -> dict[str, Any]:
    cue_names = sorted(name for name, pattern in PERFORMED_CUES.items() if pattern.search(text))
    example_count = len(MULTI_EXAMPLE.findall(text))
    method_count = len(MULTI_METHOD.findall(text))
    return {
        "performed_cues": cue_names,
        "performed_cue_count": len(cue_names),
        "has_input_cue": bool(INPUT_CUE.search(text)),
        "has_outcome_cue": bool(OUTCOME_CUE.search(text)),
        "has_synthesis_cue": bool(SYNTHESIS_CUE.search(text)),
        "example_heading_count": example_count,
        "method_heading_count": method_count,
        "reference_only_cue": bool(REFERENCE_ONLY.search(text)),
        "non_synthetic_cue": bool(NON_SYNTHETIC.search(text[:800])),
        "evidence_text_length": len(text),
    }


def classify_candidate(
    candidate: dict[str, Any],
    evidence: dict[str, Any] | None,
    already_in_graph: bool = False,
) -> tuple[str, list[str], int, dict[str, Any]]:
    reasons: list[str] = []
    if candidate.get("candidate_status") != "participant_roles_partial":
        reasons.append("not_partial_participant_candidate")
    if evidence is None:
        return "excluded", reasons + ["missing_unique_evidence_span"], 0, {}
    text = evidence["evidence_text"]
    metrics = procedure_metrics(text)
    if already_in_graph:
        reasons.append("already_in_graph")
    if (
        evidence.get("source_id") != "epo_ops"
        or evidence.get("extraction_method") not in NATIVE_EXTRACTION_METHODS
    ):
        reasons.append("not_native_xml")
    if evidence.get("evidence_status") == "excluded_non_experimental":
        reasons.append("non_experimental_evidence")
    if metrics["reference_only_cue"]:
        reasons.append("reference_or_prior_art_only")
    if metrics["non_synthetic_cue"]:
        reasons.append("purification_formulation_or_solid_form")
    if metrics["example_heading_count"] > 1 or metrics["method_heading_count"] > 1:
        reasons.append("multiple_procedures_in_evidence_span")
    if metrics["performed_cue_count"] < 4:
        reasons.append("insufficient_performed_procedure_cues")
    if not metrics["has_input_cue"]:
        reasons.append("missing_explicit_input_cue")
    if not metrics["has_outcome_cue"]:
        reasons.append("missing_isolated_outcome_cue")
    if not metrics["has_synthesis_cue"]:
        reasons.append("missing_synthesis_context")
    if candidate.get("reported_outcome_count", 0) < 1:
        reasons.append("no_structured_reported_outcome")
    if metrics["evidence_text_length"] < 180:
        reasons.append("evidence_span_too_short")
    if metrics["evidence_text_length"] > 4500:
        reasons.append("evidence_span_too_broad")

    if reasons:
        return "excluded", sorted(set(reasons)), 0, metrics

    score = (
        30
        + min(candidate.get("reported_outcome_count", 0), 4) * 8
        + min(candidate.get("measurement_count", 0), 30)
        + metrics["performed_cue_count"] * 4
        + min(candidate.get("target_mention_count", 0), 3) * 3
    )
    return "eligible", [], score, metrics


def evidence_already_in_graph(
    evidence: dict[str, Any],
    used_evidence_ids: set[str],
    used_text_by_publication: dict[str, list[str]],
) -> bool:
    if evidence["evidence_span_id"] in used_evidence_ids:
        return True
    candidate_text = evidence.get("evidence_text") or ""
    return any(len(text) >= 120 and text in candidate_text for text in used_text_by_publication.get(evidence["publication_number"], []))
def select_batch(
    db: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    batch_size: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_by_hash: dict[str, list[dict[str, Any]]] = {}
    for row in db.execute(
        """SELECT evidence_span_id, publication_number, source_id, artifact_sha256,
                  paragraph_id, text_sha256, evidence_text, evidence_status,
                  extraction_method, source_url, review_status
           FROM evidence_span"""
    ):
        evidence_by_hash.setdefault(row["text_sha256"], []).append(dict(row))
    used_evidence_ids: set[str] = set()
    used_text_by_publication: dict[str, list[str]] = {}
    for row in db.execute("SELECT e.evidence_span_id, e.publication_number, e.evidence_text FROM process_step ps JOIN evidence_span e USING (evidence_span_id)"):
        used_evidence_ids.add(row["evidence_span_id"])
        if row["evidence_text"]:
            used_text_by_publication.setdefault(row["publication_number"], []).append(row["evidence_text"])

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("candidate_status") != "participant_roles_partial":
            continue
        matches = evidence_by_hash.get(candidate["evidence_text_sha256"], [])
        evidence = matches[0] if len(matches) == 1 else None
        status, reasons, score, metrics = classify_candidate(
            candidate,
            evidence,
            bool(
                evidence
                and evidence_already_in_graph(
                    evidence, used_evidence_ids, used_text_by_publication
                )
            ),
        )
        record = {
            "reaction_candidate_id": candidate["reaction_candidate_id"],
            "example_id": candidate["example_id"],
            "drug_id": candidate["drug_id"],
            "target_compound_id": candidate["target_compound_id"],
            "publication_number": candidate["publication_number"],
            "source_publication_number": candidate["source_publication_number"],
            "heading": candidate["heading"],
            "evidence_text_sha256": candidate["evidence_text_sha256"],
            "measurement_count": candidate.get("measurement_count", 0),
            "reported_outcome_count": candidate.get("reported_outcome_count", 0),
            "selected_participant_role_count": candidate.get("selected_participant_role_count", 0),
            "selector_status": status,
            "selector_score": score,
            "reason_codes": reasons,
            "metrics": metrics,
            "review_status": "unreviewed",
            "human_review_required": True,
            "creates_curated_reaction": False,
        }
        if evidence:
            record.update(
                {
                    "evidence_span_id": evidence["evidence_span_id"],
                    "evidence_publication_number": evidence["publication_number"],
                    "evidence_artifact_sha256": evidence["artifact_sha256"],
                    "paragraph_id": evidence["paragraph_id"],
                    "source_url": evidence["source_url"],
                }
            )
        (eligible if status == "eligible" else excluded).append(record)

    eligible.sort(
        key=lambda record: (
            -record["selector_score"],
            record["drug_id"],
            record["reaction_candidate_id"],
        )
    )
    selected: list[dict[str, Any]] = []
    backlog: list[dict[str, Any]] = []
    selected_drugs: set[str] = set()
    for record in eligible:
        if len(selected) < batch_size and record["drug_id"] not in selected_drugs:
            selected_record = dict(record)
            selected_record["selector_status"] = "selected"
            selected.append(selected_record)
            selected_drugs.add(record["drug_id"])
        else:
            backlog_record = dict(record)
            backlog_record["selector_status"] = "eligible_backlog"
            backlog_record["reason_codes"] = [
                "batch_limit" if len(selected) >= batch_size else "one_candidate_per_drug_limit"
            ]
            backlog.append(backlog_record)
    return selected, backlog, excluded


def build_selection(
    db_path: Path,
    candidate_dir: Path,
    output_dir: Path,
    batch_size: int,
) -> dict[str, Any]:
    source_path = candidate_dir / "reaction_candidates.jsonl"
    source_manifest = candidate_dir / "manifest.json"
    candidates = read_jsonl(source_path)
    expected = json.loads(source_manifest.read_text(encoding="utf-8"))["files"]
    expected_sha = next(item["sha256"] for item in expected if item["file"] == source_path.name)
    if sha256_file(source_path) != expected_sha:
        raise ValueError("reaction candidate checksum does not match its manifest")

    db = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        selected, backlog, excluded = select_batch(db, candidates, batch_size)
    finally:
        db.close()

    partial_input = sum(
        candidate.get("candidate_status") == "participant_roles_partial" for candidate in candidates
    )
    if partial_input != len(selected) + len(backlog) + len(excluded):
        raise RuntimeError("selection accounting mismatch")

    if output_dir.exists() or output_dir.with_suffix(".partial").exists():
        raise FileExistsError(output_dir)
    partial = output_dir.with_suffix(".partial")
    partial.mkdir(parents=True)
    write_jsonl(partial / "selected_candidates.jsonl", selected)
    write_jsonl(partial / "eligible_backlog.jsonl", backlog)
    write_jsonl(partial / "excluded_candidates.jsonl", excluded)

    reason_counts = Counter(
        reason for record in excluded for reason in record.get("reason_codes", [])
    )
    files = []
    for name in (
        "selected_candidates.jsonl",
        "eligible_backlog.jsonl",
        "excluded_candidates.jsonl",
    ):
        path = partial / name
        files.append(
            {"file": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        )
    manifest = {
        "dataset": "RXN2 conservative performed-route batch selection",
        "selector_version": SELECTOR_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "succeeded",
        "counts": {
            "partial_input": partial_input,
            "selected": len(selected),
            "eligible_backlog": len(backlog),
            "excluded": len(excluded),
            "selected_drugs": len({record["drug_id"] for record in selected}),
        },
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "input": {
            "reaction_candidates_sha256": expected_sha,
            "reaction_candidates_manifest_sha256": sha256_file(source_manifest),
        },
        "files": files,
        "safety": {
            "creates_database_reactions": False,
            "creates_routes": False,
            "accepts_chemistry": False,
            "requires_native_xml": True,
            "rejects_multi_procedure_spans": True,
            "rejects_purification_formulation_and_solid_form_examples": True,
            "human_review_required": True,
        },
    }
    (partial / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    partial.replace(output_dir)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    manifest = build_selection(
        args.db.resolve(), args.candidate_dir.resolve(), args.output_dir.resolve(), args.batch_size
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

