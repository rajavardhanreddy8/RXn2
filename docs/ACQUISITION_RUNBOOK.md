# Snapshot acquisition runbook

Acquisition is an operator-controlled activity. The project does not hide credentials or uncontrolled network calls inside training or serving.

## 1. Choose a pinned release

Set `RXN2_RAW_ROOT=I:/My Drive/RXN2/data/raw` in `.env`. Confirm the authoritative Drive store before acquisition:

```powershell
npm run storage:check
```

Do not use an unrecorded `latest` directory in a reproducible dataset. Select a dated release, read its release notes/schema/license, and create a matching directory under the Drive raw root:

```text
I:/My Drive/RXN2/data/raw/<source_id>/<release_id>/
```

Example:

```text
I:/My Drive/RXN2/data/raw/surechembl_bulk/2026-07-21/
```

## 2. Download outside the application

Use the provider's supported bulk-download method. Respect credentials, fair-use limits, and terms. Never scrape a public search UI.

For the first patent–compound candidate build, the minimum SureChEMBL files are:

- `compounds.parquet`
- `patents.parquet`
- `patent_compound_map.parquet`
- `fields.parquet`

The pinned 2026-07-21 core set is about 14 GB before processing
(`compounds` 3.9 GB, `patent_compound_map` 4.6 GB, `patents` 5.5 GB, plus
`fields`). Ensure the storage check passes and never load the set into
application memory.

## 3. Manifest every artifact

```powershell
node src/cli.js manifest `
  --root "I:/My Drive/RXN2/data/raw/surechembl_bulk/2026-07-21" `
  --output "I:/My Drive/RXN2/data/manifests/surechembl-2026-07-21.json"
```

The command streams SHA-256 in bounded memory and records every relative path
and size. Retain the provider's `LICENCE`, `README`, release URL, and release
date beside this manifest. During ingestion the same files are registered in
local SQLite; a changed byte in a pinned release is rejected.

## 4. Validate the release contract

Before parsing:

- all expected files exist;
- SHA-256 values are stable across a second check;
- source schema matches the supported parser;
- release/license/terms are stored;
- file counts and key column types are profiled;
- no dated release silently points to changing content.

## 5. Build a candidate slice

Build the drug catalogue before matching patents. Both FDA inputs are ZIP files retained exactly as downloaded; ChEMBL is its extracted SQLite database:

```powershell
docker compose run --rm api python scripts/bulk_pipeline.py ingest-fda `
  --drugs-fda /raw/drugs_at_fda/2026-07-24/drugsatfda.zip `
  --orange-book /raw/fda_orange_book/2026-07/eobzip.zip `
  --release 2026-07-24

docker compose run --rm api python scripts/bulk_pipeline.py ingest-chembl `
  --chembl-sqlite /raw/chembl_snapshot/CHEMBL37/chembl_37.db `
  --release CHEMBL37
```

PubChem, UniChem, EMA, and later national-regulator adapters feed the same normalized JSONL boundary:

```json
{"preferred_name":"Example drug","aliases":[{"value":"Example brand","type":"brand_name"}],"identifiers":{"PUBCHEM_CID":"123"},"requires_existing_drug":true,"compound":{"compound_id":"PUBCHEM:123","smiles":"...","inchi":"...","inchi_key":"AAAAAAAAAAAAAA-BBBBBBBBBB-C","material_form":"active_moiety"}}
```

Convert a source export with its reconciliation report, then ingest the normalized output:

```powershell
docker compose run --rm api python scripts/catalogue_converters.py pubchem `
  --input /raw/pubchem_bulk/2026-07-24/approved-small-molecules.csv `
  --output data/processed/pubchem-approved-small-molecules.jsonl `
  --report data/processed/pubchem-reconciliation.json
```

```powershell
docker compose run --rm api python scripts/bulk_pipeline.py ingest-catalogue-jsonl `
  --input data/processed/pubchem-approved-small-molecules.jsonl `
  --source pubchem_bulk `
  --release 2026-07-01
```

PubChem, UniChem, and EMA are identity/regulatory enrichers. Their converters
set `requires_existing_drug`; they cannot create an unapproved or unresolved
drug entity. FDA and ChEMBL establish the approved small-molecule catalogue
first. The EMA converter accepts the official medicine-pages JSON file directly
and excludes veterinary, explicitly biologic, vaccine, and advanced-therapy
records before matching.

Ingest a pinned SureChEMBL directory after all four required Parquet files are present:

```powershell
docker compose run --rm api python scripts/bulk_pipeline.py ingest-surechembl `
  --snapshot /raw/surechembl_bulk/2026-07-21 `
  --release 2026-07-21
```

The command uses DuckDB for the multi-gigabyte joins, streams result batches into SQLite, records artifact checksums, and refreshes `drug_coverage`. It is idempotent and safe to rerun for the same release. `exact_structure` and `same_connectivity` matches remain `needs_review` patent candidates.

The standalone SQL template [`sql/duckdb/build_surechembl_candidates.sql`](../sql/duckdb/build_surechembl_candidates.sql) remains useful for warehouse exploration, but the Python command is the supported ingestion path.

The result is a candidate table, not gold data. It must still be joined to full patent text and curated.

After the Colab worker produces `result.json`, `result.md`, and `result.txt`, register the result without accepting any chemistry:

```powershell
docker compose run --rm api python scripts/ingest_ocr_result.py `
  --result /ocr/<job-id>/result.json `
  --publication US-1234567-A1 `
  --source-document /raw/patent_fulltext/<release>/US-1234567-A1.pdf
```

This creates only an `unreviewed` extraction job. It creates no evidence span or route until a reviewer records exact page/paragraph locations.

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
  --root "I:/My Drive/RXN2/data/raw/surechembl_bulk/2026-07-21" `
  --output data/manifests/surechembl-2026-07-21.json
```

The dataset version later references this manifest plus parser, policy, curation, and split manifests.

## 8. Verify coverage

```powershell
npm run catalogue:coverage
npm run catalogue:summary
```

The API exposes `GET /api/catalogue/coverage` and `GET /api/catalogue/drugs/{drug_id}`. A drug advances only from recorded local evidence. SureChEMBL association alone can produce `patents_found`; it cannot produce `examples_extracted` or an accepted route.
