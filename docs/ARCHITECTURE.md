# Target architecture

## Dependency direction

```text
bulk/manual snapshots
        |
        v
raw artifacts + manifests
        |
        v
source parsers -> canonical evidence DB -> curation queue
                                         |
                                         v
                                frozen ML datasets
                                         |
                                         v
                                  trained models
                                         |
                                         v
                            local query/API -> UI
```

Nothing below the canonical evidence database calls a source API. The UI never calls patent, FDA, chemistry, or trial providers directly.

## Repository shape after the existing application is supplied

```text
apps/
  api/                  # local query and inference boundary
  web/                  # evidence-first review and exploration UI
packages/
  domain/               # stable IDs, enums, validation, policies
  evidence-store/       # SQLite/Postgres query layer
pipelines/
  acquire/              # operator-run snapshot adapters
  parse/                # source-specific, versioned parsers
  normalize/            # units, patent IDs, structures, names
  link/                 # candidate generation and scoring
  curate/               # review imports/exports and adjudication
  publish/              # dataset freeze, manifests, Parquet exports
training/
  extraction/
  linking/
  scale/
configs/
sql/
docs/
data/                   # raw/parsed/curated/processed, mostly ignored
```

The current root-level `src/` is a zero-dependency bootstrap for `packages/domain` plus `pipelines/publish`. It can move mechanically once the original application code is available.

## Storage choices

- **Raw:** original ZIP/XML/Parquet/JSON files under the Google Drive `RXN2/data/raw` root; content addressed by SHA-256.
- **Local cache:** rebuildable staged inputs, capped at 250 GB while retaining at least 100 GB or 10% free disk space.
- **Working analytics:** Parquet plus DuckDB for large SureChEMBL/USPTO joins.
- **Curated transactional evidence:** SQLite for one researcher; PostgreSQL when concurrent reviewers are introduced.
- **Training:** immutable Parquet/JSONL shards and a dataset manifest.
- **Serving:** read-only SQLite/PostgreSQL plus local model artifacts.

SQLite is implemented now because it is portable and testable without external services. Do not load full SureChEMBL into SQLite; filter/join its Parquet snapshots with DuckDB and insert only the candidate/curated slice.
The pipeline fails closed when Drive is unavailable or the cache/free-space limits would be crossed. SQLite and DuckDB working databases must never be placed on Drive.

## Stable boundaries

### Source adapter

Input: operator-provided snapshot and release metadata.

Output: artifact manifest and source-shaped parsed records. It does not emit training labels.

### Canonicalizer

Input: parsed records.

Output: normalized identifiers/facts with reversible references to source values. It never discards the original unit or name.

### Linker

Input: canonical drugs, compounds, patents, and steps.

Output: typed scored candidates plus features and model version. It never silently merges entities.

### Curator

Input: candidate and exact evidence.

Output: append-only decisions, reviewer, rationale, and supersession relationship.

### Dataset publisher

Input: accepted decisions and a policy version.

Output: immutable examples, split assignments, statistics, manifest, and license/attribution bundle.

## External-service policy

External services may be used when all of the following hold:

- the source terms permit the action;
- the result can be snapshotted and checksummed;
- the pipeline has a manual/bulk alternative or can mark the source optional;
- failure does not break training, serving, or reproducibility;
- credentials remain outside source control and output artifacts.

WIPO PATENTSCOPE public-site automation fails this policy and is excluded. EPO OPS and Google BigQuery are optional adapters. USPTO ODP credentials are acquisition-only.
