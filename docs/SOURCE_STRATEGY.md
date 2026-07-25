# Patent and drug source strategy

## Recommended source stack

The core is bulk-first and local. APIs are optional acquisition adapters, never runtime dependencies.

| Priority | Source | Use | Decision |
| --- | --- | --- | --- |
| 1 | SureChEMBL bulk | Worldwide patent–compound candidates, family IDs, CPC/IPC, biomedical annotations | Primary open discovery backbone |
| 1 | FDA Orange Book | High-precision approved product–U.S. patent seeds | Primary regulatory seed |
| 1 | USPTO full-text XML | Procedure/example passages, quantities, yields, claims and descriptions for U.S. publications | Primary U.S. evidence text |
| 2 | Drugs@FDA | Approval/submission milestones and documents | Regulatory timeline enrichment |
| 2 | ChEMBL + selected PubChem records | Structure, active-moiety, synonym, drug/target cross-references | Identity enrichment with license isolation |
| 2 | PatentsView | U.S. assignee/inventor/entity resolution and long-text research files | Enrichment; not the official record |
| 2 | ORD + Lowe USPTO reactions | Reaction representation, pretraining, paragraph candidates | Pretraining only, not scale truth |
| 3 | ClinicalTrials.gov | Trial-phase timeline | Stage enrichment only |
| 3 | EPO OPS | Family/legal-status and non-U.S. backfill | Optional authenticated adapter |
| 3 | Google Patents BigQuery | Worldwide bibliographic/similarity candidate export | Optional reproducible export |
| Excluded from automation | WIPO PATENTSCOPE public site | Manual verification | Terms prohibit automated queries, bulk storage, and scraping |

The full machine-readable registry is [`configs/sources.json`](../configs/sources.json).

## Why SureChEMBL is the best starting point

SureChEMBL publishes biweekly Parquet snapshots containing compounds, patents, patent–compound mappings, classifications, family IDs, and biomedical annotations. It is CC BY 4.0 and supports local analysis. As of the 2026-07-17 snapshot, its largest core files are several gigabytes each, so ingestion should be columnar and filtered before copying into the curated store.

References:

- [SureChEMBL bulk schema and release policy](https://chembl.gitbook.io/surechembl/downloads/bulk-data)
- [SureChEMBL bulk directory](https://ftp.ebi.ac.uk/pub/databases/chembl/SureChEMBL/bulk_data/)
- [SureChEMBL license](https://ftp.ebi.ac.uk/pub/databases/chembl/SureChEMBL/bulk_data/LICENCE)

SureChEMBL is a candidate generator. A chemical mention in a description, image, or claim is not proof of an executed synthesis or its scale.

## Seed-to-evidence acquisition flow

1. **Seed drug entities.** Load Orange Book `Products.txt` and `Patent.txt`; normalize application/product/patent numbers.
2. **Resolve chemical identity.** Map ingredient names to curated structures and active moieties using ChEMBL and provenance-preserving PubChem records.
3. **Expand patent candidates.** Query the local SureChEMBL snapshot by full InChIKey, connectivity block, curated active-moiety relationships, Orange Book patent, and patent family.
4. **Keep similar compounds separate.** Fingerprint/substructure neighbours become `analogue` candidates; they never become same-drug records automatically.
5. **Acquire evidence text.** Pull the relevant U.S. publication/grant XML from a frozen USPTO bulk release. Use licensed EPO/WIPO products only when non-U.S. full text is required.
6. **Find example passages.** Detect example/preparation sections, quantities, outcomes, operations, and cross-references. Save paragraph IDs and offsets.
7. **Resolve reactions and routes.** Normalize reactants/products and link comparable transformations across documents.
8. **Curate.** A reviewer accepts/rejects identity, performed-vs-generic status, quantities, route links, and scale labels.
9. **Freeze a dataset version.** Hash artifacts, parsers, policy, accepted labels, and split assignments.

This staged approach avoids downloading and parsing the entire worldwide full-text corpus before the project has a useful drug list.

## Identity levels

Use a typed graph rather than one overloaded "same drug" flag:

| Relationship | Minimum evidence | May be used as same drug? |
| --- | --- | --- |
| `exact_structure` | Full standardized InChIKey/structure match | Yes, after standardization review |
| `same_connectivity` | First InChIKey block/structure connectivity | Candidate only; stereochemistry/isotope/charge may differ |
| `active_moiety` | Curated parent–salt/solvate relationship | Yes for program grouping; keep material form separate |
| `prodrug_of` | Curated biochemical relationship | No; related program entity |
| `analogue_of` | Fingerprint/substructure similarity | No |
| `name_only` | Synonym or string match | No |
| `regulatory_patent` | Orange Book application/product/patent relationship | Strong program seed, not proof that every patent example makes the marketed material |

## What counts as scale-up evidence

Two steps are a scale-up pair only when all of these are supported:

- accepted identity link for the target material;
- comparable transformation or process step;
- normalized mass basis for both observations;
- evidence that each passage reports an executed/historical example, not only a generic or prophetic instruction;
- a material scale change, initially proposed as at least 10×;
- no unresolved unit, OCR, yield, or paragraph-reference conflict.

Store the scale factor as a continuous value. The 10× rule is a versioned curation filter, not a scientific law.

## Source-specific constraints

- [USPTO Open Data Portal](https://data.uspto.gov/) now requires registration and an API key for its bulk-data API. Operators can download releases; credentials never enter the serving system.
- [USPTO XML resources](https://www.uspto.gov/learning-and-resources/xml-resources) document multiple historical schemas. The parser must record DTD/version per document.
- [PatentsView](https://www.uspto.gov/ip-policy/economic-research/patentsview) explicitly describes its data as research-grade rather than the official USPTO record.
- [Orange Book data](https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files) provides product, patent, and exclusivity files, updated monthly.
- [Drugs@FDA](https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files) provides a downloadable relational set and is updated on weekdays.
- [ClinicalTrials.gov](https://clinicaltrials.gov/data-api/how-download-study-records) supports JSON/CSV exports. Trial phase is sponsor-submitted context, not a manufacturing-scale label.
- [PubChem bulk downloads](https://pubchem.ncbi.nlm.nih.gov/docs/downloads) are available, but contributor-level provenance and license restrictions must be retained.
- [EPO OPS](https://www.epo.org/en/searching-for-patents/data/web-services/ops) is authenticated and free up to its stated weekly threshold; its terms limit redistribution of raw data.
- [WIPO PATENTSCOPE terms](https://www.wipo.int/en/web/patentscope/data/terms_patentscope) forbid automated queries, bulk acquisition/storage, and scraping on the public service. Use [licensed PCT data products](https://www.wipo.int/en/web/patentscope/data/index) instead.
- [Google Patents Public Datasets](https://cloud.google.com/blog/topics/public-datasets/google-patents-public-datasets-connecting-public-paid-and-private-patent-data) can support a one-time candidate export, but table-specific licensing and query provenance must be recorded.

## Existing training data

- [Lowe USPTO reactions](https://figshare.com/articles/dataset/Chemical_reactions_from_US_patents_1976-Sep2016_/5104873) is CC0 and includes patent number, paragraph number where available, year, text-mined yield, and calculated yield. Its own notes warn about frequent duplicates and incorrect atom maps.
- [Open Reaction Database](https://docs.open-reaction-database.org/en/stable/schema.html) captures amounts, reaction conditions, setup, workup, outcomes, and provenance. It is useful as a representation/pretraining corpus, but is not a labeled patent scale-up set.

Both can improve extraction and reaction models. Neither should supply the final same-drug/across-scale labels without project curation.
