#!/usr/bin/env python3
"""Prepare compact RXN2 import files in Colab or another cloud runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


SURECHEMBL_FILES = (
    "compounds.parquet",
    "patent_compound_map.parquet",
    "patents.parquet",
    "fields.parquet",
)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, records) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    count = 0
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(
                    json.dumps(
                        record, ensure_ascii=False, sort_keys=True, default=str
                    )
                    + "\n"
                )
                count += 1
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return count


def write_report(path: Path | None, command: str, output: Path, counts: dict) -> dict:
    result = {
        "contract": "rxn2-cloud-result-v1",
        "command": command,
        "created_at": datetime.now(UTC).isoformat(),
        "output": str(output.resolve()),
        "output_size_bytes": output.stat().st_size,
        "output_sha256": sha256_file(output),
        **counts,
    }
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return result


def export_seeds(database: Path, output: Path) -> dict:
    db = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    def records():
        yield from (
            dict(row)
            for row in db.execute(
                """SELECT dc.drug_id, c.compound_id, c.inchi_key, c.connectivity_key
                   FROM drug_compound dc JOIN compound c USING (compound_id)
                   WHERE c.inchi_key IS NOT NULL OR c.connectivity_key IS NOT NULL
                   ORDER BY dc.drug_id, c.compound_id"""
            )
        )

    count = write_jsonl(output, records())
    db.close()
    if not count:
        raise ValueError("no structured drug seeds are available")
    return {"seed_records": count}


def export_chembl(chembl_sqlite: Path, output: Path) -> dict:
    source = sqlite3.connect(
        f"file:{chembl_sqlite.resolve().as_posix()}?mode=ro", uri=True
    )
    source.row_factory = sqlite3.Row
    required = {
        "molecule_dictionary",
        "compound_structures",
        "molecule_hierarchy",
        "molecule_synonyms",
    }
    present = {
        row[0]
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = required - present
    if missing:
        raise ValueError(f"ChEMBL SQLite is missing: {', '.join(sorted(missing))}")
    aliases: dict[int, list[dict[str, str]]] = {}
    for row in source.execute(
        """SELECT s.molregno, s.synonyms, s.syn_type
           FROM molecule_synonyms s JOIN molecule_dictionary md USING (molregno)
           WHERE md.max_phase = 4 AND lower(md.molecule_type) = 'small molecule'"""
    ):
        if row["synonyms"]:
            aliases.setdefault(int(row["molregno"]), []).append(
                {
                    "value": str(row["synonyms"]).strip(),
                    "type": str(row["syn_type"] or "synonym").strip(),
                }
            )
    query = """
        SELECT md.molregno, md.chembl_id, md.pref_name,
               cs.canonical_smiles, cs.standard_inchi, cs.standard_inchi_key,
               COALESCE(mh.parent_molregno, md.molregno) parent_molregno,
               COALESCE(parent.chembl_id, md.chembl_id) parent_chembl_id,
               COALESCE(parent.pref_name, md.pref_name, md.chembl_id) parent_name
        FROM molecule_dictionary md
        LEFT JOIN compound_structures cs ON cs.molregno = md.molregno
        LEFT JOIN molecule_hierarchy mh ON mh.molregno = md.molregno
        LEFT JOIN molecule_dictionary parent
          ON parent.molregno = COALESCE(mh.parent_molregno, md.molregno)
        WHERE md.max_phase = 4 AND lower(md.molecule_type) = 'small molecule'
        ORDER BY COALESCE(mh.parent_molregno, md.molregno),
                 md.molregno != COALESCE(mh.parent_molregno, md.molregno),
                 md.molregno
    """

    def records():
        for row in source.execute(query):
            parent = row["parent_chembl_id"]
            yield {
                "preferred_name": row["parent_name"],
                "aliases": aliases.get(int(row["molregno"]), []),
                "identifiers": {"CHEMBL": parent},
                "active_moiety_id": f"chembl-moiety:{parent}",
                "compound": {
                    "compound_id": row["chembl_id"],
                    "smiles": row["canonical_smiles"],
                    "inchi": row["standard_inchi"],
                    "inchi_key": row["standard_inchi_key"],
                    "material_form": (
                        "active_moiety"
                        if row["chembl_id"] == parent
                        else "salt_or_form"
                    ),
                    "relationship_type": (
                        "active_moiety"
                        if row["chembl_id"] == parent
                        else "salt_or_form"
                    ),
                },
            }

    count = write_jsonl(output, records())
    source.close()
    return {"catalogue_records": count}


def load_seeds(warehouse, seeds: Path) -> int:
    warehouse.execute(
        """CREATE TEMP TABLE seed(
             drug_id VARCHAR, compound_id VARCHAR,
             inchi_key VARCHAR, connectivity_key VARCHAR)"""
    )
    batch = []
    count = 0
    with seeds.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not record.get("drug_id") or not record.get("compound_id"):
                raise ValueError(f"invalid seed on line {line_number}")
            batch.append(
                (
                    record["drug_id"],
                    record["compound_id"],
                    record.get("inchi_key"),
                    record.get("connectivity_key"),
                )
            )
            count += 1
            if len(batch) == 10_000:
                warehouse.executemany("INSERT INTO seed VALUES (?, ?, ?, ?)", batch)
                batch.clear()
    if batch:
        warehouse.executemany("INSERT INTO seed VALUES (?, ?, ?, ?)", batch)
    return count


def export_surechembl(
    snapshot: Path,
    seeds: Path,
    output: Path,
    snapshot_manifest_sha256: str,
    batch_size: int,
) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", snapshot_manifest_sha256):
        raise ValueError("snapshot manifest SHA-256 must contain 64 hex characters")
    files = {name: snapshot / name for name in SURECHEMBL_FILES}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise ValueError(f"SureChEMBL snapshot is missing: {', '.join(missing)}")
    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError("install duckdb in the cloud runtime") from error
    warehouse = duckdb.connect()
    seed_count = load_seeds(warehouse, seeds)
    if not seed_count:
        raise ValueError("seed file is empty")

    def quoted(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "''")

    cursor = warehouse.execute(
        f"""
        SELECT s.drug_id, s.compound_id, sc.id source_compound_id,
               CASE WHEN s.inchi_key IS NOT NULL AND sc.inchi_key = s.inchi_key
                    THEN 'exact_structure' ELSE 'same_connectivity' END match_type,
               p.id source_patent_id, p.patent_number publication_number,
               p.country, p.publication_date, p.family_id, p.title,
               p.assignee, p.cpc, p.ipcr, pcm.field_id, f.field_name
        FROM seed s
        JOIN read_parquet('{quoted(files["compounds.parquet"])}') sc
          ON (s.inchi_key IS NOT NULL AND sc.inchi_key = s.inchi_key)
          OR (s.connectivity_key IS NOT NULL
              AND substr(sc.inchi_key, 1, 14) = s.connectivity_key)
        JOIN read_parquet('{quoted(files["patent_compound_map.parquet"])}') pcm
          ON pcm.compound_id = sc.id
        JOIN read_parquet('{quoted(files["patents.parquet"])}') p
          ON p.id = pcm.patent_id
        LEFT JOIN read_parquet('{quoted(files["fields.parquet"])}') f
          ON f.id = pcm.field_id
        ORDER BY s.drug_id, p.patent_number, sc.id, pcm.field_id
        """
    )
    columns = [item[0] for item in cursor.description]

    def records():
        while batch := cursor.fetchmany(batch_size):
            for values in batch:
                record = dict(zip(columns, values, strict=True))
                record["snapshot_manifest_sha256"] = snapshot_manifest_sha256.lower()
                yield record

    count = write_jsonl(output, records())
    warehouse.close()
    return {"seed_records": seed_count, "candidate_records": count}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    seeds = commands.add_parser("export-seeds")
    seeds.add_argument("--db", type=Path, required=True)
    seeds.add_argument("--output", type=Path, required=True)
    chembl = commands.add_parser("chembl")
    chembl.add_argument("--chembl-sqlite", type=Path, required=True)
    chembl.add_argument("--output", type=Path, required=True)
    surechembl = commands.add_parser("surechembl")
    surechembl.add_argument("--snapshot", type=Path, required=True)
    surechembl.add_argument("--seeds", type=Path, required=True)
    surechembl.add_argument("--output", type=Path, required=True)
    surechembl.add_argument("--snapshot-manifest-sha256", required=True)
    surechembl.add_argument("--batch-size", type=int, default=25_000)
    for command in (seeds, chembl, surechembl):
        command.add_argument("--report", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "export-seeds":
        counts = export_seeds(args.db, args.output)
    elif args.command == "chembl":
        counts = export_chembl(args.chembl_sqlite, args.output)
    else:
        counts = export_surechembl(
            args.snapshot,
            args.seeds,
            args.output,
            args.snapshot_manifest_sha256,
            args.batch_size,
        )
    print(json.dumps(write_report(args.report, args.command, args.output, counts), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
