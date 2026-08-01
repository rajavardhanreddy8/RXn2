#!/usr/bin/env python3
"""Populate deterministic molecule properties and atom counts with RDKit."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.app.chemistry import annotate_compound
from scripts.bulk_pipeline import DEFAULT_DB, DEFAULT_SCHEMA, connect


def seed_periodic_table(db: sqlite3.Connection) -> int:
    try:
        from rdkit.Chem import GetPeriodicTable
    except ModuleNotFoundError as error:
        raise RuntimeError("RDKit is required to seed the periodic table") from error
    periodic = GetPeriodicTable()
    db.executemany(
        "INSERT OR IGNORE INTO element (element_id, atomic_number, symbol, name) VALUES (?, ?, ?, ?)",
        [
            (number, number, periodic.GetElementSymbol(number), periodic.GetElementName(number))
            for number in range(1, 119)
        ],
    )
    return db.execute("SELECT count(*) FROM element").fetchone()[0]


def annotate_catalogue(db: sqlite3.Connection) -> dict[str, int]:
    seed_periodic_table(db)
    rows = db.execute("SELECT compound_id, smiles FROM compound ORDER BY compound_id").fetchall()
    counts = {"input": len(rows), "annotated": 0, "missing_structure": 0, "invalid_structure": 0}
    for row in rows:
        if not row["smiles"]:
            counts["missing_structure"] += 1
            continue
        try:
            annotate_compound(db, row["compound_id"], row["smiles"])
            counts["annotated"] += 1
        except ValueError:
            counts["invalid_structure"] += 1
    if counts["input"] != sum(counts[key] for key in counts if key != "input"):
        raise RuntimeError("compound annotation accounting mismatch")
    db.commit()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args(argv)
    db = connect(args.db.resolve(), args.schema.resolve())
    try:
        print(json.dumps(annotate_catalogue(db), sort_keys=True))
        return 0
    except (RuntimeError, sqlite3.Error) as error:
        db.rollback()
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
