# RXN2 Project Log

## Current summary

- **Project description:** RXN2 is a provenance-first knowledge graph for approved chemical drugs, public patent evidence, experimental procedures, compounds, and manufacturing-route candidates. It serves chemists and process-development teams that need traceable route evidence without invented chemistry.
- **Goal and success criteria:** Cover every in-scope publicly identifiable approved non-biologic chemical drug with a recorded public-evidence outcome. A trusted route requires resolved structures, exact source evidence, chemically validated transformations, connected steps, and explicit human acceptance. Coverage gaps must be reported rather than filled by inference.
- **Scope and constraints:** Google Drive is authoritative for raw snapshots and extraction artifacts; local SQLite is authoritative for curated data. LLM output is provisional only. Formulation, dosage, purification, analytical, and other supporting manufacturing facts remain available in separate graph layers and do not become core synthesis edges. Confidential process and supplier data are out of scope unless lawfully supplied.
- **Current status:** **In progress.** The catalogue, patent index, evidence graph, large projection, chemistry-layer separation, element-conservation gate, and first atom-mapping batch exist. All 70 currently recorded performed procedures were scheduled for extraction; 69 completed through exact-evidence validation and one model-schema failure is quarantined for retry. The current phase is repairing unresolved material identities and connecting validated transformations into multi-step route candidates. Latest reconciliation: **2026-09-17 12:45 +05:30**.

## Checkpoints

| ID | Deliverable / acceptance condition | State | Evidence or blocker |
|---|---|---|---|
| C001 | Production drug catalogue with structures and regulatory provenance | In progress | Production DB contains 7,592 drugs, 6,810 compounds, and 100,077 regulatory products. Global-source completeness still requires a release/accounting audit. |
| C002 | Drug-linked public patent candidate index | Complete for current snapshot | 45,066 patent families, 46,490 publications, and 69,868 drug-patent candidates are present. Future releases remain incremental work. |
| C003 | Evidence-bounded procedure and relation extraction | In progress | Production database: 70 performed evidence spans were scheduled; 69 have completed outcomes producing 423 validated, 324 unresolved, and 310 rejected relations. One schema-format failure is quarantined. All non-rejected relations remain `needs_review`. |
| C004 | Rebuildable multidimensional graph projection | Complete for current database | `graph_node`: 215,451; `graph_edge`: 341,859. Projection is derived and must remain idempotent. |
| C005 | Chemistry-gated single-step reaction graph | In progress | 21 atom-mapping records are validated. Production reactions: 35 `needs_review`, 1 rejected, and 0 accepted. Thirteen candidate transformations still lack sufficient consumed atom sources; one self-transformation is excluded from route claims. |
| C006 | Evidence-backed multi-step compound-to-drug routes | In progress | Four document-ordered candidates satisfy the current atom-map and identity-continuity gates, but all remain `needs_review` pending chemistry adjudication. |
| C007 | Reviewed route benchmark and predictive comparison | Planned | No accepted chemistry yet; training or “best route” claims would be premature. |
| C008 | Supplier quotes and route costing | Deferred | Requires licensed or supplied commercial quotation data. |

## Next actions and open questions

1. Repair the one quarantined provider schema failure using the exact role vocabulary; do not bypass quote or offset validation.
2. Resolve the 13 missing consumed/product atom sources from existing evidence and compound records; do not rerun broad extraction unless deterministic resolution fails.
3. Rerun element conservation, atom mapping, and graph projection idempotently; preserve rejection reasons.
4. Produce a prioritized chemistry-review queue with atom changes, quantities, conditions, yields, and exact evidence shown together.
5. After review, promote only approved reaction instances; accepted chemistry remains zero until then.

Open question: qualified chemistry-review ownership and throughput have not been confirmed.

## History

### E0001 | 2026-09-06 16:49 +05:30 | Progress | Reconciled current RXN2 foundation
- Checkpoints: C001-C008
- Observed result: RXN2 has a large deterministic evidence projection and a validated first atom-mapping batch, but it does not yet have accepted or fully assembled multi-step chemistry routes.
- Evidence: `data/curated/rxn2-production.sqlite`, `data/curated/rxn2-provisional.sqlite`, `data/processed/atom-mapping/results.jsonl`, and commits `e6c53fb`, `e3db98b`, and `668caae`.
- Follow-up: Repair unresolved material identities first, then assemble and review multi-step routes.

### E0002 | 2026-09-17 12:45 +05:30 | Progress | Expanded production performed-procedure extraction
- Checkpoint: C003, C004, C006
- Observed result: 69 of 70 currently recorded performed procedures completed through the evidence-bounded relation extractor. The output added 423 validated, 324 unresolved, and 310 rejected relations; no relation or reaction was accepted automatically. The remaining record is a provider schema-format failure retained for retry.
- Evidence: `data/curated/rxn2-production.sqlite`, `data/processed/review/route-review-queue.jsonl`, and `data/processed/review/gold-curation-packets.md`.
- Follow-up: Repair the isolated schema failure, then continue deterministic atom-source resolution and chemistry review of the four route candidates.
