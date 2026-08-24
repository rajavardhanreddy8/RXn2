#!/usr/bin/env python3
"""Bulk catalogue, SureChEMBL candidate, and coverage pipeline.

All inputs are operator-provided immutable snapshots. The serving API never
downloads third-party data at runtime.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator

try:
    from scripts.hybrid_storage import (
        DEFAULT_POLICY as DEFAULT_STORAGE_POLICY,
        StoragePolicy,
        is_relative_to,
        stage_file,
        stage_snapshot,
    )
except ModuleNotFoundError:
    from hybrid_storage import (
        DEFAULT_POLICY as DEFAULT_STORAGE_POLICY,
        StoragePolicy,
        is_relative_to,
        stage_file,
        stage_snapshot,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "curated" / "rxn2-production.sqlite"
DEFAULT_SCHEMA = ROOT / "sql" / "schema.sql"
DEFAULT_SOURCES = ROOT / "configs" / "sources.json"
SMALL_MOLECULE = "small_molecule"
CHEMBL_CHEMICAL_MODALITIES = {
    "small molecule": SMALL_MOLECULE,
    "inorganic small molecule": "inorganic_small_molecule",
    "polymeric small molecule": "polymeric_small_molecule",
    "oligosaccharide": "oligosaccharide",
    "oligonucleotide": "oligonucleotide",
}
CHEMICAL_MODALITIES = frozenset(CHEMBL_CHEMICAL_MODALITIES.values())
MATERIAL_FORMS = {
    "active_moiety",
    "salt",
    "solvate",
    "stereoisomer",
    "salt_or_form",
    "unknown",
}
SURECHEMBL_FILES = (
    "compounds.parquet",
    "patent_compound_map.parquet",
    "patents.parquet",
    "fields.parquet",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    value = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def normalize_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def clean(value: object | None) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@contextmanager
def atomic(db: sqlite3.Connection, name: str):
    db.execute(f"SAVEPOINT {name}")
    try:
        yield
    except Exception:
        db.execute(f"ROLLBACK TO {name}")
        db.execute(f"RELEASE {name}")
        raise
    else:
        db.execute(f"RELEASE {name}")


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def connect(path: Path, schema: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(schema.read_text(encoding="utf-8"))
    columns = {row["name"] for row in db.execute("PRAGMA table_info(regulatory_product)")}
    if "marketing_status" not in columns:
        db.execute(
            "ALTER TABLE regulatory_product "
            "ADD COLUMN marketing_status TEXT NOT NULL DEFAULT 'unknown'"
        )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_regulatory_product_status "
        "ON regulatory_product(marketing_status, jurisdiction)"
    )
    return db


def register_sources(db: sqlite3.Connection, registry_path: Path) -> dict[str, dict]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    sources = {source["id"]: source for source in registry["sources"]}
    for source in sources.values():
        db.execute(
            """INSERT INTO source
            (source_id, name, authority, role, collection_mode, runtime_dependency,
             automated_acquisition_allowed, redistribution, license_code, homepage, registry_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              name=excluded.name, authority=excluded.authority, role=excluded.role,
              collection_mode=excluded.collection_mode,
              automated_acquisition_allowed=excluded.automated_acquisition_allowed,
              redistribution=excluded.redistribution, license_code=excluded.license_code,
              homepage=excluded.homepage, registry_json=excluded.registry_json""",
            (
                source["id"], source["name"], source["authority"], source["role"],
                source["collection_mode"], int(source.get("runtime_dependency", False)),
                int(source.get("automated_acquisition_allowed", False)),
                source["redistribution"], source["license"], source.get("homepage"),
                json_text(source),
            ),
        )
    db.commit()
    return sources


def register_release(
    db: sqlite3.Connection,
    source_id: str,
    release: str,
    files: Iterable[Path],
    parser_version: str,
) -> tuple[str, dict[str, str]]:
    release_id = f"{source_id}:{release}"
    db.execute(
        """INSERT INTO source_release
        (release_id, source_id, released_on, acquired_at, parser_version, schema_version, notes)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          acquired_at=excluded.acquired_at, parser_version=excluded.parser_version""",
        (release_id, source_id, release, now(), parser_version, "Operator-provided immutable bulk snapshot"),
    )
    artifacts: dict[str, str] = {}
    for path in files:
        absolute = path.resolve()
        relative = absolute.relative_to(ROOT).as_posix() if ROOT in absolute.parents else str(absolute)
        size_bytes = absolute.stat().st_size
        checksum = sha256_file(absolute)
        existing = db.execute(
            """SELECT a.artifact_id, a.sha256, a.size_bytes FROM artifact a
               WHERE a.release_id = ? AND a.relative_path = ?""",
            (release_id, relative),
        ).fetchone()
        if existing:
            if existing["size_bytes"] != size_bytes or existing["sha256"] != checksum:
                raise RuntimeError(
                    f"immutable release artifact changed for {source_id}:{release}: {relative}"
                )
            artifacts[path.name.casefold()] = existing["artifact_id"]
            continue
        artifact_id = f"{release_id}:{checksum[:16]}"
        media_type = "application/vnd.apache.parquet" if absolute.suffix.casefold() == ".parquet" else "application/octet-stream"
        db.execute(
            """INSERT INTO artifact
            (artifact_id, release_id, relative_path, sha256, size_bytes, media_type)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET relative_path=excluded.relative_path""",
            (artifact_id, release_id, relative, checksum, size_bytes, media_type),
        )
        artifacts[path.name.casefold()] = artifact_id
    return release_id, artifacts


def record_ingestion_run(
    db: sqlite3.Connection,
    release_id: str,
    source_id: str,
    parser_version: str,
    counts: dict[str, object],
    started_at: str,
) -> None:
    input_rows = int(counts.get("input_rows", counts.get("accepted_rows", 0)))
    accepted_rows = int(counts.get("accepted_rows", input_rows))
    excluded_rows = int(counts.get("excluded_rows", 0))
    rejected_rows = int(counts.get("rejected_rows", 0))
    if input_rows != accepted_rows + excluded_rows + rejected_rows:
        raise RuntimeError(
            f"ingestion accounting mismatch for {release_id}: input={input_rows}, "
            f"accepted={accepted_rows}, excluded={excluded_rows}, rejected={rejected_rows}"
        )
    reason_counts = counts.get("reason_counts", {})
    run_id = stable_id("ingestion-run", release_id, parser_version)
    completed_at = now()
    db.execute(
        """INSERT INTO ingestion_run
        (ingestion_run_id, release_id, source_id, parser_version, started_at,
         completed_at, status, input_rows, accepted_rows, excluded_rows,
         rejected_rows, reason_counts_json, details_json)
        VALUES (?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(release_id, parser_version) DO UPDATE SET
          started_at=excluded.started_at, completed_at=excluded.completed_at,
          status=excluded.status, input_rows=excluded.input_rows,
          accepted_rows=excluded.accepted_rows, excluded_rows=excluded.excluded_rows,
          rejected_rows=excluded.rejected_rows,
          reason_counts_json=excluded.reason_counts_json,
          details_json=excluded.details_json""",
        (
            run_id, release_id, source_id, parser_version, started_at, completed_at,
            input_rows, accepted_rows, excluded_rows, rejected_rows,
            json_text(reason_counts), json_text(counts),
        ),
    )


def named_drug_ids(db: sqlite3.Connection, preferred_name: str) -> set[str]:
    normalized = normalize_name(preferred_name)
    return {
        row["drug_id"]
        for row in db.execute(
            "SELECT DISTINCT drug_id FROM drug_alias WHERE normalized_alias = ?",
            (normalized,),
        )
    }


def queue_name_link_candidates(
    db: sqlite3.Connection,
    subject_id: str,
    preferred_name: str,
    candidate_drug_ids: Iterable[str],
    source_id: str,
) -> int:
    count = 0
    normalized = normalize_name(preferred_name)
    for object_id in sorted(set(candidate_drug_ids)):
        candidate_id = stable_id(
            "link-candidate", "drug_entity", subject_id, object_id, "same_active_moiety"
        )
        db.execute(
            """INSERT OR IGNORE INTO link_candidate
            (candidate_id, subject_type, subject_id, object_type, object_id,
             relationship_type, score, method, model_version, features_json, created_at)
            VALUES (?, 'drug_entity', ?, 'drug_entity', ?, 'same_active_moiety',
                    0.7, 'normalized_name', 'name-review-v1', ?, ?)""",
            (
                candidate_id, subject_id, object_id,
                json_text({"normalized_name": normalized, "source_id": source_id}),
                now(),
            ),
        )
        count += db.execute("SELECT changes()").fetchone()[0]
    return count


def queue_source_record_candidates(
    db: sqlite3.Connection,
    source_id: str,
    release: str,
    preferred_name: str,
    candidate_drug_ids: Iterable[str],
) -> int:
    normalized = normalize_name(preferred_name)
    subject_id = stable_id("source-record", source_id, release, normalized)
    count = 0
    for object_id in sorted(set(candidate_drug_ids)):
        candidate_id = stable_id(
            "link-candidate", "source_record", subject_id, object_id,
            "possible_drug_identity",
        )
        db.execute(
            """INSERT OR IGNORE INTO link_candidate
            (candidate_id, subject_type, subject_id, object_type, object_id,
             relationship_type, score, method, model_version, features_json, created_at)
            VALUES (?, 'source_record', ?, 'drug_entity', ?, 'possible_drug_identity',
                    0.7, 'normalized_name', 'name-review-v1', ?, ?)""",
            (
                candidate_id, subject_id, object_id,
                json_text({
                    "normalized_name": normalized,
                    "preferred_name": preferred_name,
                    "release": release,
                    "source_id": source_id,
                }),
                now(),
            ),
        )
        count += db.execute("SELECT changes()").fetchone()[0]
    return count


def find_or_create_drug(
    db: sqlite3.Connection,
    preferred_name: str,
    source_id: str,
    identity_key: str | None = None,
    modality: str = SMALL_MOLECULE,
) -> str:
    normalized = normalize_name(preferred_name)
    if not normalized:
        raise ValueError("drug name cannot be empty")
    drug_id = stable_id("drug", source_id, identity_key or normalized)
    matches = named_drug_ids(db, preferred_name) - {drug_id}
    db.execute(
        """INSERT INTO drug_entity (drug_id, preferred_name, modality, review_status)
           VALUES (?, ?, ?, 'unreviewed')
           ON CONFLICT(drug_id) DO UPDATE SET
             preferred_name=COALESCE(drug_entity.preferred_name, excluded.preferred_name)""",
        (drug_id, preferred_name.strip(), modality),
    )
    add_alias(db, drug_id, preferred_name, "preferred_name", source_id)
    queue_name_link_candidates(db, drug_id, preferred_name, matches, source_id)
    return drug_id


def matched_drug_id(
    db: sqlite3.Connection,
    inchi_key: str | None,
    identifiers: dict[str, object],
) -> str | None:
    matches: set[str] = set()
    if inchi_key:
        matches.update(
            row["drug_id"]
            for row in db.execute(
                """SELECT DISTINCT dc.drug_id
                   FROM drug_compound dc JOIN compound c USING (compound_id)
                   WHERE c.inchi_key = ?""",
                (inchi_key,),
            )
        )
    for namespace, raw_values in identifiers.items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        for value in values:
            identifier = clean(value)
            if not identifier:
                continue
            matches.update(
                row["drug_id"]
                for row in db.execute(
                    """SELECT drug_id FROM drug_identifier
                       WHERE namespace = ? AND identifier_value = ?""",
                    (namespace, identifier),
                )
            )
    if len(matches) > 1:
        raise ValueError(
            "cross-source identity conflict: exact structure/identifier matches multiple drugs"
        )
    return next(iter(matches), None)


def attach_or_create_drug(
    db: sqlite3.Connection,
    preferred_name: str,
    source_id: str,
    matched_id: str | None,
) -> str:
    if not matched_id:
        return find_or_create_drug(db, preferred_name, source_id)
    db.execute(
        """UPDATE drug_entity
           SET preferred_name = COALESCE(preferred_name, ?)
           WHERE drug_id = ?""",
        (preferred_name.strip(), matched_id),
    )
    add_alias(db, matched_id, preferred_name, "preferred_name", source_id)
    return matched_id


def require_compatible_name_match(
    db: sqlite3.Connection,
    preferred_name: str,
    inchi_key: str | None,
    matched_id: str | None,
) -> None:
    """Reject name-only merges when the catalogue already has another exact structure."""
    if matched_id or not inchi_key:
        return
    existing_keys = {
        row["inchi_key"]
        for row in db.execute(
            """SELECT DISTINCT c.inchi_key
               FROM drug_alias da
               JOIN drug_compound dc USING (drug_id)
               JOIN compound c USING (compound_id)
               WHERE da.normalized_alias = ? AND c.inchi_key IS NOT NULL""",
            (normalize_name(preferred_name),),
        )
    }
    if existing_keys and inchi_key not in existing_keys:
        raise ValueError(
            "name-only match conflicts with an existing exact structure; manual reconciliation required"
        )


def add_alias(db: sqlite3.Connection, drug_id: str, alias: str | None, alias_type: str, source_id: str) -> None:
    normalized = normalize_name(alias)
    if not normalized:
        return
    db.execute(
        """INSERT OR IGNORE INTO drug_alias
        (drug_id, alias, normalized_alias, alias_type, source_id) VALUES (?, ?, ?, ?, ?)""",
        (drug_id, str(alias).strip(), normalized, alias_type, source_id),
    )


def add_identifier(db: sqlite3.Connection, drug_id: str, namespace: str, value: object, source_id: str) -> None:
    identifier = clean(value)
    if identifier:
        db.execute(
            """INSERT OR IGNORE INTO drug_identifier
            (drug_id, namespace, identifier_value, source_id) VALUES (?, ?, ?, ?)""",
            (drug_id, namespace, identifier, source_id),
        )


def zip_rows(path: Path, member_suffix: str, delimiter: str) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.casefold().endswith(member_suffix.casefold())]
        if not members:
            raise ValueError(f"{path} does not contain {member_suffix}")
        with archive.open(sorted(members, key=len)[0]) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as text:
                yield from csv.DictReader(text, delimiter=delimiter)


def optional_zip_rows(
    path: Path, member_suffix: str, delimiter: str
) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        members = [
            name for name in archive.namelist()
            if name.casefold().endswith(member_suffix.casefold())
        ]
        if not members:
            return
        with archive.open(sorted(members, key=len)[0]) as raw:
            with io.TextIOWrapper(
                raw, encoding="utf-8-sig", errors="replace", newline=""
            ) as text:
                yield from csv.DictReader(text, delimiter=delimiter)


def value_from(row: dict[str, str], *names: str) -> str | None:
    normalized = {normalize_name(key): value for key, value in row.items()}
    for name in names:
        value = clean(normalized.get(normalize_name(name)))
        if value:
            return value
    return None


def split_ingredients(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def fda_marketing_status(
    row: dict[str, str], lookup: dict[str, str], assigned_status_id: str | None = None
) -> str:
    raw_status = value_from(
        row, "Type", "MarketingStatusDescription", "Marketing Status"
    )
    status_id = assigned_status_id or value_from(
        row, "MarketingStatusID", "Marketing_Status_ID"
    )
    description = lookup.get(status_id or "", raw_status or "")
    normalized = normalize_name(description)
    if "withdraw" in normalized:
        return "withdrawn"
    if "discont" in normalized or normalized == "discn":
        return "discontinued"
    if "tentative" in normalized:
        return "tentative"
    if normalized in {"rx", "otc"} or "prescription" in normalized or "over the counter" in normalized:
        return "active"
    return "unknown"


def ingest_fda_products(
    db: sqlite3.Connection,
    path: Path,
    source_id: str,
    delimiter: str,
) -> dict[str, int]:
    lookup = {}
    for item in optional_zip_rows(path, "MarketingStatus_Lookup.txt", delimiter):
        status_id = value_from(item, "MarketingStatusID", "Marketing_Status_ID")
        description = value_from(
            item, "MarketingStatusDescription", "Marketing_Status_Description"
        )
        if status_id and description:
            lookup[status_id] = description
    assigned_statuses: dict[tuple[str, str], str] = {}
    for item in optional_zip_rows(path, "MarketingStatus.txt", delimiter):
        application = value_from(item, "ApplNo", "Appl_No")
        product_number = value_from(item, "ProductNo", "Product_No")
        status_id = value_from(item, "MarketingStatusID", "Marketing_Status_ID")
        if application and product_number and status_id:
            assigned_statuses[
                (application.zfill(6), product_number.zfill(3))
            ] = status_id
    reasons: Counter[str] = Counter()
    counts: dict[str, object] = {
        "input_rows": 0,
        "accepted_rows": 0,
        "excluded_rows": 0,
        "rejected_rows": 0,
        "products": 0,
        "drug_links": 0,
    }
    for row in zip_rows(path, "products.txt", delimiter):
        counts["input_rows"] += 1
        application = value_from(row, "ApplNo", "Appl_No")
        product_number = value_from(row, "ProductNo", "Product_No")
        ingredients = split_ingredients(value_from(row, "ActiveIngredient", "Ingredient"))
        missing = [
            name for name, item in (
                ("application_number", application),
                ("product_number", product_number),
                ("active_ingredient", ingredients),
            )
            if not item
        ]
        if missing:
            counts["rejected_rows"] += 1
            reasons["missing_" + "_and_".join(missing)] += 1
            continue
        trade_name = value_from(row, "DrugName", "Trade_Name", "Trade Name")
        form_route = value_from(row, "Form", "DF;Route", "Dosage form; Route of Administration")
        dosage_form, _, route = (form_route or "").partition(";")
        normalized_application = application.zfill(6)
        normalized_product = product_number.zfill(3)
        product_id = (
            f"fda:{source_id}:{normalized_application}:{normalized_product}"
        )
        marketing_status = fda_marketing_status(
            row, lookup,
            assigned_statuses.get((normalized_application, normalized_product)),
        )
        db.execute(
            """INSERT INTO regulatory_product
            (regulatory_product_id, jurisdiction, application_number, product_number,
             trade_name, dosage_form, route, strength, approval_date, marketing_status, applicant,
             source_id, raw_record_json)
            VALUES (?, 'US-FDA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(regulatory_product_id) DO UPDATE SET
              trade_name=excluded.trade_name, dosage_form=excluded.dosage_form,
              route=excluded.route, strength=excluded.strength,
              approval_date=COALESCE(excluded.approval_date, regulatory_product.approval_date),
              marketing_status=excluded.marketing_status,
              raw_record_json=excluded.raw_record_json""",
            (
                product_id, normalized_application, normalized_product, trade_name,
                clean(dosage_form), clean(route), value_from(row, "Strength"),
                value_from(row, "Approval_Date", "Approval Date"),
                marketing_status,
                value_from(row, "SponsorName", "Applicant_Full_Name", "Applicant"),
                source_id, json_text(row),
            ),
        )
        counts["products"] += 1
        for ingredient in ingredients:
            drug_id = find_or_create_drug(db, ingredient, source_id)
            add_alias(db, drug_id, ingredient, "active_ingredient", source_id)
            add_alias(db, drug_id, trade_name, "brand_name", source_id)
            add_identifier(db, drug_id, "FDA_APPLICATION", application.zfill(6), source_id)
            db.execute(
                """INSERT OR IGNORE INTO regulatory_product_drug
                (regulatory_product_id, drug_id, relationship_type) VALUES (?, ?, 'active_ingredient')""",
                (product_id, drug_id),
            )
            counts["drug_links"] += 1
        counts["accepted_rows"] += 1
    if counts["input_rows"] == 0:
        raise ValueError(f"{path} contains no FDA product rows")
    if counts["accepted_rows"] == 0:
        raise ValueError(f"{path} contains no valid FDA product rows")
    counts["reason_counts"] = dict(sorted(reasons.items()))
    return counts


def ingest_fda(
    db: sqlite3.Connection,
    drugs_fda: Path | None,
    orange_book: Path | None,
    release: str,
    artifact_drugs_fda: Path | None = None,
    artifact_orange_book: Path | None = None,
) -> dict:
    results: dict[str, object] = {"release": release}
    with atomic(db, "fda_release"):
        if drugs_fda:
            started_at = now()
            release_id, _ = register_release(
                db, "drugs_at_fda", release, [artifact_drugs_fda or drugs_fda],
                "fda-products-v2",
            )
            counts = ingest_fda_products(db, drugs_fda, "drugs_at_fda", "\t")
            record_ingestion_run(
                db, release_id, "drugs_at_fda", "fda-products-v2", counts, started_at
            )
            results["drugs_at_fda"] = counts
        if orange_book:
            started_at = now()
            release_id, _ = register_release(
                db, "fda_orange_book", release,
                [artifact_orange_book or orange_book], "orange-book-products-v2",
            )
            counts = ingest_fda_products(
                db, orange_book, "fda_orange_book", "~"
            )
            record_ingestion_run(
                db, release_id, "fda_orange_book", "orange-book-products-v2",
                counts, started_at,
            )
            results["orange_book"] = counts
    db.commit()
    return results


def chembl_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def ingest_chembl(
    db: sqlite3.Connection,
    chembl_path: Path,
    release: str,
    artifact_path: Path | None = None,
) -> dict[str, int]:
    started_at = now()
    source = sqlite3.connect(f"file:{chembl_path.resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    required = {"molecule_dictionary", "compound_structures", "molecule_hierarchy", "molecule_synonyms"}
    present = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = required - present
    if missing:
        raise ValueError(f"ChEMBL SQLite is missing tables: {', '.join(sorted(missing))}")
    counts = {"drugs": 0, "compounds": 0, "aliases": 0}
    query = """
        SELECT md.molregno, md.chembl_id, md.pref_name, md.max_phase,
               md.molecule_type, md.first_approval,
               cs.canonical_smiles, cs.standard_inchi, cs.standard_inchi_key,
               COALESCE(mh.parent_molregno, md.molregno) AS parent_molregno,
               COALESCE(parent.chembl_id, md.chembl_id) AS parent_chembl_id,
               COALESCE(parent.pref_name, md.pref_name, md.chembl_id) AS parent_name
        FROM molecule_dictionary md
        LEFT JOIN compound_structures cs ON cs.molregno = md.molregno
        LEFT JOIN molecule_hierarchy mh ON mh.molregno = md.molregno
        LEFT JOIN molecule_dictionary parent ON parent.molregno = COALESCE(mh.parent_molregno, md.molregno)
        WHERE md.max_phase = 4 AND lower(md.molecule_type) IN ({})
        ORDER BY md.molregno
    """.format(",".join("?" for _ in CHEMBL_CHEMICAL_MODALITIES))
    with atomic(db, "chembl_release"):
        release_id, _ = register_release(
            db, "chembl_snapshot", release, [artifact_path or chembl_path],
            "chembl-sqlite-v2",
        )
        rows = source.execute(query, tuple(CHEMBL_CHEMICAL_MODALITIES))
        molregno_to_drug: dict[int, str] = {}
        for row in rows:
            preferred = row["parent_name"] or row["pref_name"] or row["chembl_id"]
            drug_id = find_or_create_drug(
                db,
                preferred,
                "chembl_snapshot",
                modality=CHEMBL_CHEMICAL_MODALITIES[row["molecule_type"].casefold()],
            )
            molregno_to_drug[int(row["molregno"])] = drug_id
            active_moiety_id = f"chembl-moiety:{row['parent_chembl_id']}"
            db.execute(
            """INSERT INTO active_moiety
            (active_moiety_id, preferred_name, structure_key, structure_source, review_status)
            VALUES (?, ?, ?, 'chembl_snapshot', 'unreviewed')
            ON CONFLICT(active_moiety_id) DO UPDATE SET
              preferred_name=COALESCE(excluded.preferred_name, active_moiety.preferred_name),
              structure_key=COALESCE(excluded.structure_key, active_moiety.structure_key)""",
                (active_moiety_id, preferred, clean(row["standard_inchi_key"])),
            )
            db.execute(
                "UPDATE drug_entity SET active_moiety_id = ? WHERE drug_id = ?",
                (active_moiety_id, drug_id),
            )
            inchi_key = clean(row["standard_inchi_key"])
            db.execute(
            """INSERT INTO compound
            (compound_id, preferred_name, smiles, inchi, inchi_key, connectivity_key,
             active_moiety_id, material_form, source_id, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'chembl_snapshot', 'unreviewed')
            ON CONFLICT(compound_id) DO UPDATE SET
              preferred_name=COALESCE(excluded.preferred_name, compound.preferred_name),
              smiles=COALESCE(excluded.smiles, compound.smiles),
              inchi=COALESCE(excluded.inchi, compound.inchi),
              inchi_key=COALESCE(excluded.inchi_key, compound.inchi_key),
              connectivity_key=COALESCE(excluded.connectivity_key, compound.connectivity_key)""",
                (
                row["chembl_id"], row["pref_name"] or preferred, clean(row["canonical_smiles"]),
                clean(row["standard_inchi"]), inchi_key, inchi_key[:14] if inchi_key else None,
                active_moiety_id, "active_moiety" if row["chembl_id"] == row["parent_chembl_id"] else "salt_or_form",
                ),
            )
            db.execute(
            """INSERT OR IGNORE INTO drug_compound
            (drug_id, compound_id, relationship_type, review_status) VALUES (?, ?, ?, 'unreviewed')""",
                (
                    drug_id, row["chembl_id"],
                    "active_moiety"
                    if row["chembl_id"] == row["parent_chembl_id"]
                    else "salt_or_form",
                ),
            )
            add_identifier(
                db, drug_id, "CHEMBL", row["parent_chembl_id"], "chembl_snapshot"
            )
            add_alias(
                db, drug_id, row["pref_name"], "chembl_preferred_name",
                "chembl_snapshot",
            )
            counts["drugs"] += 1
            counts["compounds"] += 1
        synonym_query = """
        SELECT s.molregno, s.synonyms, s.syn_type
        FROM molecule_synonyms s JOIN molecule_dictionary md USING (molregno)
        WHERE md.max_phase = 4 AND lower(md.molecule_type) IN ({})
    """.format(",".join("?" for _ in CHEMBL_CHEMICAL_MODALITIES))
        for row in source.execute(synonym_query, tuple(CHEMBL_CHEMICAL_MODALITIES)):
            drug_id = molregno_to_drug.get(int(row["molregno"]))
            if drug_id and clean(row["synonyms"]):
                add_alias(
                    db, drug_id, row["synonyms"],
                    clean(row["syn_type"]) or "synonym", "chembl_snapshot",
                )
                counts["aliases"] += 1
        counts.update({
            "input_rows": counts["drugs"],
            "accepted_rows": counts["drugs"],
            "excluded_rows": 0,
            "rejected_rows": 0,
            "reason_counts": {},
        })
        record_ingestion_run(
            db, release_id, "chembl_snapshot", "chembl-sqlite-v2",
            counts, started_at,
        )
    source.close()
    db.commit()
    return counts


def ingest_catalogue_jsonl(
    db: sqlite3.Connection,
    input_path: Path,
    source_id: str,
    release: str,
) -> dict[str, int]:
    """Ingest normalized records prepared from PubChem, UniChem, EMA, or another registry."""
    started_at = now()
    counts = {
        "input_rows": 0,
        "accepted_rows": 0,
        "excluded_rows": 0,
        "rejected_rows": 0,
        "drugs": 0,
        "compounds": 0,
        "aliases": 0,
        "identifiers": 0,
        "regulatory_products": 0,
        "unmatched_existing_drugs": 0,
        "ambiguous_existing_drugs": 0,
        "name_link_candidates": 0,
    }
    reason_counts: Counter[str] = Counter()
    with atomic(db, "catalogue_jsonl"):
        release_id, _ = register_release(
            db, source_id, release, [input_path], "catalogue-jsonl-v2"
        )
        handle = input_path.open(encoding="utf-8")
        try:
            lines = enumerate(handle, 1)
            for line_number, line in lines:
                if not line.strip():
                    continue
                counts["input_rows"] += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"invalid JSON on line {line_number}: {error.msg}") from error
                preferred_name = clean(record.get("preferred_name"))
                if not preferred_name:
                    raise ValueError(f"line {line_number}: preferred_name is required")
                modality = clean(record.get("modality")) or SMALL_MOLECULE
                if modality not in CHEMICAL_MODALITIES:
                    raise ValueError(
                        f"line {line_number}: unsupported chemical modality {modality!r}")
                identifiers = record.get("identifiers", {})
                if not isinstance(identifiers, dict):
                    raise ValueError(f"line {line_number}: identifiers must be an object")
                compound = record.get("compound")
                inchi_key = clean(compound.get("inchi_key")) if isinstance(compound, dict) else None
                if inchi_key and len(inchi_key) != 27:
                    raise ValueError(f"line {line_number}: inchi_key must contain 27 characters")
                matched_id = matched_drug_id(db, inchi_key, identifiers)
                exact_source_identity = any(
                    namespace == "CHEMBL" and clean(values)
                    for namespace, values in identifiers.items()
                )
                if not exact_source_identity:
                    require_compatible_name_match(db, preferred_name, inchi_key, matched_id)
                requires_existing = record.get("requires_existing_drug", False)
                if not isinstance(requires_existing, bool):
                    raise ValueError(f"line {line_number}: requires_existing_drug must be boolean")
                if requires_existing and not matched_id:
                    name_matches = named_drug_ids(db, preferred_name)
                    if not name_matches:
                        counts["unmatched_existing_drugs"] += 1
                        counts["excluded_rows"] += 1
                        reason_counts["unmatched_existing_drug"] += 1
                        continue
                    counts["name_link_candidates"] += queue_source_record_candidates(
                        db, source_id, release, preferred_name, name_matches
                    )
                    if len(name_matches) > 1:
                        counts["ambiguous_existing_drugs"] += 1
                        reason_counts["ambiguous_name_match"] += 1
                    else:
                        counts["unmatched_existing_drugs"] += 1
                        reason_counts["name_only_match_requires_review"] += 1
                    counts["excluded_rows"] += 1
                    continue
                if matched_id:
                    drug_id = attach_or_create_drug(db, preferred_name, source_id, matched_id)
                else:
                    chembl_values = identifiers.get("CHEMBL")
                    chembl_id = (
                        chembl_values[0]
                        if isinstance(chembl_values, list) and chembl_values
                        else chembl_values
                    )
                    drug_id = find_or_create_drug(
                        db,
                        preferred_name,
                        source_id,
                        f"CHEMBL:{clean(chembl_id)}" if clean(chembl_id) else None,
                        modality=modality,
                    )
                counts["drugs"] += 1
                db.execute(
                    "UPDATE drug_entity SET modality = ? WHERE drug_id = ?",
                    (modality, drug_id))
                for alias in record.get("aliases", []):
                    if isinstance(alias, str):
                        add_alias(db, drug_id, alias, "synonym", source_id)
                    else:
                        add_alias(db, drug_id, alias.get("value"), alias.get("type", "synonym"), source_id)
                    counts["aliases"] += 1
                for namespace, values in identifiers.items():
                    for value in values if isinstance(values, list) else [values]:
                        add_identifier(db, drug_id, namespace, value, source_id)
                        counts["identifiers"] += 1
                if compound:
                    material_form = clean(compound.get("material_form")) or "active_moiety"
                    if material_form not in MATERIAL_FORMS:
                        raise ValueError(
                            f"line {line_number}: unsupported material_form {material_form!r}"
                        )
                    compound_id = clean(compound.get("compound_id")) or stable_id(
                        "compound", inchi_key or compound.get("smiles") or preferred_name
                    )
                    active_moiety_id = clean(record.get("active_moiety_id")) or stable_id(
                        "moiety", (inchi_key or normalize_name(preferred_name))[:14]
                    )
                    db.execute(
                        """INSERT OR IGNORE INTO active_moiety
                        (active_moiety_id, preferred_name, structure_key, structure_source, review_status)
                        VALUES (?, ?, ?, ?, 'unreviewed')""",
                        (active_moiety_id, preferred_name, inchi_key[:14] if inchi_key else None, source_id),
                    )
                    db.execute("UPDATE drug_entity SET active_moiety_id = ? WHERE drug_id = ?", (active_moiety_id, drug_id))
                    db.execute(
                        """INSERT INTO compound
                        (compound_id, preferred_name, smiles, inchi, inchi_key, connectivity_key,
                         active_moiety_id, material_form, source_id, review_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed')
                        ON CONFLICT(compound_id) DO UPDATE SET
                          smiles=COALESCE(excluded.smiles, compound.smiles),
                          inchi=COALESCE(excluded.inchi, compound.inchi),
                          inchi_key=COALESCE(excluded.inchi_key, compound.inchi_key),
                          connectivity_key=COALESCE(excluded.connectivity_key, compound.connectivity_key)""",
                        (
                            compound_id, preferred_name, clean(compound.get("smiles")),
                            clean(compound.get("inchi")), inchi_key, inchi_key[:14] if inchi_key else None,
                            active_moiety_id, material_form, source_id,
                        ),
                    )
                    db.execute(
                        """INSERT OR IGNORE INTO drug_compound
                        (drug_id, compound_id, relationship_type, review_status)
                        VALUES (?, ?, ?, 'unreviewed')""",
                        (drug_id, compound_id, clean(compound.get("relationship_type")) or "active_moiety"),
                    )
                    counts["compounds"] += 1
                for product in record.get("regulatory_products", []):
                    if not isinstance(product, dict):
                        raise ValueError(
                            f"line {line_number}: regulatory_products entries must be objects"
                        )
                    jurisdiction = clean(product.get("jurisdiction"))
                    application = clean(product.get("application_number"))
                    product_number = clean(product.get("product_number"))
                    if not jurisdiction or not (application or product_number):
                        raise ValueError(
                            f"line {line_number}: regulatory product requires jurisdiction "
                            "and application_number or product_number"
                        )
                    product_id = clean(product.get("regulatory_product_id")) or stable_id(
                        "regulatory-product", jurisdiction, application, product_number
                    )
                    db.execute(
                        """INSERT INTO regulatory_product
                        (regulatory_product_id, jurisdiction, application_number, product_number,
                         trade_name, dosage_form, route, strength, approval_date, marketing_status,
                         applicant, source_id, raw_record_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(regulatory_product_id) DO UPDATE SET
                          trade_name=COALESCE(excluded.trade_name, regulatory_product.trade_name),
                          approval_date=COALESCE(excluded.approval_date, regulatory_product.approval_date),
                          marketing_status=excluded.marketing_status,
                          raw_record_json=excluded.raw_record_json""",
                        (
                            product_id, jurisdiction, application, product_number,
                            clean(product.get("trade_name")), clean(product.get("dosage_form")),
                            clean(product.get("route")), clean(product.get("strength")),
                            clean(product.get("approval_date")),
                            clean(product.get("marketing_status")) or "unknown",
                            clean(product.get("applicant")), source_id, json_text(product),
                        ),
                    )
                    db.execute(
                        """INSERT OR IGNORE INTO regulatory_product_drug
                        (regulatory_product_id, drug_id, relationship_type)
                        VALUES (?, ?, 'active_ingredient')""",
                        (product_id, drug_id),
                    )
                    counts["regulatory_products"] += 1
                counts["accepted_rows"] += 1
        finally:
            handle.close()
        if counts["input_rows"] == 0:
            raise ValueError(f"{input_path} contains no catalogue records")
        counts["reason_counts"] = dict(sorted(reason_counts.items()))
        record_ingestion_run(
            db, release_id, source_id, "catalogue-jsonl-v2", counts, started_at
        )
    db.commit()
    return counts


def require_snapshot_files(snapshot: Path) -> dict[str, Path]:
    files = {name: snapshot / name for name in SURECHEMBL_FILES}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise ValueError(f"SureChEMBL snapshot is missing: {', '.join(missing)}")
    return files


def duckdb_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def surechembl_family(family_id: object, publication: str) -> tuple[str, str]:
    value = clean(family_id)
    if value and value != "-1":
        return f"surechembl:{value}", "source_reported"
    return f"surechembl-publication:{publication}", "publication_fallback"


def upsert_patent_candidate(
    db: sqlite3.Connection,
    record: dict[str, object],
    release_id: str,
    artifact_id: str,
    parser_version: str,
    created_at: str,
) -> str | None:
    publication = clean(record.get("publication_number"))
    if not publication:
        raise ValueError("candidate requires publication_number")
    publication = re.sub(r"\s+", "", publication).upper()
    drug_id = clean(record.get("drug_id"))
    compound_id = clean(record.get("compound_id"))
    source_compound_id = clean(record.get("source_compound_id"))
    match_type = clean(record.get("match_type"))
    if not drug_id or not compound_id or not source_compound_id:
        raise ValueError("candidate requires drug_id, compound_id, and source_compound_id")
    if match_type not in {"exact_structure", "same_connectivity"}:
        raise ValueError(f"unsupported candidate match_type: {match_type!r}")
    if not db.execute(
        """SELECT 1 FROM drug_compound
           WHERE drug_id = ? AND compound_id = ?""",
        (drug_id, compound_id),
    ).fetchone():
        raise ValueError(f"candidate references an unknown drug/compound link: {drug_id}")

    family, family_type = surechembl_family(record.get("family_id"), publication)
    db.execute(
        """INSERT OR IGNORE INTO patent_family
        (family_id, family_type, source_id, confidence)
        VALUES (?, ?, 'surechembl_bulk', 0.9)""",
        (family, family_type),
    )
    db.execute(
        """INSERT INTO patent_document
        (publication_number, country_code, publication_date, title, artifact_id,
         source_id, source_document_id, parser_version, raw_record_json)
        VALUES (?, ?, ?, ?, ?, 'surechembl_bulk', ?, ?, ?)
        ON CONFLICT(publication_number) DO UPDATE SET
          title=COALESCE(excluded.title, patent_document.title),
          publication_date=COALESCE(excluded.publication_date, patent_document.publication_date)""",
        (
            publication,
            clean(record.get("country")) or publication[:2],
            clean(record.get("publication_date")),
            clean(record.get("title")),
            artifact_id,
            clean(record.get("source_patent_id")),
            parser_version,
            json_text(
                {
                    "assignee": record.get("assignee"),
                    "cpc": record.get("cpc"),
                    "ipcr": record.get("ipcr"),
                    "snapshot_manifest_sha256": record.get("snapshot_manifest_sha256"),
                }
            ),
        ),
    )
    db.execute(
        """INSERT OR IGNORE INTO patent_family_member
        (family_id, publication_number, relationship) VALUES (?, ?, 'member')""",
        (family, publication),
    )
    field_id = clean(record.get("field_id"))
    confidence = 0.98 if match_type == "exact_structure" else 0.85
    candidate_id = stable_id(
        "surechembl-candidate",
        drug_id,
        compound_id,
        publication,
        source_compound_id,
        field_id,
    )
    db.execute(
        """INSERT INTO patent_candidate
        (candidate_id, drug_id, compound_id, publication_number, source_release_id,
         source_compound_id, source_field_id, source_field_name, match_type,
         confidence, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'needs_review', ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
          source_field_name=excluded.source_field_name,
          confidence=excluded.confidence""",
        (
            candidate_id,
            drug_id,
            compound_id,
            publication,
            release_id,
            source_compound_id,
            field_id,
            clean(record.get("field_name")),
            match_type,
            confidence,
            created_at,
        ),
    )
    return publication


def ingest_patent_candidates_jsonl(
    db: sqlite3.Connection,
    input_path: Path,
    release: str,
) -> dict[str, int]:
    started_at = now()
    counts: dict[str, object] = {
        "input_rows": 0,
        "accepted_rows": 0,
        "excluded_rows": 0,
        "rejected_rows": 0,
        "candidates": 0,
        "patents": 0,
    }
    seen_patents: set[str] = set()
    manifest_hashes: set[str] = set()
    created_at = now()
    with atomic(db, "candidate_jsonl"):
        release_id, artifacts = register_release(
            db,
            "surechembl_bulk",
            release,
            [input_path],
            "cloud-surechembl-candidate-jsonl-v2",
        )
        artifact_id = artifacts[input_path.name.casefold()]
        with input_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                counts["input_rows"] += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"invalid candidate JSON on line {line_number}: {error.msg}"
                    ) from error
                if not isinstance(record, dict):
                    raise ValueError(f"candidate line {line_number} must be an object")
                manifest_hash = clean(record.get("snapshot_manifest_sha256"))
                if not manifest_hash or not re.fullmatch(r"[0-9a-fA-F]{64}", manifest_hash):
                    raise ValueError(
                        f"candidate line {line_number} requires snapshot_manifest_sha256"
                    )
                manifest_hashes.add(manifest_hash.casefold())
                if len(manifest_hashes) > 1:
                    raise ValueError("candidate file references multiple snapshot manifests")
                publication = upsert_patent_candidate(
                    db,
                    record,
                    release_id,
                    artifact_id,
                    "cloud-surechembl-candidate-jsonl-v2",
                    created_at,
                )
                counts["candidates"] += 1
                counts["accepted_rows"] += 1
                if publication not in seen_patents:
                    seen_patents.add(publication)
                    counts["patents"] += 1
        if counts["input_rows"] == 0:
            raise ValueError("candidate file is empty")
        counts["reason_counts"] = {}
        record_ingestion_run(
            db, release_id, "surechembl_bulk",
            "cloud-surechembl-candidate-jsonl-v2", counts, started_at,
        )
    db.commit()
    return {"candidates": int(counts["candidates"]), "patents": int(counts["patents"])}


def ingest_surechembl(
    db: sqlite3.Connection,
    snapshot: Path,
    release: str,
    batch_size: int = 25_000,
    artifact_snapshot: Path | None = None,
) -> dict[str, int]:
    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError("SureChEMBL ingestion requires: python -m pip install duckdb") from error
    files = require_snapshot_files(snapshot)
    artifact_files = require_snapshot_files(artifact_snapshot or snapshot)
    started_at = now()
    seeds = db.execute(
        """SELECT dc.drug_id, c.compound_id, c.inchi_key, c.connectivity_key
           FROM drug_compound dc JOIN compound c USING (compound_id)
           WHERE c.inchi_key IS NOT NULL OR c.connectivity_key IS NOT NULL"""
    ).fetchall()
    if not seeds:
        raise ValueError("No structured catalogue compounds are available; ingest ChEMBL first")
    warehouse = duckdb.connect()
    warehouse.execute(
        "CREATE TEMP TABLE seed(drug_id VARCHAR, compound_id VARCHAR, inchi_key VARCHAR, connectivity_key VARCHAR)"
    )
    warehouse.executemany(
        "INSERT INTO seed VALUES (?, ?, ?, ?)",
        [(row["drug_id"], row["compound_id"], row["inchi_key"], row["connectivity_key"]) for row in seeds],
    )
    query = f"""
        SELECT s.drug_id, s.compound_id, sc.id AS source_compound_id,
               CASE WHEN s.inchi_key IS NOT NULL AND sc.inchi_key = s.inchi_key
                    THEN 'exact_structure' ELSE 'same_connectivity' END AS match_type,
               p.id AS source_patent_id, p.patent_number, p.country,
               p.publication_date, p.family_id, p.title, p.assignee, p.cpc, p.ipcr,
               pcm.field_id, f.field_name
        FROM seed s
        JOIN read_parquet('{duckdb_path(files['compounds.parquet'])}') sc
          ON (s.inchi_key IS NOT NULL AND sc.inchi_key = s.inchi_key)
          OR (s.connectivity_key IS NOT NULL AND substr(sc.inchi_key, 1, 14) = s.connectivity_key)
        JOIN read_parquet('{duckdb_path(files['patent_compound_map.parquet'])}') pcm
          ON pcm.compound_id = sc.id
        JOIN read_parquet('{duckdb_path(files['patents.parquet'])}') p
          ON p.id = pcm.patent_id
        LEFT JOIN read_parquet('{duckdb_path(files['fields.parquet'])}') f
          ON f.id = pcm.field_id
        ORDER BY s.drug_id, p.patent_number, sc.id, pcm.field_id
    """
    counts: dict[str, object] = {
        "input_rows": 0,
        "accepted_rows": 0,
        "excluded_rows": 0,
        "rejected_rows": 0,
        "seeds": len(seeds),
        "candidates": 0,
        "patents": 0,
    }
    seen_patents: set[str] = set()
    created_at = now()
    try:
        with atomic(db, "surechembl_release"):
            release_id, artifacts = register_release(
                db, "surechembl_bulk", release, artifact_files.values(),
                "surechembl-parquet-v2",
            )
            patent_artifact = artifacts["patents.parquet"]
            cursor = warehouse.execute(query)
            while batch := cursor.fetchmany(batch_size):
                for record in batch:
                    (
                drug_id, compound_id, source_compound_id, match_type, source_patent_id,
                publication_number, country, publication_date, family_id, title,
                assignee, cpc, ipcr, field_id, field_name,
                    ) = record
                    publication = upsert_patent_candidate(
                        db,
                        {
                    "drug_id": drug_id,
                    "compound_id": compound_id,
                    "source_compound_id": source_compound_id,
                    "match_type": match_type,
                    "source_patent_id": source_patent_id,
                    "publication_number": publication_number,
                    "country": country,
                    "publication_date": publication_date,
                    "family_id": family_id,
                    "title": title,
                    "assignee": assignee,
                    "cpc": cpc,
                    "ipcr": ipcr,
                    "field_id": field_id,
                    "field_name": field_name,
                        },
                        release_id,
                        patent_artifact,
                        "surechembl-parquet-v2",
                        created_at,
                    )
                    counts["input_rows"] += 1
                    counts["accepted_rows"] += 1
                    counts["candidates"] += 1
                    if publication not in seen_patents:
                        seen_patents.add(publication)
                        counts["patents"] += 1
            counts["reason_counts"] = {}
            record_ingestion_run(
                db, release_id, "surechembl_bulk", "surechembl-parquet-v2",
                counts, started_at,
            )
        db.commit()
    finally:
        warehouse.close()
    return {
        "seeds": int(counts["seeds"]),
        "candidates": int(counts["candidates"]),
        "patents": int(counts["patents"]),
    }


def refresh_coverage(db: sqlite3.Connection, *, commit: bool = True) -> dict[str, int]:
    refreshed_at = now()
    rows = db.execute(
        """WITH
        patent_stats AS (
          SELECT drug_id, count(DISTINCT publication_number) patent_count
          FROM patent_candidate WHERE review_status <> 'rejected' GROUP BY drug_id
        ),
        example_links AS (
          SELECT pc.drug_id, e.evidence_span_id
          FROM patent_candidate pc JOIN evidence_span e USING (publication_number)
          WHERE pc.review_status <> 'rejected' AND e.review_status <> 'rejected'
          UNION
          SELECT pc.drug_id, e.evidence_span_id
          FROM patent_candidate pc
          JOIN patent_family_member candidate_member
            ON candidate_member.publication_number = pc.publication_number
          JOIN patent_family_member evidence_member
            ON evidence_member.family_id = candidate_member.family_id
          JOIN evidence_span e
            ON e.publication_number = evidence_member.publication_number
          WHERE pc.review_status <> 'rejected' AND e.review_status <> 'rejected'
        ),
        example_stats AS (
          SELECT drug_id, count(DISTINCT evidence_span_id) example_count
          FROM example_links GROUP BY drug_id
        ),
        route_stats AS (
          SELECT dc.drug_id,
                 count(DISTINCT CASE WHEN pr.review_status = 'accepted' THEN pr.route_id END) reviewed_count,
                 count(DISTINCT CASE WHEN pr.review_status IN ('needs_review', 'unreviewed') THEN pr.route_id END) review_count
          FROM drug_compound dc JOIN process_route pr ON pr.target_compound_id = dc.compound_id
          GROUP BY dc.drug_id
        ),
        price_stats AS (
          SELECT dc.drug_id,
                 count(DISTINCT CASE WHEN re.actual_material_cost IS NOT NULL THEN rc.route_candidate_id END) priced_count,
                 max(re.actual_cost_coverage) max_coverage
          FROM drug_compound dc JOIN route_candidate rc ON rc.target_compound_id = dc.compound_id
          JOIN route_evaluation re USING (route_candidate_id)
          GROUP BY dc.drug_id
        )
        SELECT d.drug_id, COALESCE(p.patent_count, 0) patent_count,
               COALESCE(e.example_count, 0) example_count,
               COALESCE(r.reviewed_count, 0) reviewed_count,
               COALESCE(r.review_count, 0) review_count,
               COALESCE(ps.priced_count, 0) priced_count,
               COALESCE(ps.max_coverage, 0) max_coverage,
               COALESCE(o.public_evidence_unavailable, 0) unavailable,
               o.reason unavailable_reason
        FROM drug_entity d
        LEFT JOIN patent_stats p USING (drug_id)
        LEFT JOIN example_stats e USING (drug_id)
        LEFT JOIN route_stats r USING (drug_id)
        LEFT JOIN price_stats ps USING (drug_id)
        LEFT JOIN drug_coverage_override o USING (drug_id)
        ORDER BY d.drug_id"""
    ).fetchall()
    status_counts: dict[str, int] = {}
    for row in rows:
        flags = {
            "identified": 1,
            "patents_found": int(row["patent_count"] > 0),
            "examples_extracted": int(row["example_count"] > 0),
            "routes_under_review": int(row["review_count"] > 0),
            "complete_reviewed_route": int(row["reviewed_count"] > 0),
            "price_complete": int(row["reviewed_count"] > 0 and row["max_coverage"] >= 0.8),
            "cost_comparison_ready": int(row["reviewed_count"] > 0 and row["priced_count"] >= 2),
            "public_evidence_unavailable": int(row["unavailable"]),
        }
        status = "identified"
        for candidate in (
            "patents_found", "examples_extracted", "routes_under_review",
            "complete_reviewed_route", "price_complete", "cost_comparison_ready",
        ):
            if flags[candidate]:
                status = candidate
        if flags["public_evidence_unavailable"]:
            status = "public_evidence_unavailable"
        details = {
            "max_actual_cost_coverage": row["max_coverage"],
            "public_evidence_unavailable_reason": row["unavailable_reason"],
        }
        db.execute(
            """INSERT INTO drug_coverage
            (drug_id, status, identified, patents_found, examples_extracted,
             routes_under_review, complete_reviewed_route, price_complete,
             cost_comparison_ready, public_evidence_unavailable, patent_count,
             extracted_example_count, reviewed_route_count, priced_route_count,
             refreshed_at, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drug_id) DO UPDATE SET
              status=excluded.status, identified=excluded.identified,
              patents_found=excluded.patents_found,
              examples_extracted=excluded.examples_extracted,
              routes_under_review=excluded.routes_under_review,
              complete_reviewed_route=excluded.complete_reviewed_route,
              price_complete=excluded.price_complete,
              cost_comparison_ready=excluded.cost_comparison_ready,
              public_evidence_unavailable=excluded.public_evidence_unavailable,
              patent_count=excluded.patent_count,
              extracted_example_count=excluded.extracted_example_count,
              reviewed_route_count=excluded.reviewed_route_count,
              priced_route_count=excluded.priced_route_count,
              refreshed_at=excluded.refreshed_at, details_json=excluded.details_json""",
            (
                row["drug_id"], status, flags["identified"], flags["patents_found"],
                flags["examples_extracted"], flags["routes_under_review"],
                flags["complete_reviewed_route"], flags["price_complete"],
                flags["cost_comparison_ready"], flags["public_evidence_unavailable"],
                row["patent_count"], row["example_count"], row["reviewed_count"],
                row["priced_count"], refreshed_at, json_text(details),
            ),
        )
        status_counts[status] = status_counts.get(status, 0) + 1
    if commit:
        db.commit()
    return status_counts


def summary(db: sqlite3.Connection) -> dict:
    tables = (
        "drug_entity", "drug_alias", "compound", "regulatory_product",
        "patent_candidate", "patent_document", "drug_coverage",
    )
    counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    statuses = {
        row["status"]: row["count"]
        for row in db.execute("SELECT status, count(*) count FROM drug_coverage GROUP BY status")
    }
    return {"counts": counts, "coverage": statuses}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, default=DEFAULT_DB)
    result.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    result.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    result.add_argument("--storage-policy", type=Path, default=DEFAULT_STORAGE_POLICY)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    fda = commands.add_parser("ingest-fda")
    fda.add_argument("--drugs-fda", type=Path)
    fda.add_argument("--orange-book", type=Path)
    fda.add_argument("--release", required=True)
    chembl = commands.add_parser("ingest-chembl")
    chembl.add_argument("--chembl-sqlite", type=Path, required=True)
    chembl.add_argument("--release", required=True)
    catalogue = commands.add_parser("ingest-catalogue-jsonl")
    catalogue.add_argument("--input", type=Path, required=True)
    catalogue.add_argument("--source", required=True)
    catalogue.add_argument("--release", required=True)
    surechembl = commands.add_parser("ingest-surechembl")
    surechembl.add_argument("--snapshot", type=Path, required=True)
    surechembl.add_argument("--release", required=True)
    surechembl.add_argument("--batch-size", type=int, default=25_000)
    candidates = commands.add_parser("ingest-patent-candidates-jsonl")
    candidates.add_argument("--input", type=Path, required=True)
    candidates.add_argument("--release", required=True)
    commands.add_parser("refresh-coverage")
    commands.add_parser("summary")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    policy = StoragePolicy.load(args.storage_policy.resolve())
    resolved_db = args.db.resolve()
    if is_relative_to(resolved_db, policy.raw_root.resolve()):
        print("ERROR: curated SQLite database must not live in the Drive raw store", file=sys.stderr)
        return 1
    db = connect(resolved_db, args.schema.resolve())
    try:
        register_sources(db, args.sources.resolve())
        if args.command == "init":
            result: object = {"database": str(args.db.resolve()), "initialized": True}
        elif args.command == "ingest-fda":
            if not args.drugs_fda and not args.orange_book:
                raise ValueError("provide --drugs-fda and/or --orange-book")
            original_drugs_fda = args.drugs_fda.resolve() if args.drugs_fda else None
            original_orange_book = args.orange_book.resolve() if args.orange_book else None
            staged_drugs_fda = stage_file(original_drugs_fda, policy) if original_drugs_fda else None
            staged_orange_book = (
                stage_file(original_orange_book, policy) if original_orange_book else None
            )
            result = ingest_fda(
                db, staged_drugs_fda, staged_orange_book, args.release,
                original_drugs_fda, original_orange_book,
            )
            result["coverage"] = refresh_coverage(db)
        elif args.command == "ingest-chembl":
            original_chembl = args.chembl_sqlite.resolve()
            result = ingest_chembl(
                db, stage_file(original_chembl, policy), args.release, original_chembl
            )
            result["coverage"] = refresh_coverage(db)
        elif args.command == "ingest-catalogue-jsonl":
            registry = json.loads(args.sources.resolve().read_text(encoding="utf-8"))
            source_ids = {source["id"] for source in registry["sources"]}
            if args.source not in source_ids:
                raise ValueError(f"unknown source: {args.source}")
            result = ingest_catalogue_jsonl(db, args.input, args.source, args.release)
            result["coverage"] = refresh_coverage(db)
        elif args.command == "ingest-surechembl":
            original_snapshot = args.snapshot.resolve()
            staged_snapshot = stage_snapshot(
                original_snapshot, SURECHEMBL_FILES, policy
            )
            result = ingest_surechembl(
                db, staged_snapshot, args.release, args.batch_size, original_snapshot
            )
            result["coverage"] = refresh_coverage(db)
        elif args.command == "ingest-patent-candidates-jsonl":
            result = ingest_patent_candidates_jsonl(
                db, args.input.resolve(), args.release
            )
            result["coverage"] = refresh_coverage(db)
        elif args.command == "refresh-coverage":
            result = {"coverage": refresh_coverage(db)}
        else:
            result = summary(db)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
