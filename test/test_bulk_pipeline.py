from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import duckdb

from scripts.bulk_pipeline import (
    connect,
    ingest_chembl,
    ingest_fda,
    ingest_surechembl,
    refresh_coverage,
    register_sources,
)


ROOT = Path(__file__).resolve().parents[1]


def make_fda_zip(path: Path) -> None:
    content = (
        "ApplNo\tProductNo\tForm\tStrength\tDrugName\tActiveIngredient\n"
        "019872\t001\tTABLET;ORAL\t500MG\tTYLENOL\tACETAMINOPHEN\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Products.txt", content)


def make_chembl(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE molecule_dictionary (
          molregno INTEGER PRIMARY KEY, chembl_id TEXT, pref_name TEXT,
          max_phase INTEGER, molecule_type TEXT, first_approval INTEGER
        );
        CREATE TABLE compound_structures (
          molregno INTEGER, canonical_smiles TEXT, standard_inchi TEXT,
          standard_inchi_key TEXT
        );
        CREATE TABLE molecule_hierarchy (molregno INTEGER, parent_molregno INTEGER);
        CREATE TABLE molecule_synonyms (molregno INTEGER, synonyms TEXT, syn_type TEXT);
        INSERT INTO molecule_dictionary VALUES
          (1, 'CHEMBL112', 'ACETAMINOPHEN', 4, 'Small molecule', 1951);
        INSERT INTO compound_structures VALUES
          (1, 'CC(=O)NC1=CC=C(C=C1)O', 'InChI=1S/C8H9NO2', 'RZVAJINKPMORJF-UHFFFAOYSA-N');
        INSERT INTO molecule_hierarchy VALUES (1, 1);
        INSERT INTO molecule_synonyms VALUES (1, 'Paracetamol', 'INN');
        """
    )
    db.commit()
    db.close()


def make_surechembl(path: Path) -> None:
    path.mkdir()
    warehouse = duckdb.connect()
    warehouse.execute(
        f"""COPY (SELECT 7::BIGINT id, 'CC(=O)NC1=CC=C(C=C1)O' smiles,
                    'InChI=1S/C8H9NO2' inchi,
                    'RZVAJINKPMORJF-UHFFFAOYSA-N' inchi_key, 151.16::DOUBLE mol_weight)
             TO '{(path / 'compounds.parquet').as_posix()}' (FORMAT PARQUET)"""
    )
    warehouse.execute(
        f"""COPY (SELECT 11::BIGINT patent_id, 7::BIGINT compound_id, 3::BIGINT field_id)
             TO '{(path / 'patent_compound_map.parquet').as_posix()}' (FORMAT PARQUET)"""
    )
    warehouse.execute(
        f"""COPY (SELECT 11::BIGINT id, 'US-123-A1' patent_number, 'US' country,
                    DATE '2020-01-01' publication_date, 99::BIGINT family_id,
                    []::VARCHAR[] cpc, []::VARCHAR[] ipcr, []::VARCHAR[] ipc,
                    []::VARCHAR[] ecla, ['Example Pharma']::VARCHAR[] assignee,
                    'Acetaminophen process' title)
             TO '{(path / 'patents.parquet').as_posix()}' (FORMAT PARQUET)"""
    )
    warehouse.execute(
        f"""COPY (SELECT 3::BIGINT id, 'claims' field_name)
             TO '{(path / 'fields.parquet').as_posix()}' (FORMAT PARQUET)"""
    )
    warehouse.close()


def test_catalogue_and_surechembl_pipeline(tmp_path):
    database = tmp_path / "catalogue.sqlite"
    db = connect(database, ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")

    fda = tmp_path / "drugsatfda.zip"
    make_fda_zip(fda)
    assert ingest_fda(db, fda, None, "2026-07-17")["drugs_at_fda"]["products"] == 1

    chembl = tmp_path / "chembl.sqlite"
    make_chembl(chembl)
    assert ingest_chembl(db, chembl, "CHEMBL37")["compounds"] == 1
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM drug_alias").fetchone()[0] >= 3

    surechembl = tmp_path / "surechembl"
    make_surechembl(surechembl)
    result = ingest_surechembl(db, surechembl, "2026-07-17", batch_size=10)
    assert result["candidates"] == 1
    assert result["patents"] == 1

    statuses = refresh_coverage(db)
    assert statuses == {"patents_found": 1}
    coverage = db.execute("SELECT * FROM drug_coverage").fetchone()
    assert coverage["patent_count"] == 1
    assert coverage["patents_found"] == 1
    assert coverage["complete_reviewed_route"] == 0
    db.close()
