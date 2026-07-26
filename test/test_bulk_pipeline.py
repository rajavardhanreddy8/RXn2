from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import duckdb
import pytest

from scripts.bulk_pipeline import (
    connect,
    ingest_catalogue_jsonl,
    ingest_chembl,
    ingest_fda,
    ingest_patent_candidates_jsonl,
    ingest_surechembl,
    refresh_coverage,
    register_release,
    register_sources,
)
from scripts.catalogue_converters import convert
from scripts.cloud_prepare import export_chembl, export_seeds, export_surechembl
from scripts.ingest_ocr_result import ingest_ocr_result


ROOT = Path(__file__).resolve().parents[1]


def make_fda_zip(path: Path) -> None:
    content = (
        "ApplNo\tProductNo\tForm\tStrength\tDrugName\tActiveIngredient\n"
        "019872\t001\tTABLET;ORAL\t500MG\tTYLENOL\tACETAMINOPHEN\n"
    )
    marketing_status = (
        "ApplNo\tProductNo\tMarketingStatusID\n"
        "019872\t001\t1\n"
    )
    lookup = (
        "MarketingStatusID\tMarketingStatusDescription\n"
        "1\tPrescription\n"
        "3\tDiscontinued\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Products.txt", content)
        archive.writestr("MarketingStatus.txt", marketing_status)
        archive.writestr("MarketingStatus_Lookup.txt", lookup)


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
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM link_candidate").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM drug_alias").fetchone()[0] >= 3
    assert db.execute(
        "SELECT marketing_status FROM regulatory_product"
    ).fetchone()[0] == "active"

    surechembl = tmp_path / "surechembl"
    make_surechembl(surechembl)
    result = ingest_surechembl(db, surechembl, "2026-07-17", batch_size=10)
    assert result["candidates"] == 1
    assert result["patents"] == 1
    assert ingest_surechembl(db, surechembl, "2026-07-17", batch_size=10)["candidates"] == 1
    assert db.execute("SELECT count(*) FROM patent_candidate").fetchone()[0] == 1

    statuses = refresh_coverage(db)
    assert statuses == {"identified": 1, "patents_found": 1}
    coverage = db.execute(
        "SELECT * FROM drug_coverage WHERE patent_count = 1"
    ).fetchone()
    assert coverage["patent_count"] == 1
    assert coverage["patents_found"] == 1
    assert coverage["complete_reviewed_route"] == 0

    source_pdf = tmp_path / "US-123-A1.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n% fixture patent\n")
    ocr_output = tmp_path / "ocr" / "job-1"
    ocr_output.mkdir(parents=True)
    result_json = ocr_output / "result.json"
    result_json.write_text(
        """{"job_id":"job-1","status":"succeeded","created_at":"2026-07-24T00:00:00Z","completed_at":"2026-07-24T00:01:00Z","input_files":[{"name":"US-123-A1.pdf"}],"text":"Example 1: acetaminophen was isolated."}""",
        encoding="utf-8",
    )
    (ocr_output / "result.md").write_text("# OCR result\n", encoding="utf-8")
    (ocr_output / "result.txt").write_text(
        "Example 1: acetaminophen was isolated.", encoding="utf-8"
    )
    processed_ocr = tmp_path / "processed-ocr"
    ocr = ingest_ocr_result(
        db,
        result_json,
        "US-123-A1",
        source_pdf,
        processed_root=processed_ocr,
    )
    assert ocr["status"] == "needs_review"
    assert ocr["evidence_spans_created"] == 0
    assert (processed_ocr / "job-1" / "result.txt").is_file()
    assert db.execute(
        "SELECT count(*) FROM extraction_job WHERE review_status='unreviewed'"
    ).fetchone()[0] == 1
    assert refresh_coverage(db) == {"identified": 1, "patents_found": 1}

    cloud_output = tmp_path / "ocr" / "job-cloud"
    cloud_output.mkdir()
    cloud_result = cloud_output / "result.json"
    cloud_result.write_text(
        '{"job_id":"job-cloud","status":"succeeded","text":"Cloud OCR text."}',
        encoding="utf-8",
    )
    (cloud_output / "result.txt").write_text("Cloud OCR text.", encoding="utf-8")
    cloud_ocr = ingest_ocr_result(
        db,
        cloud_result,
        "US-123-A1",
        tmp_path / "cloud-only.pdf",
        source_document_sha256="b" * 64,
        processed_root=processed_ocr,
    )
    assert cloud_ocr["status"] == "needs_review"
    assert db.execute(
        "SELECT input_sha256 FROM extraction_job WHERE extraction_job_id=?",
        ("unlimited-ocr:job-cloud",),
    ).fetchone()[0] == "b" * 64
    db.close()


def test_catalogue_jsonl_deduplicates_exact_structure_and_adds_regulatory_product(tmp_path):
    database = tmp_path / "catalogue.sqlite"
    db = connect(database, ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    chembl = tmp_path / "chembl.sqlite"
    make_chembl(chembl)
    ingest_chembl(db, chembl, "CHEMBL37")

    normalized = tmp_path / "pubchem.jsonl"
    normalized.write_text(
        """{"preferred_name":"Paracetamol","identifiers":{"PUBCHEM_CID":"1983"},"compound":{"compound_id":"PUBCHEM:1983","smiles":"CC(=O)NC1=CC=C(C=C1)O","inchi_key":"RZVAJINKPMORJF-UHFFFAOYSA-N","material_form":"active_moiety"},"regulatory_products":[{"jurisdiction":"EU-EMA","application_number":"EMA-TEST-1","trade_name":"Paracetamol test"}]}\n""",
        encoding="utf-8",
    )
    result = ingest_catalogue_jsonl(db, normalized, "pubchem_bulk", "2026-07-24")
    assert result["regulatory_products"] == 1
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM drug_identifier WHERE namespace='PUBCHEM_CID'"
    ).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM regulatory_product").fetchone()[0] == 1
    db.close()


def test_source_converters_account_for_every_input_row(tmp_path):
    source = tmp_path / "pubchem.csv"
    source.write_text(
        "CID,Title,InChIKey,Canonical_SMILES,Approved,Molecule_Type\n"
        "1983,Paracetamol,RZVAJINKPMORJF-UHFFFAOYSA-N,CC(=O)NC1=CC=C(C=C1)O,true,small molecule\n"
        "2,Example antibody,AAAAAAAAAAAAAA-BBBBBBBBBB-C,,true,biologic\n",
        encoding="utf-8",
    )
    output = tmp_path / "normalized.jsonl"
    report_path = tmp_path / "reconciliation.json"
    report = convert("pubchem", source, output, report_path)
    assert report["input_rows"] == 2
    assert report["accepted_rows"] == 1
    assert report["accepted_records"] == 1
    assert report["excluded_rows"] == 1
    assert report["rejected_rows"] == 0
    assert sum(report["reason_counts"].values()) == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
    assert __import__("json").loads(output.read_text(encoding="utf-8"))[
        "requires_existing_drug"
    ] is True


def test_ema_converter_preserves_product_and_excludes_biologics(tmp_path):
    source = tmp_path / "ema.csv"
    source.write_text(
        "Medicine name,Active substance,Product number,Authorisation status,Medicine type\n"
        "Example tablets,Example drug,EMA-1,Authorised,Human medicine\n"
        "Example vaccine,Example antigen,EMA-2,Authorised,Vaccine\n",
        encoding="utf-8",
    )
    output = tmp_path / "ema.jsonl"
    report = convert("ema", source, output, tmp_path / "ema-report.json")
    record = __import__("json").loads(output.read_text(encoding="utf-8"))
    assert report["input_rows"] == 2
    assert report["accepted_records"] == 1
    assert report["excluded_rows"] == 1
    assert record["regulatory_products"][0]["application_number"] == "EMA-1"
    assert record["identifiers"] == {}
    assert record["requires_existing_drug"] is True


def test_ema_json_enriches_only_an_existing_small_molecule(tmp_path):
    source = tmp_path / "ema.json"
    source.write_text(
        """{"metadata":{"updated":"2026-07-24"},"data":[{"category":"Human","name_of_medicine":"Paracetamol example","ema_product_number":"EMEA/H/C/000001","medicine_status":"Authorised","active_substance":"Paracetamol","advanced_therapy":"No","biosimilar":"No","marketing_authorisation_date":"01/01/2026"}]}""",
        encoding="utf-8",
    )
    output = tmp_path / "ema.jsonl"
    report = convert("ema", source, output, tmp_path / "ema-report.json")
    assert report["input_rows"] == report["accepted_rows"] == 1

    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    skipped = ingest_catalogue_jsonl(db, output, "ema_medicines", "2026-07-24")
    assert skipped["unmatched_existing_drugs"] == 1
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 0

    chembl = tmp_path / "chembl.sqlite"
    make_chembl(chembl)
    ingest_chembl(db, chembl, "CHEMBL37")
    enriched = ingest_catalogue_jsonl(db, output, "ema_medicines", "2026-07-24")
    assert enriched["regulatory_products"] == 0
    assert enriched["unmatched_existing_drugs"] == 1
    assert db.execute(
        "SELECT count(*) FROM link_candidate WHERE subject_type='source_record'"
    ).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 1
    db.close()


def test_release_rerun_rejects_same_size_checksum_change(tmp_path):
    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    artifact = tmp_path / "snapshot.dat"
    artifact.write_bytes(b"alpha")
    register_release(db, "pubchem_bulk", "fixture-1", [artifact], "fixture-parser")
    artifact.write_bytes(b"omega")
    with pytest.raises(RuntimeError, match="immutable release artifact changed"):
        register_release(db, "pubchem_bulk", "fixture-1", [artifact], "fixture-parser")
    db.close()


def test_catalogue_preserves_salts_and_stereochemistry(tmp_path):
    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    source = tmp_path / "forms.jsonl"
    source.write_text(
        "\n".join(
            [
                '{"preferred_name":"Example drug","identifiers":{"TEST_DRUG":"1"},"active_moiety_id":"moiety:example","compound":{"compound_id":"example:active","inchi_key":"AAAAAAAAAAAAAA-BBBBBBBBBB-C","material_form":"active_moiety"}}',
                '{"preferred_name":"Example drug hydrochloride","identifiers":{"TEST_DRUG":"1"},"active_moiety_id":"moiety:example","compound":{"compound_id":"example:salt","inchi_key":"AAAAAAAAAAAAAA-CCCCCCCCCC-D","material_form":"salt"}}',
                '{"preferred_name":"Example drug stereoisomer","identifiers":{"TEST_DRUG":"1"},"active_moiety_id":"moiety:example","compound":{"compound_id":"example:stereo","inchi_key":"AAAAAAAAAAAAAA-DDDDDDDDDD-E","material_form":"stereoisomer"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = ingest_catalogue_jsonl(db, source, "pubchem_bulk", "forms-1")
    assert result["compounds"] == 3
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 1
    forms = {
        row[0]: row[1]
        for row in db.execute("SELECT compound_id, material_form FROM compound")
    }
    assert forms == {
        "example:active": "active_moiety",
        "example:salt": "salt",
        "example:stereo": "stereoisomer",
    }
    assert db.execute(
        "SELECT count(DISTINCT inchi_key) FROM compound WHERE connectivity_key='AAAAAAAAAAAAAA'"
    ).fetchone()[0] == 3
    db.close()


def test_malformed_catalogue_release_rolls_back_records(tmp_path):
    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    source = tmp_path / "malformed.jsonl"
    source.write_text(
        '{"preferred_name":"Must roll back","identifiers":{}}\n{"preferred_name":',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        ingest_catalogue_jsonl(db, source, "pubchem_bulk", "malformed-1")
    assert db.execute("SELECT count(*) FROM drug_entity").fetchone()[0] == 0
    assert db.execute(
        "SELECT count(*) FROM source_release WHERE release_id='pubchem_bulk:malformed-1'"
    ).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM ingestion_run").fetchone()[0] == 0
    db.close()


def test_fda_accounting_and_discontinued_status_are_first_class(tmp_path):
    source = tmp_path / "orange.zip"
    content = (
        "Appl_No~Product_No~DF;Route~Strength~Trade_Name~Ingredient~Type\n"
        "123456~001~TABLET;ORAL~10MG~ACTIVE BRAND~ACTIVE DRUG~RX\n"
        "123456~002~TABLET;ORAL~20MG~OLD BRAND~OLD DRUG~DISCN\n"
        "~~~~~~\n"
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("products.txt", content)
    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    result = ingest_fda(db, None, source, "2026-07")
    counts = result["orange_book"]
    assert counts["input_rows"] == 3
    assert counts["accepted_rows"] == 2
    assert counts["rejected_rows"] == 1
    assert counts["reason_counts"]["missing_application_number_and_product_number_and_active_ingredient"] == 1
    statuses = {
        row[0] for row in db.execute(
            "SELECT DISTINCT marketing_status FROM regulatory_product"
        )
    }
    assert statuses == {"active", "discontinued"}
    run = db.execute("SELECT * FROM ingestion_run").fetchone()
    assert run["input_rows"] == 3
    assert run["accepted_rows"] == 2
    assert run["rejected_rows"] == 1
    db.close()


def test_malformed_fda_release_is_atomic(tmp_path):
    source = tmp_path / "broken.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "Products.txt",
            "ApplNo\tProductNo\tActiveIngredient\n\t\t\n",
        )
    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    with pytest.raises(ValueError, match="no valid FDA product rows"):
        ingest_fda(db, source, None, "broken")
    assert db.execute("SELECT count(*) FROM regulatory_product").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM source_release").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM artifact").fetchone()[0] == 0
    db.close()


def test_cloud_preparation_round_trip_keeps_raw_parquet_out_of_local_db(tmp_path):
    chembl = tmp_path / "chembl.sqlite"
    make_chembl(chembl)
    catalogue = tmp_path / "chembl-catalogue.jsonl"
    assert export_chembl(chembl, catalogue)["catalogue_records"] == 1

    db = connect(tmp_path / "catalogue.sqlite", ROOT / "sql" / "schema.sql")
    register_sources(db, ROOT / "configs" / "sources.json")
    ingest_catalogue_jsonl(db, catalogue, "chembl_snapshot", "CHEMBL37")
    seeds = tmp_path / "seeds.jsonl"
    assert export_seeds(tmp_path / "catalogue.sqlite", seeds)["seed_records"] == 1

    snapshot = tmp_path / "surechembl"
    make_surechembl(snapshot)
    candidates = tmp_path / "candidates.jsonl"
    exported = export_surechembl(
        snapshot, seeds, candidates, "a" * 64, batch_size=10
    )
    assert exported == {"seed_records": 1, "candidate_records": 1}
    imported = ingest_patent_candidates_jsonl(db, candidates, "2026-07-21")
    assert imported == {"candidates": 1, "patents": 1}
    assert refresh_coverage(db) == {"patents_found": 1}
    artifact_paths = [
        row[0] for row in db.execute("SELECT relative_path FROM artifact")
    ]
    assert any(path.endswith("candidates.jsonl") for path in artifact_paths)
    assert not any(path.endswith(".parquet") for path in artifact_paths)
    db.close()
