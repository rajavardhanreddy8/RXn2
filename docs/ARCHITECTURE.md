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
- **Local transient space:** at most 5 GB, with a 5 GB free-space floor; never used for bulk patent or ChEMBL snapshots.
- **Cloud working analytics:** Colab/cloud DuckDB reads Drive Parquet and emits compact, checksummed JSONL imports.
- **Curated transactional evidence:** SQLite for one researcher; PostgreSQL when concurrent reviewers are introduced.
- **Training:** immutable Parquet/JSONL shards and a dataset manifest.
- **Serving:** read-only SQLite/PostgreSQL plus local model artifacts.

SQLite is implemented now because it is portable and testable without external services. Do not load full SureChEMBL into SQLite; cloud DuckDB filters its Drive-backed Parquet snapshots and local RXN2 inserts only the candidate/curated slice.
The pipeline fails closed when Drive is unavailable, a local free-space floor would be crossed, or a raw input is too large for bounded direct streaming. SQLite stays local. Cloud DuckDB temporary files live in the cloud runtime, not on the Windows machine or inside Drive.

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
