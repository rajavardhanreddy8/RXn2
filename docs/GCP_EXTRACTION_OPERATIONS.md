# GCP Extraction Operations

## Pipeline
1. Publish a source-linked procedure passage.
2. Run bounded model extraction.
3. Preserve the raw response and token metadata.
4. Run deterministic evidence and chemistry checks.
5. Store candidates as `needs_review`.
6. Report queue health, cost, retries, and failures.

## Operating rules
- Do not accept chemistry automatically.
- Do not retry unknown model outcomes.
- Stop new inference when the allowance ledger is exhausted.
- Keep every result linked to its source passage and checksum.
