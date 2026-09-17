"""Export a readable side-by-side conflict report for patent review proposals."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / 'data/processed/review/process-patent-groq-qwen-full-20260831/human-review-queue.jsonl'
OUTPUT = QUEUE.with_name('human-review-conflicts.md')

def compact_materials(review):
    return '; '.join(f"{m.get('surface_text')} [{m.get('role')}]" for m in review.get('materials', [])) or '(none reported)'

def compact_facts(review):
    return '; '.join(f"{f.get('field')}={f.get('value_text')}" for f in review.get('facts', [])) or '(none reported)'

def main():
    rows = [json.loads(line) for line in QUEUE.read_text(encoding='utf-8').splitlines() if line.strip()]
    lines = [
        '# RXN2 patent-review conflicts',
        '',
        'Each entry shows the two agent proposals side by side. These are proposals for human review, not accepted chemistry.',
        '',
    ]
    for index, row in enumerate(rows, 1):
        source, proposal = row['source'], row['proposal']
        a, b = proposal['reviews']['source_completeness'], proposal['reviews']['adversarial_chemistry']
        lines += [
            f"## {index}. {source['publication_number']} — {source['heading']}",
            '',
            f"**Conflict flags:** {', '.join(proposal['consensus']['disagreements'])}",
            '',
            '| Field | Agent A — source completeness | Agent B — chemistry challenge |',
            '|---|---|---|',
            f"| Procedure type | `{a.get('procedure_type')}` | `{b.get('procedure_type')}` |",
            f"| Materials and roles | {compact_materials(a)} | {compact_materials(b)} |",
            f"| Facts and conditions | {compact_facts(a)} | {compact_facts(b)} |",
            f"| Missing fields | {', '.join(a.get('missing_fields') or []) or '(none)'} | {', '.join(b.get('missing_fields') or []) or '(none)'} |",
            f"| Issues | {', '.join(a.get('issues') or []) or '(none)'} | {', '.join(b.get('issues') or []) or '(none)'} |",
            f"| Recommendation | `{a.get('recommendation')}` | `{b.get('recommendation')}` |",
            '',
            f"**Agent A rationale:** {a.get('rationale', '')}",
            '',
            f"**Agent B rationale:** {b.get('rationale', '')}",
            '',
            f"**Patent source:** `{source.get('source_artifact')}`; paragraphs `{', '.join(source.get('paragraph_labels') or [])}`.",
            '',
        ]
    OUTPUT.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'records': len(rows), 'output': str(OUTPUT)}))

if __name__ == '__main__':
    main()
