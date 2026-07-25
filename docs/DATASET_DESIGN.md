# Canonical dataset design

## Unit of analysis

The primary curated record is a **process-step evidence observation**:

> one reported process step, for one target material, supported by one exact patent passage, with normalized quantities/outcomes and a reviewed identity link.

A patent is not a row, and a drug is not a row. Both contain many observations at different grains.

## Layers

1. **Raw** — byte-identical downloaded artifacts plus acquisition manifests.
2. **Parsed** — source-shaped XML/Parquet/text records with no semantic overwrites.
3. **Canonical** — normalized patents, families, compounds, active moieties, passages, steps, quantities, events, and typed links.
4. **Curated** — accepted/rejected review decisions and conflicts.
5. **ML** — immutable task-specific examples, labels, features, and splits.

Never overwrite an earlier layer. Corrections create a new parser, curation event, or dataset version.

## Core entities

The executable schema is [`sql/schema.sql`](../sql/schema.sql). Important entities are:

- `source`, `source_release`, `artifact`: acquisition provenance and checksums;
- `patent_document`, `patent_family`, `patent_family_member`: publication/family identity;
- `compound`, `active_moiety`, `compound_relationship`: chemical identity graph;
- `drug_entity`, `regulatory_event`, `clinical_event`: program/stage context;
- `evidence_span`: exact source location and reviewed text;
- `process_route`, `process_step`, `quantity_observation`, `outcome_observation`: process facts;
- `link_candidate`, `curation_decision`: machine proposal and human decision;
- `scale_label`: versioned derived label plus separate development stage;
- `dataset_version`, `dataset_example`, `dataset_split`: frozen ML material;
- `quality_result`: machine-readable quality evidence.

## Scale policy

The initial operational bands are defined in [`configs/scale_policy.json`](../configs/scale_policy.json):

| Band | Product/batch mass basis |
| --- | ---: |
| `sub_gram` | < 1 g |
| `laboratory` | 1 to < 100 g |
| `kilo_lab` | 100 g to < 10 kg |
| `pilot` | 10 to < 100 kg |
| `manufacturing` | ≥ 100 kg |

These thresholds are initial dataset policy, not universal industry or regulatory definitions. The continuous normalized value is authoritative. A future policy can relabel rows without re-extracting source facts.

Basis preference is isolated product mass, then explicit batch mass, limiting-reagent mass, and largest input mass. Vessel volume alone cannot assign a band.

## Development stage

Allowed stage labels are `discovery`, `preclinical`, `phase_1`, `phase_2`, `phase_3`, `approved`, `post_approval`, and `unknown`.

A non-unknown stage requires its own dated source such as a trial record or regulatory event. It must not be inferred from patent scale, assignee, publication date, or marketing status of a different formulation.

## Evidence status

Each passage should be classified as one of:

- `performed`: wording and context report an executed result;
- `historical`: a prior process is described with traceable source context;
- `prophetic`: an example is drafted as expected/planned;
- `generic`: broad instruction or claim without a specific execution;
- `ambiguous`: evidence is insufficient.

Only accepted `performed` or carefully reviewed `historical` observations enter supervised scale-up labels by default.

## Comparable-step links

Route/step comparison uses:

- product active-moiety/material-form identity;
- reaction-center or transformation fingerprint;
- precursor connectivity;
- step order and named intermediate;
- reagent/solvent/operation profile;
- patent example cross-references;
- assignee, inventor, priority/family, and chronology as supporting—not decisive—features.

A model emits a scored `link_candidate`. A reviewer decision produces the label. Family membership alone cannot establish comparable chemistry.

## Minimum provenance for training eligibility

Every accepted example must have:

- source and release identifier;
- artifact SHA-256;
- publication number and family identifier when available;
- paragraph/section and character offsets;
- evidence-text SHA-256;
- extraction method and parser/model version;
- normalized quantity with original value/unit preserved;
- typed compound/active-moiety link;
- evidence status and reviewer decision;
- license/redistribution class;
- split group(s).

## Quality gates

Critical failures:

- duplicate accepted example IDs;
- missing source/artifact/evidence hash;
- invalid or negative quantities;
- accepted scale label without a valid mass basis;
- a clinical stage inferred from scale;
- orphan evidence/step/compound relationships;
- one patent family or active moiety present in incompatible evaluation splits;
- synthetic examples in a production dataset version.

High failures:

- exact-structure claim based only on a name;
- evidence offset/hash disagreement;
- yield or purity outside allowed bounds without a documented exception;
- accepted performed example whose wording is prophetic/generic;
- unreviewed parser/model version or source-schema drift.

## Curation tranche

Start narrow enough to audit:

- 25 active moieties covering small molecules, salts/solvates, and multiple assignees;
- at least five candidate families per active moiety where available;
- two independent reviewers for identity and comparable-step links;
- a minimum of 500 accepted process-step observations before training an extraction/link model for evaluation;
- explicit negative candidates: similar structures, family-only matches, name collisions, and generic/prophetic examples.

These are project targets, not claims about statistical sufficiency. Expand only after inter-reviewer agreement and error analysis are acceptable.
