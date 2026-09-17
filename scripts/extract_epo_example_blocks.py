#!/usr/bin/env python3
"""Extract performed-example candidate blocks from native EPO description XML.

The output is evidence only. It does not create reactions, resolve structures, or
accept chemistry. Every block retains its native paragraph labels and source hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


HEADING = re.compile(
    r"^(?:(?:reference|comparative)\s+)?(?:example|preparation|ejemplo|preparaci[oó]n|"
    r"beispiel|herstellung|exemple|pr[ée]paration)"
    r"(?:\s*[-.:]\s*|\s+)(?:\d+[A-Z]?|[IVXLC]+|[A-Z])\b",
    re.IGNORECASE,
)
PARAGRAPH_LABEL = re.compile(r"^\s*(\[\d{1,6}\])\s*")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(*parts: str) -> str:
    value = "\x1f".join(parts)
    return f"epo-example:{sha256_text(value)[:24]}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def normalized_text(element: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def publication_number(directory: Path) -> str:
    manifest = directory / "manifest.json"
    if manifest.is_file():
        value = json.loads(manifest.read_text(encoding="utf-8")).get("publication_number")
        if value:
            return str(value)
    return directory.name


def description_language(root: ET.Element) -> str | None:
    for element in root.iter():
        if local_name(element.tag) == "description":
            return element.attrib.get("lang")
    return None


def paragraph_records(root: ET.Element) -> list[dict]:
    records = []
    for element in root.iter():
        if local_name(element.tag) != "p":
            continue
        text = normalized_text(element)
        if not text:
            continue
        match = PARAGRAPH_LABEL.match(text)
        label = match.group(1) if match else None
        records.append({"label": label, "text": text})
    return records


def extract_blocks(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    publication = publication_number(path.parent)
    language = description_language(root)
    artifact_sha256 = sha256_file(path)
    blocks: list[dict] = []
    current: list[dict] = []
    heading = ""

    def flush() -> None:
        nonlocal current, heading
        if not current:
            return
        text = "\n".join(row["text"] for row in current)
        labels = [row["label"] for row in current if row["label"]]
        blocks.append(
            {
                "schema_version": "rxn2-epo-example-block-v1",
                "example_id": stable_id(publication, heading, text),
                "publication_number": publication,
                "source_publication_number": publication,
                "heading": heading,
                "language": language,
                "paragraph_labels": labels,
                "paragraph_start": labels[0] if labels else None,
                "paragraph_end": labels[-1] if labels else None,
                "text": text,
                "text_sha256": sha256_text(text),
                "source_artifact": str(path),
                "source_artifact_sha256": artifact_sha256,
                "extraction_method": "deterministic_native_xml",
                "review_status": "unreviewed",
                "human_review_required": True,
            }
        )
        current = []
        heading = ""

    for paragraph in paragraph_records(root):
        candidate = PARAGRAPH_LABEL.sub("", paragraph["text"], count=1).strip()
        match = HEADING.match(candidate)
        if match:
            flush()
            heading = match.group(0).strip()
            current = [paragraph]
        elif current:
            current.append(paragraph)
    flush()
    return blocks


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="EPO snapshot root")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    partial = Path(str(args.output) + ".partial")
    if args.output.exists() or partial.exists():
        raise FileExistsError(f"Refusing to overwrite output: {args.output}")
    paths = sorted(args.input.rglob("description.xml"))
    if not paths:
        raise SystemExit(f"No description.xml files found under {args.input}")

    partial.mkdir(parents=True)
    rows: list[dict] = []
    failures = []
    for path in paths:
        try:
            rows.extend(extract_blocks(path))
        except (ET.ParseError, OSError, ValueError) as error:
            failures.append({"source_artifact": str(path), "error": str(error)})
    output = partial / "example_blocks.jsonl"
    write_jsonl(output, rows)
    counts = Counter(row["publication_number"] for row in rows)
    manifest = {
        "schema_version": "rxn2-epo-example-extraction-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_root": str(args.input),
        "description_documents": len(paths),
        "documents_with_examples": len(counts),
        "example_blocks": len(rows),
        "failures": failures,
        "output_sha256": sha256_file(output),
        "safety": {
            "creates_reactions": False,
            "accepts_chemistry": False,
            "human_review_required": True,
        },
    }
    (partial / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(partial, args.output)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
