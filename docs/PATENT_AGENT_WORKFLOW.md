# RXN2 patent-agent workflow

The agents accelerate acquisition and review, but they do not replace dataset
quality gates. Only a bounded manifest controls network acquisition, and LLM output
never becomes a human curation decision.

## Roles

1. **Acquisition agent** downloads only publications listed in an approved JSON
   manifest. It uses authenticated EPO OPS endpoints, verifies the existing artifact
   manifests, resumes completed publications, retries transient failures, and records
   state. It never scrapes a search interface or discovers arbitrary publications.
2. **Deterministic example extractor** reads frozen native `description.xml` files,
   separates experimental example blocks, and records paragraph labels, source paths,
   and SHA-256 hashes. It creates no reaction records.
3. **Source-completeness reviewer** extracts only explicit materials, conditions,
   quantities, outcomes, and missing fields with verbatim quotes.
4. **Adversarial chemistry reviewer** independently checks for missed starting
   materials, role mistakes, salt/stereochemistry ambiguity, references to other
   examples, and route gaps.
5. **Local validator** rejects any LLM item whose quote is not an exact substring of
   the frozen evidence. It compares the two agents and sends all disagreement to a
   human review queue.

Two reviews made by the same model are correlated automated opinions. They never
count as two independent human reviewers and cannot promote a route to gold.

## Run a bounded acquisition

Run modules from the repository root so RXN2 imports are available:

```powershell
python -m scripts.run_epo_acquisition_agent `
  --batch data/processed/manifests/process-patent-epo-retry-20260826.json `
  --output data/raw/epo_ops/process-patent-batch-20260826 `
  --include-description `
  --retry-rounds 3
```

The state file is written under the output directory. HTTP 404 and invalid
publication numbers are permanent failures; rate limits and server errors are
retried with a bounded delay.

## Extract native example blocks

```powershell
python scripts/extract_epo_example_blocks.py `
  --input data/raw/epo_ops/process-patent-batch-20260826-complete `
  --output data/processed/epo_ops/process-patent-examples-20260830
```

The current 30-document snapshot produced 243 unreviewed example blocks from 29
documents with no XML parse failures.

## Run a small LLM pilot

```powershell
python -m scripts.review_patent_examples_with_llm `
  --example-blocks data/processed/epo_ops/process-patent-examples-20260830/example_blocks.jsonl `
  --output data/processed/review/process-patent-agent-pilot-20260830 `
  --model nvidia/nemotron-3-ultra-550b-a55b:free `
  --limit 1
```

RXN2 keeps OpenRouter `data_collection=deny`. As of 2026-08-30, OpenRouter lists
the requested Nemotron free model, but its free endpoint does not match that privacy
policy. The runner therefore records a provider failure instead of weakening the
policy. A compatible free endpoint or explicit project-level privacy decision is
required before scaling the LLM stage.

## Promotion rule

Agent agreement means only `agent_agreement_candidate`. The record remains
`needs_human_review`. Gold promotion still requires deterministic chemistry gates,
source verification, and two distinct human reviewer identities through the existing
curation-decision workflow.
