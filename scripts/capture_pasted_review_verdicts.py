"""Capture the user's pasted human verdicts as a readable RXN2 artifact."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATTACHMENT = Path(r"C:\Users\admin\.codex\attachments\e1620b2c-2c39-446d-b977-9e92f51bee9e\pasted-text.txt")
OUTPUT = ROOT / "data/processed/review/process-patent-groq-qwen-full-20260831/human-review-verdicts-21-55.md"

def main():
    lines = ATTACHMENT.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    out = [
        "# RXN2 human-review verdicts: records 21–55",
        "",
        "These verdicts were supplied by the human reviewer. They are preserved as review notes; they do not automatically approve gold chemistry.",
        "",
        "| # | Patent / example | Materials and roles verdict | Facts verdict | Reviewer flag |",
        "|---:|---|---|---|---|",
    ]
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) != 5:
            continue
        cells = [c.replace("|", "\\|").replace("\n", " ").strip() for c in cols]
        out.append("| " + " | ".join(cells) + " |")
    out += [
        "",
        "## Review rules and follow-up flags",
        "",
        "- Record 31: keep `procedure_type=purification` and `recommendation=ready_for_human_review`; preserve the non-synthetic taxonomy question.",
        "- Record 53: keep `ready_for_human_review`; preserve the stoichiometry/yield-to-intermediate mapping flag.",
        "- Records 45 and 51: keep phenyl chloroformate role ambiguity as a shared convention decision.",
        "- Record 24: fill the missing paragraph citation before finalization.",
        "- Record 12: decide whether the Ex.14–17 scope should be split into separate records.",
        "- Records 30, 32, 33, 41 and 42: targeted rerun may be useful because operation verbs were systematically dropped.",
    ]
    OUTPUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"captured {len(out)-8} verdict rows to {OUTPUT}")

if __name__ == "__main__":
    main()
