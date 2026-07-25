# Model-training plan

Training comes after the evidence dataset passes curation and leakage gates.

## Model sequence

1. **Passage retrieval** — rank example/preparation paragraphs likely to contain executed process evidence.
2. **Structured extraction** — identify materials, roles, amounts, units, operations, conditions, yield, purity, and cross-references.
3. **Identity/link ranking** — score same active moiety and comparable route/step candidates.
4. **Scale classification/regression** — predict the versioned band and/or continuous log mass only when a target is useful and supported.
5. **Scale-change risk models** — later, predict outcome changes across comparable steps; do not attempt this from sparse patent labels initially.

The first useful models reduce curator workload. They should not pretend to replace process-development judgment.

## Pretraining and weak supervision

- Lowe USPTO reactions: reaction/paragraph retrieval and reaction representation; deduplicate by patent family, paragraph, and reaction signature.
- Open Reaction Database: amount/condition/workup/outcome representation and validation.
- SureChEMBL: patent–compound candidate generation and biomedical retrieval.
- ChEMBL/PubChem: identifier and synonym features with source-level provenance.

Weak labels remain marked as weak. They cannot become gold labels merely by passing through another model.

## Gold tasks and metrics

| Task | Split unit | Primary metrics |
| --- | --- | --- |
| Evidence passage retrieval | patent family + active moiety | Recall@k, precision@k |
| Entity/amount extraction | patent family | span F1, normalized value/unit exact match |
| Performed vs prophetic/generic | patent family + time | macro F1, calibration |
| Same-drug link | active moiety | precision/recall by link type |
| Comparable-step link | active moiety + route | PR-AUC, Precision@k |
| Scale band | active moiety + family | macro F1, calibration, confusion by adjacent band |
| Continuous scale | active moiety + family | MAE on log10(g), interval coverage |

Report uncertainty and per-source/per-era slices. OCR quality, jurisdiction, chemistry class, and source schema are expected failure dimensions.

## Required splits

- **Family holdout:** no priority/simple family crosses train and evaluation.
- **Active-moiety holdout:** evaluates transfer to unseen drugs; no form/salt/prodrug shortcut leakage.
- **Temporal holdout:** train on publications before a cutoff and test after it.
- **Near-duplicate guard:** MinHash/text/reaction fingerprints prevent copied examples from crossing splits.
- **Assignee slice:** measure whether a model is learning house drafting style rather than chemistry.

The included CLI creates deterministic group splits for early data checks. Production publishing must compute connected components across family, active moiety, and near-duplicate edges before assigning a split.

## Training gate

Do not start a final supervised run until:

- the production dataset contains no synthetic examples;
- every positive has accepted evidence and mass basis;
- reviewer agreement is reported by task and link type;
- negative examples include hard name/structure/family confounders;
- leakage scans pass;
- source and license manifests are complete;
- a simple rules/retrieval baseline is frozen;
- an error-analysis template and rollback path exist.

## Baselines first

Before fine-tuning a large model, benchmark:

- deterministic quantity/unit extraction;
- BM25/keyword retrieval over example sections;
- structure/connectivity/active-moiety rules;
- gradient-boosted or logistic link ranking on interpretable features;
- calibrated ordinal classification for scale band.

If a learned system cannot beat these baselines on leakage-safe evaluation, it should not be deployed.

## Model artifacts

Every run records:

- dataset manifest SHA-256 and split policy;
- source-release set;
- code commit/package lock;
- feature/parser/model versions;
- hyperparameters and random seeds;
- metrics and slice metrics;
- accepted use and prohibited use;
- model-card limitations and reviewer sign-off.
