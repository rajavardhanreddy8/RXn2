#!/usr/bin/env python3
"""Build evidence-linked, unreviewed reaction candidates from EPO example blocks.

This stage is deliberately conservative: it resolves names through structures already
stored in RXN2 and assigns participant roles only when an explicit textual cue is
adjacent to a mention. It never writes accepted chemistry or curated graph edges.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROLE_RULES: tuple[tuple[str, float, re.Pattern[str], re.Pattern[str]], ...] = (
    (
        "produced",
        0.95,
        re.compile(r"(?:afforded|yielded|gave|giving|obtained|isolated|to give|to afford)\s+(?:the\s+)?$", re.I),
        re.compile(r"^\s+(?:was|were)\s+(?:obtained|isolated|afforded)", re.I),
    ),
    (
        "solvent",
        0.95,
        re.compile(r"(?:dissolved|suspended|slurried)\s+in\s+$", re.I),
        re.compile(r"^\s+as\s+(?:the\s+)?solvent\b", re.I),
    ),
    (
        "catalyst",
        0.95,
        re.compile(r"(?:catalyst|catalytic\s+amount\s+of)\s*$", re.I),
        re.compile(r"^\s+(?:as\s+)?(?:a\s+)?catalyst\b", re.I),
    ),
    (
        "workup",
        0.90,
        re.compile(r"(?:washed|extracted|quenched)\s+with\s+$", re.I),
        re.compile(r"^\s+(?:wash|extract|quench)\b", re.I),
    ),
    (
        "consumed",
        0.90,
        re.compile(r"(?:added|charged|treated\s+with|reacted\s+with|solution\s+of|mixture\s+of)\s+$", re.I),
        re.compile(r"^\s+(?:was|were)\s+(?:added|charged|treated|reacted)\b", re.I),
    ),
    (
        "reagent",
        0.90,
        re.compile(r"(?:reagent|using)\s+$", re.I),
        re.compile(r"^\s+as\s+(?:a\s+)?reagent\b", re.I),
    ),
)


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:24]}"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSONL at {path}:{line_number}") from exc


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_structure_maps(db: sqlite3.Connection):
    compounds = {
        row["compound_id"]: dict(row)
        for row in db.execute(
            "SELECT compound_id, inchi_key, connectivity_key, review_status FROM compound"
        )
    }
    drug_compounds: dict[str, list[dict]] = defaultdict(list)
    for row in db.execute(
        """SELECT dc.drug_id, dc.compound_id, c.inchi_key, c.connectivity_key,
                  dc.review_status AS link_review_status, c.review_status AS compound_review_status
           FROM drug_compound dc JOIN compound c ON c.compound_id = dc.compound_id
           WHERE dc.review_status <> 'rejected' AND c.review_status <> 'rejected'"""
    ):
        drug_compounds[row["drug_id"]].append(dict(row))
    return compounds, drug_compounds


def resolve_mention(mention: dict, target_compound_id: str, compounds: dict, drug_compounds: dict) -> dict:
    rows: dict[str, dict] = {}
    for candidate in mention["candidate_entities"]:
        if candidate["entity_type"] == "compound":
            row = compounds.get(candidate["entity_id"])
            if row:
                rows[row["compound_id"]] = row
        elif candidate["entity_type"] == "drug":
            for row in drug_compounds.get(candidate["entity_id"], []):
                rows[row["compound_id"]] = row

    exact_keys = {row["inchi_key"] for row in rows.values() if row.get("inchi_key")}
    connectivity_keys = {
        row["connectivity_key"] for row in rows.values() if row.get("connectivity_key")
    }
    if len(exact_keys) == 1:
        level = "exact_structure"
        eligible = [row for row in rows.values() if row.get("inchi_key") in exact_keys]
    elif len(connectivity_keys) == 1:
        level = "connectivity_only"
        eligible = [
            row for row in rows.values() if row.get("connectivity_key") in connectivity_keys
        ]
    elif not exact_keys and not connectivity_keys:
        level = "unresolved_no_structure"
        eligible = []
    else:
        level = "unresolved_multiple_structures"
        eligible = []

    ids = sorted({row["compound_id"] for row in eligible})
    canonical = target_compound_id if target_compound_id in ids else (ids[0] if ids else None)
    return {
        "resolution_level": level,
        "canonical_compound_id": canonical,
        "candidate_compound_ids": ids,
        "exact_inchi_keys": sorted(exact_keys),
        "connectivity_keys": sorted(connectivity_keys),
    }


def role_candidates(text: str, start: int, end: int) -> list[dict]:
    before = text[max(0, start - 100) : start]
    after = text[end : min(len(text), end + 100)]
    candidates = []
    for role, confidence, before_rule, after_rule in ROLE_RULES:
        side = None
        if before_rule.search(before):
            side = "before"
        elif after_rule.search(after):
            side = "after"
        if side:
            candidates.append(
                {
                    "role": role,
                    "confidence": confidence,
                    "rule": f"explicit_{role}_cue_{side}",
                }
            )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--structured-dir", type=Path, required=True)
    parser.add_argument("--example-blocks", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    if args.output_dir.exists() or Path(str(args.output_dir) + ".partial").exists():
        raise FileExistsError(f"Refusing to overwrite output: {args.output_dir}")
    partial = Path(str(args.output_dir) + ".partial")
    partial.mkdir(parents=True)

    source_text: dict[str, str] = {}
    for path in args.example_blocks:
        for row in read_jsonl(path):
            source_text[row["text_sha256"]] = row["text"]

    examples = list(read_jsonl(args.structured_dir / "structured_examples.jsonl"))
    examples_by_id = {row["example_id"]: row for row in examples}
    mentions_by_example: dict[str, list[dict]] = defaultdict(list)
    for row in read_jsonl(args.structured_dir / "entity_mentions.jsonl"):
        mentions_by_example[row["example_id"]].append(row)
    measurements_by_example: dict[str, list[dict]] = defaultdict(list)
    for row in read_jsonl(args.structured_dir / "measurements.jsonl"):
        measurements_by_example[row["example_id"]].append(row)
    outcomes_by_example: dict[str, list[dict]] = defaultdict(list)
    for row in read_jsonl(args.structured_dir / "reported_outcomes.jsonl"):
        outcomes_by_example[row["example_id"]].append(row)

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    try:
        compounds, drug_compounds = load_structure_maps(db)
    finally:
        db.close()

    participant_rows: list[dict] = []
    reaction_rows: list[dict] = []
    resolution_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()

    for example in examples:
        example_id = example["example_id"]
        text = source_text.get(example["evidence_text_sha256"])
        if text is None:
            raise ValueError(f"Missing source text for {example_id}")
        selected_roles = []
        for mention in mentions_by_example.get(example_id, []):
            if text[mention["char_start"] : mention["char_end"]] != mention["surface_text"]:
                raise ValueError(f"Offset mismatch for {mention['mention_id']}")
            resolution = resolve_mention(
                mention, example["compound_id"], compounds, drug_compounds
            )
            roles = role_candidates(text, mention["char_start"], mention["char_end"])
            selected_role = None
            if resolution["resolution_level"] == "exact_structure" and len(roles) == 1:
                selected_role = roles[0]["role"]
                selected_roles.append(selected_role)
                role_counts[selected_role] += 1
            resolution_counts[resolution["resolution_level"]] += 1
            participant_rows.append(
                {
                    "participant_candidate_id": stable_id(
                        "participant-candidate", mention["mention_id"], selected_role or "unresolved"
                    ),
                    "reaction_candidate_id": stable_id("reaction-candidate", example_id),
                    "example_id": example_id,
                    "mention_id": mention["mention_id"],
                    "publication_number": example["publication_number"],
                    "source_publication_number": example["source_publication_number"],
                    "char_start": mention["char_start"],
                    "char_end": mention["char_end"],
                    "surface_text": mention["surface_text"],
                    **resolution,
                    "role_candidates": roles,
                    "selected_role": selected_role,
                    "review_status": "unreviewed",
                    "human_review_required": True,
                    "evidence_text_sha256": example["evidence_text_sha256"],
                }
            )

        distinct_roles = set(selected_roles)
        if "produced" in distinct_roles and "consumed" in distinct_roles:
            status = "participant_roles_bidirectional_candidate"
        elif selected_roles:
            status = "participant_roles_partial"
        else:
            status = "evidence_only"
        reaction_rows.append(
            {
                "reaction_candidate_id": stable_id("reaction-candidate", example_id),
                "example_id": example_id,
                "drug_id": example["drug_id"],
                "target_compound_id": example["compound_id"],
                "publication_number": example["publication_number"],
                "source_publication_number": example["source_publication_number"],
                "heading": example["heading"],
                "candidate_status": status,
                "participant_mention_count": len(mentions_by_example.get(example_id, [])),
                "selected_participant_role_count": len(selected_roles),
                "measurement_count": len(measurements_by_example.get(example_id, [])),
                "reported_outcome_count": len(outcomes_by_example.get(example_id, [])),
                "evidence_text_sha256": example["evidence_text_sha256"],
                "review_status": "unreviewed",
                "human_review_required": True,
                "creates_curated_reaction": False,
            }
        )

    write_jsonl(partial / "reaction_candidates.jsonl", reaction_rows)
    write_jsonl(partial / "participant_candidates.jsonl", participant_rows)
    files = []
    for name in ("reaction_candidates.jsonl", "participant_candidates.jsonl"):
        path = partial / name
        files.append(
            {"file": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    status_counts = Counter(row["candidate_status"] for row in reaction_rows)
    manifest = {
        "dataset": "RXN2 EPO unreviewed reaction candidates",
        "extractor_version": "epo-reaction-candidate-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "succeeded",
        "review_status": "unreviewed",
        "human_review_required": True,
        "counts": {
            "reaction_candidates": len(reaction_rows),
            "participant_candidates": len(participant_rows),
            "candidate_status": dict(sorted(status_counts.items())),
            "mention_resolution": dict(sorted(resolution_counts.items())),
            "selected_roles": dict(sorted(role_counts.items())),
        },
        "inputs": {
            "structured_manifest_sha256": sha256_file(args.structured_dir / "manifest.json"),
            "example_blocks": [
                {"file": str(path), "sha256": sha256_file(path)}
                for path in args.example_blocks
            ],
        },
        "files": files,
        "safety": {
            "creates_database_reactions": False,
            "creates_routes": False,
            "accepts_chemistry": False,
            "role_requires_explicit_adjacent_cue": True,
            "role_requires_exact_structure_resolution": True,
            "connectivity_only_mentions_get_roles": False,
        },
    }
    (partial / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(partial, args.output_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
