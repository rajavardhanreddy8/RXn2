# Drug scale-up patent data core

This repository is the data-first nucleus for the project. It keeps the product idea—learning how drug-development processes change across scale—while removing live third-party APIs from the runtime path.

The assigned workspace contained no existing project files, so this is a clean foundation rather than a code migration. The intended order is:

1. build a traceable patent/process dataset;
2. curate reliable same-drug and comparable-step links;
3. freeze leakage-safe training/evaluation datasets;
4. train and validate models;
5. put an application/API on top of the local evidence store.

## What exists now

- an offline SQLite evidence model in [`sql/schema.sql`](sql/schema.sql);
- a source registry that separates bulk snapshots from restricted/optional services;
- a scale policy that never confuses manufacturing scale with clinical stage;
- validation, deterministic split, ingest, manifest, and quality-check commands with no npm dependencies;
- synthetic examples that exercise the workflow without pretending to be real patent evidence;
- research and migration documents for the patent corpus, curation, and model-training plan.
- a normalized SQLite knowledge graph for compounds, reviewed reaction instances, evidence, hazards, suppliers and dated quotes;
- deterministic evidence-bounded route generation and transparent raw-material cost/feasibility scoring;
- a FastAPI/RDKit service and React/TypeScript local route explorer;
- an optional review-gated Qroq/Groq extraction adapter that is never required at runtime.
- a resumable global-drug catalogue pipeline for FDA, ChEMBL and normalized PubChem/UniChem/EMA records;
- bulk SureChEMBL Parquet matching that creates patent candidates without accepting chemistry;
- persisted per-drug coverage states exposed through the API and React interface.

Node 22.5+ is required because the CLI uses the built-in `node:sqlite` module. The current workspace has Node 24.

## Quick start

```powershell
node src/cli.js validate --input examples/curated_examples.jsonl
node src/cli.js init-db --db data/curated/rxn2-production.sqlite
node src/cli.js ingest --db data/curated/rxn2-demo.sqlite --input examples/curated_examples.jsonl
node src/cli.js split --input examples/curated_examples.jsonl --output data/processed/splits.json --seed fixture-12 --db data/curated/rxn2-demo.sqlite --dataset-version working-fixtures-v1
node src/cli.js quality --db data/curated/rxn2-production.sqlite
node --test
```

Copy `.env.example` to `.env`, confirm the Drive mount, then initialize the catalogue:

```powershell
npm run storage:check
npm run catalogue:init
npm run catalogue:summary
```

Google Drive is authoritative for raw snapshots and cloud-produced exchange
files. Multi-gigabyte ChEMBL/SureChEMBL work runs in Colab or another cloud
runtime; RXN2 imports only compact JSONL results into local SQLite. Windows may
directly stream bounded source files, but it never stages the bulk patent corpus.


### Unattended operation without GCP

RXN2 can run its bounded local stages through Windows Task Scheduler while
Google Drive remains the authoritative raw and processed-artifact store. Text
PDFs are extracted locally; scanned PDFs are queued for the existing Colab OCR
notebook. No machine-produced chemistry is accepted automatically.

```powershell
npm run automation:run
npm run automation:status
npm run automation:install
```

The scheduled task runs daily at 02:00, starts when the laptop next becomes
available, prevents overlapping runs, stops after two hours, and writes the
The checked-in examples are explicitly synthetic. They validate the machinery only; they are never eligible to support scientific conclusions.

## Run the MVP

The complete application is containerized because its chemistry service uses Python 3.12 and RDKit:

```powershell
docker compose up --build
```

Open `http://localhost:5173`. The API and generated OpenAPI documentation are at `http://localhost:8000/docs`.

Production uses `data/curated/rxn2-production.sqlite` and never seeds synthetic
records. The preserved `data/curated/rxn2-demo.sqlite` contains the bundled
**Demo benzamide target** and five benchmark compounds; select that database
and set `RXN2_SEED_DEMO=true` only for explicit demo/test runs. No demo record
supports a scientific or cost-reduction claim.

Run verification with:

```powershell
npm test
npm --prefix apps/web run build
docker compose run --rm api pytest -q apps/api/tests
```

`GROQ_API_KEY` is optional. When absent, `/api/extraction/qroq` returns a controlled disabled response and all core functions continue locally. When enabled, extracted facts enter `needs_review`; they never enter the accepted graph automatically.

Catalogue ingestion does not download or rewrite source files. It verifies
cloud-produced import artifacts, streams SHA-256 in bounded memory, and records
release artifacts in the evidence database before parsing.

## Operating rule

Production serving reads local, versioned SQLite/Parquet/model artifacts. External systems are acquisition sources only. Every acquisition is retained as an immutable snapshot with release date, checksum, source terms, and parser version. Live API availability cannot change a model prediction or a user query result.

Start with:

- [`docs/PROJECT_PRINCIPLES.md`](docs/PROJECT_PRINCIPLES.md)
- [`docs/SOURCE_STRATEGY.md`](docs/SOURCE_STRATEGY.md)
- [`docs/ACQUISITION_RUNBOOK.md`](docs/ACQUISITION_RUNBOOK.md)
- [`docs/DATASET_DESIGN.md`](docs/DATASET_DESIGN.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/MODEL_TRAINING.md`](docs/MODEL_TRAINING.md)
- [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md)
