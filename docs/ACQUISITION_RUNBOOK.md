# Snapshot acquisition runbook

Acquisition is an operator-controlled activity. The project does not hide credentials or uncontrolled network calls inside training or serving.

## 1. Choose a pinned release

Do not use an unrecorded `latest` directory in a reproducible dataset. Select a dated release, read its release notes/schema/license, and create a matching directory:

```text
data/raw/<source_id>/<release_id>/
```

Example:

```text
data/raw/surechembl_bulk/2026-07-17/
```

## 2. Download outside the application

Use the provider's supported bulk-download method. Respect credentials, fair-use limits, and terms. Never scrape a public search UI.

For the first patent–compound candidate build, the minimum SureChEMBL files are:

- `compounds.parquet`
- `patents.parquet`
- `patent_compound_map.parquet`
- `fields.parquet`

The full 2026-07-17 set is large (core files are multiple gigabytes), so ensure adequate disk space and avoid loading it into application memory.

## 3. Register every artifact

```powershell
node src/cli.js init-db --db data/curated/scaleup.sqlite
node src/cli.js register-artifact `
  --db data/curated/scaleup.sqlite `
  --source surechembl_bulk `
  --release 2026-07-17 `
  --released-on 2026-07-17 `
  --file data/raw/surechembl_bulk/2026-07-17/patents.parquet `
  --source-schema-version surechembl-2
```

The command streams SHA-256 in bounded memory and registers file size, media type, relative path, source, and release. Repeat for each file. A changed byte creates a different artifact ID.

## 4. Validate the release contract

Before parsing:

- all expected files exist;
- SHA-256 values are stable across a second check;
- source schema matches the supported parser;
- release/license/terms are stored;
- file counts and key column types are profiled;
- no dated release silently points to changing content.

## 5. Build a candidate slice

Install the bulk-pipeline dependency once:

```powershell
python -m pip install -r requirements-pipeline.txt
```

Build the drug catalogue before matching patents. Both FDA inputs are ZIP files retained exactly as downloaded; ChEMBL is its extracted SQLite database:

```powershell
python scripts/bulk_pipeline.py ingest-fda `
  --drugs-fda data/raw/drugs_at_fda/2026-07-17/drugsatfda.zip `
  --orange-book data/raw/fda_orange_book/2026-07/edrug.zip `
  --release 2026-07-17

python scripts/bulk_pipeline.py ingest-chembl `
  --chembl-sqlite data/raw/chembl_snapshot/CHEMBL37/chembl_37.db `
  --release CHEMBL37
```

PubChem, UniChem, EMA, and later national-regulator adapters feed the same normalized JSONL boundary:

```json
{"preferred_name":"Example drug","aliases":[{"value":"Example brand","type":"brand_name"}],"identifiers":{"PUBCHEM_CID":"123"},"compound":{"compound_id":"PUBCHEM:123","smiles":"...","inchi":"...","inchi_key":"AAAAAAAAAAAAAA-BBBBBBBBBB-C","material_form":"active_moiety"}}
```

```powershell
python scripts/bulk_pipeline.py ingest-catalogue-jsonl `
  --input data/processed/pubchem-approved-small-molecules.jsonl `
  --source pubchem_bulk `
  --release 2026-07-01
```

Ingest a pinned SureChEMBL directory after all four required Parquet files are present:

```powershell
python scripts/bulk_pipeline.py ingest-surechembl `
  --snapshot data/raw/surechembl_bulk/2026-07-17 `
  --release 2026-07-17
```

The command uses DuckDB for the multi-gigabyte joins, streams result batches into SQLite, records artifact checksums, and refreshes `drug_coverage`. It is idempotent and safe to rerun for the same release. `exact_structure` and `same_connectivity` matches remain `needs_review` patent candidates.

The standalone SQL template [`sql/duckdb/build_surechembl_candidates.sql`](../sql/duckdb/build_surechembl_candidates.sql) remains useful for warehouse exploration, but the Python command is the supported ingestion path.

The result is a candidate table, not gold data. It must still be joined to full patent text and curated.

## 6. Acquire only candidate full text

For U.S. candidates, select the matching USPTO grant/application bulk releases and register their ZIP/XML files. Parse documents locally using the DTD/version declared by each record.

For non-U.S. candidates:

- use an EPO OPS snapshot only within its authenticated terms and limits; or
- purchase/use the appropriate licensed EPO/WIPO bulk product;
- otherwise retain bibliographic/compound candidate status and mark full-text evidence unavailable.

Do not automate PATENTSCOPE's public website.

## 7. Freeze the acquisition manifest

```powershell
node src/cli.js manifest `
  --root data/raw/surechembl_bulk/2026-07-17 `
  --output data/manifests/surechembl-2026-07-17.json
```

The dataset version later references this manifest plus parser, policy, curation, and split manifests.

## 8. Verify coverage

```powershell
python scripts/bulk_pipeline.py refresh-coverage
python scripts/bulk_pipeline.py summary
```

The API exposes `GET /api/catalogue/coverage` and `GET /api/catalogue/drugs/{drug_id}`. A drug advances only from recorded local evidence. SureChEMBL association alone can produce `patents_found`; it cannot produce `examples_extracted` or an accepted route.
