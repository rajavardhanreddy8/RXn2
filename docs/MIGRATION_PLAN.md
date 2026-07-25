# Migration and restructuring plan

No original project files were present in the assigned workspace on 2026-07-22. The safe migration plan is therefore staged around this data core.

## Phase 0 — recover the current product

Place the existing repository in this workspace, including its README, dependency manifests, environment example, migrations, tests, and any model/data scripts. Then inventory:

- user-visible product ideas and workflows;
- domain entities and decisions embedded in code;
- live API calls and credentials;
- dataset files and undocumented transforms;
- model checkpoints, feature code, and evaluation logic;
- frontend/backend coupling and duplicate business rules.

Write an idea-to-component map before deleting or renaming anything.

## Phase 1 — adopt the evidence domain

- Move stable identifiers/enums/policies from this root `src/` into `packages/domain`.
- Put the SQL schema behind a repository layer.
- Adapt existing drug/patent concepts to typed identity/link records.
- Import existing data through a source adapter; do not copy opaque derived tables directly into gold data.

## Phase 2 — isolate acquisition

- Move every external call into `pipelines/acquire`.
- Replace runtime calls with scheduled/operator-run snapshot creation.
- Record release, URL, timestamp, checksum, size, terms, and credentials-required flag.
- Add fixtures and contract tests for each parser.

## Phase 3 — rebuild the training path

- Publish one immutable dataset version from accepted curation.
- Run family/active-moiety/temporal leakage scans.
- Reproduce existing model metrics on the frozen split.
- Keep a rules baseline and compare slice errors.
- Retire any model whose training provenance cannot be reconstructed.

## Phase 4 — reattach the application

- Expose a local query/inference API with stable DTOs.
- Show source passages, identity relationship, scale basis, confidence, and caveats in the UI.
- Make absence/uncertainty visible.
- Add end-to-end tests that work with network disabled.

## Phase 5 — controlled expansion

- Add concurrent curation and PostgreSQL only when needed.
- Add EPO OPS or licensed PCT feeds as optional adapters.
- Add new drug modalities in separate schemas/policies where small-molecule assumptions fail.
- Promote a new dataset/model only after quality, leakage, license, and scientific review gates pass.

## Acceptance criteria for the full restructure

- The product starts and serves existing evidence without internet access.
- No UI component calls an external patent/drug/AI service directly.
- Every prediction names its dataset/model version and traceable evidence.
- Raw sources can be re-parsed without changing them.
- All derived data can be rebuilt from manifests.
- Existing core user ideas are mapped to tested workflows.
- Dataset and model evaluation has no known family/active-moiety/near-duplicate leakage.
- Restricted sources are technically prevented from entering redistributable exports.
