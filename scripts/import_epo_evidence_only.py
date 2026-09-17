"""Atomically import deterministic native-EPO example blocks as unreviewed evidence.

This deliberately creates only evidence_span rows. It never creates compounds,
relations, reactions, steps, routes, or accepted chemistry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/curated/rxn2-production.sqlite"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({row["example_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate example ID in input")
    return rows


def import_rows(db: sqlite3.Connection, rows: list[dict], input_root: Path) -> dict:
    now = datetime.now(UTC).isoformat()
    imported = skipped = 0
    with db:
        if not db.execute("SELECT 1 FROM source WHERE source_id='epo_ops'").fetchone():
            raise ValueError("epo_ops source is not registered")
        for row in rows:
            if row.get("schema_version") != "rxn2-epo-example-block-v1":
                raise ValueError(f"unexpected block schema: {row.get('example_id')}")
            text = row["text"]
            if hashlib.sha256(text.encode()).hexdigest() != row["text_sha256"]:
                raise ValueError(f"text hash mismatch: {row['example_id']}")
            relative = Path(row["source_artifact"])
            artifact = input_root / relative.name if relative.name == "description.xml" else input_root / relative
            if not artifact.is_file():
                # Extractor records a repo-relative path; derive the document path by publication.
                artifact = input_root / row["publication_number"] / "description.xml"
            if not artifact.is_file() or sha256(artifact) != row["source_artifact_sha256"]:
                raise ValueError(f"source artifact mismatch: {row['example_id']}")
            if not db.execute("SELECT 1 FROM patent_document WHERE publication_number=?", (row["publication_number"],)).fetchone():
                raise ValueError(f"unknown publication: {row['publication_number']}")
            payload = (
                row["example_id"], row["publication_number"], "epo_ops", row["source_artifact_sha256"],
                "experimental_example", ":".join(str(x) for x in row.get("paragraph_labels", [])) or row["heading"],
                0, len(text), text, row["text_sha256"], "performed", "deterministic_native_xml",
                "rxn2-epo-example-block-v1", "needs_review", None, now, "EPO-OPS-terms", "derived_products_only_under_terms",
            )
            existing = db.execute("SELECT publication_number,text_sha256,artifact_sha256 FROM evidence_span WHERE evidence_span_id=?", (row["example_id"],)).fetchone()
            if existing:
                if tuple(existing) != (row["publication_number"], row["text_sha256"], row["source_artifact_sha256"]):
                    raise ValueError(f"conflicting existing evidence: {row['example_id']}")
                skipped += 1
                continue
            db.execute(
                """INSERT INTO evidence_span
                   (evidence_span_id,publication_number,source_id,artifact_sha256,section_type,paragraph_id,
                    char_start,char_end,evidence_text,text_sha256,evidence_status,extraction_method,
                    extractor_version,review_status,source_url,retrieved_at,license_code,redistribution_class)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload,
            )
            imported += 1
    return {"input_rows": len(rows), "imported": imported, "skipped_existing": skipped,
            "creates_reactions": False, "accepts_chemistry": False, "review_status": "needs_review"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    result = import_rows(sqlite3.connect(args.db), load_rows(args.input), args.artifact_root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
