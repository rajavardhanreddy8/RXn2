#!/usr/bin/env python3
"""Import verified EPO OPS family snapshots into the RXN2 evidence database."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

try:
    from scripts.bulk_pipeline import (
        DEFAULT_DB,
        DEFAULT_SCHEMA,
        ROOT,
        connect,
        json_text,
        now,
        record_ingestion_run,
        register_release,
        register_sources,
        sha256_file,
    )
except ModuleNotFoundError:
    from bulk_pipeline import (
        DEFAULT_DB,
        DEFAULT_SCHEMA,
        ROOT,
        connect,
        json_text,
        now,
        record_ingestion_run,
        register_release,
        register_sources,
        sha256_file,
    )


PARSER_VERSION = "epo-ops-family-xml-v1"
NS = {"ops": "http://ops.epo.org", "ex": "http://www.epo.org/exchange"}


def child_text(node: ET.Element, name: str) -> str | None:
    child = node.find(f"ex:{name}", NS)
    return child.text.strip() if child is not None and child.text else None


def publication_id(document: ET.Element) -> str | None:
    country = child_text(document, "country")
    number = child_text(document, "doc-number")
    kind = child_text(document, "kind")
    return f"{country}-{number}-{kind}" if country and number and kind else None


def parse_family(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    members: dict[str, dict] = {}
    for document in root.findall(".//ex:publication-reference/ex:document-id[@document-id-type='docdb']", NS):
        publication = publication_id(document)
        if not publication:
            continue
        date = child_text(document, "date")
        if date and len(date) == 8 and date.isdigit():
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        members[publication] = {
            "publication_number": publication,
            "country_code": publication[:2],
            "kind_code": publication.rsplit("-", 1)[-1],
            "publication_date": date,
        }
    if not members:
        raise ValueError(f"no DOCDB publication members in {path}")
    return [members[key] for key in sorted(members)]


def verify_snapshot(directory: Path) -> tuple[dict, list[Path]]:
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "succeeded":
        raise ValueError(f"incomplete EPO OPS snapshot: {directory.name}")
    artifacts = []
    for item in manifest.get("artifacts", []):
        path = directory / item["file"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ValueError(f"EPO OPS artifact checksum mismatch: {path}")
        artifacts.append(path)
    if directory / "family.xml" not in artifacts:
        raise ValueError(f"family.xml is not registered in {manifest_path}")
    return manifest, [manifest_path, *artifacts]


def import_epo_families(db: sqlite3.Connection, input_root: Path) -> dict:
    started_at = now()
    snapshots = []
    files: list[Path] = []
    for directory in sorted(path for path in input_root.iterdir() if path.is_dir()):
        manifest, verified = verify_snapshot(directory)
        snapshots.append((directory, manifest, parse_family(directory / "family.xml")))
        files.extend(verified)
    if not snapshots:
        raise ValueError(f"no EPO OPS snapshots found in {input_root}")

    release_dates = [str(item[1].get("downloaded_at", ""))[:10] for item in snapshots]
    release = max(value for value in release_dates if value)
    counts: Counter = Counter(input_rows=len(snapshots), accepted_rows=len(snapshots))
    with db:
        register_sources(db, ROOT / "configs" / "sources.json")
        release_id, _ = register_release(db, "epo_ops", release, files, PARSER_VERSION)
        for directory, manifest, members in snapshots:
            signature = "\n".join(member["publication_number"] for member in members)
            family_id = f"epo-ops:{hashlib.sha256(signature.encode()).hexdigest()[:24]}"
            db.execute(
                """INSERT INTO patent_family (family_id, family_type, source_id, confidence)
                   VALUES (?, 'epo_ops_family', 'epo_ops', 0.98)
                   ON CONFLICT(family_id) DO UPDATE SET confidence=excluded.confidence""",
                (family_id,),
            )
            for member in members:
                publication = member["publication_number"]
                existing = db.execute(
                    "SELECT 1 FROM patent_document WHERE publication_number = ?", (publication,)
                ).fetchone()
                db.execute(
                    """INSERT INTO patent_document
                       (publication_number, country_code, kind_code, publication_date, source_id,
                        source_document_id, parser_version, raw_record_json)
                       VALUES (?, ?, ?, ?, 'epo_ops', ?, ?, ?)
                       ON CONFLICT(publication_number) DO UPDATE SET
                         kind_code=COALESCE(patent_document.kind_code, excluded.kind_code),
                         publication_date=COALESCE(patent_document.publication_date, excluded.publication_date)""",
                    (
                        publication,
                        member["country_code"],
                        member["kind_code"],
                        member["publication_date"],
                        publication,
                        PARSER_VERSION,
                        json_text({
                            "queried_publication": manifest.get("publication_number"),
                            "snapshot_manifest": str(directory / "manifest.json"),
                        }),
                    ),
                )
                db.execute(
                    """INSERT OR IGNORE INTO patent_family_member
                       (family_id, publication_number, relationship) VALUES (?, ?, 'member')""",
                    (family_id, publication),
                )
                counts["family_members"] += 1
                if not existing:
                    counts["patent_documents_created"] += 1
            counts["families"] += 1
        record_ingestion_run(db, release_id, "epo_ops", PARSER_VERSION, dict(counts), started_at)
    return {**dict(counts), "release_id": release_id, "parser_version": PARSER_VERSION}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    db = connect(args.db.resolve(), args.schema.resolve())
    try:
        print(json.dumps(import_epo_families(db, args.input.resolve()), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error, ET.ParseError) as error:
        print(f"ERROR: {error}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
