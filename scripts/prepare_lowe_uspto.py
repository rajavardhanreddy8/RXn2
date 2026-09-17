#!/usr/bin/env python3
"""Stream the pinned Lowe reaction-SMILES archives into a weak-training JSONL file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.acquire_lowe_uspto import ASSET_NAMES, RELEASE_ID, SOURCE_ID, hashes


def number(value: str) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def record(line: str, archive: str, line_number: int) -> dict | None:
    fields = line.rstrip("\r\n").split("\t")
    if not fields or not fields[0] or ">" not in fields[0]:
        return None
    values = (fields + [""] * 6)[:6]
    return {
        "source_id": SOURCE_ID,
        "source_release_id": RELEASE_ID,
        "source_record_id": f"{archive}:{line_number}",
        "reaction_smiles": values[0],
        "patent_number": values[1] or None,
        "paragraph_number": values[2] or None,
        "publication_year": values[3] or None,
        "text_mined_yield": number(values[4]),
        "calculated_yield": number(values[5]),
        "supervision_tier": "weak_pretraining",
        "is_synthetic": False,
    }


def rsmi_member(seven_zip: str, archive: Path) -> str:
    listing = subprocess.run([seven_zip, "l", "-slt", str(archive)], check=True, capture_output=True, text=True)
    members = [line.partition("=")[2].strip() for line in listing.stdout.splitlines() if line.startswith("Path = ")]
    matches = [member for member in members if member.lower().endswith(".rsmi")]
    if len(matches) != 1:
        raise RuntimeError(f"expected one .rsmi member in {archive.name}, found {len(matches)}")
    return matches[0]


def prepare(snapshot: Path, output: Path, seven_zip: str, limit: int | None = None) -> dict:
    metadata_path = snapshot / "release-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("source_id") != SOURCE_ID or metadata.get("release_id") != RELEASE_ID:
        raise RuntimeError("unexpected Lowe release metadata")
    listed = {item["name"]: item for item in metadata.get("files", [])}
    if set(listed) != set(ASSET_NAMES):
        raise RuntimeError("Lowe release metadata does not contain the required reaction-SMILES files")
    for name in ASSET_NAMES:
        path = snapshot / name
        md5, _ = hashes(path)
        if md5 != listed[name]["expected_md5"]:
            raise RuntimeError(f"Lowe source checksum mismatch: {name}")

    executable = shutil.which(seven_zip) or (seven_zip if Path(seven_zip).is_file() else None)
    if not executable:
        raise RuntimeError("7z is required to stream the Lowe .7z archives; install p7zip in Colab")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    count = skipped = 0
    with partial.open("w", encoding="utf-8", newline="\n") as destination:
        for name in ASSET_NAMES:
            archive = snapshot / name
            member = rsmi_member(executable, archive)
            process = subprocess.Popen([executable, "x", "-so", str(archive), member], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert process.stdout is not None
            for line_number, raw in enumerate(process.stdout, 1):
                item = record(raw.decode("utf-8", errors="replace"), name, line_number)
                if item is None:
                    skipped += 1
                    continue
                destination.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
                if limit and count >= limit:
                    process.terminate()
                    break
            stderr = process.communicate()[1].decode("utf-8", errors="replace")
            if process.returncode not in (0, -15):
                raise RuntimeError(f"7z failed for {name}: {stderr[-1000:]}")
            if limit and count >= limit:
                break
    partial.replace(output)
    _, output_sha256 = hashes(output)
    manifest = {
        "source_id": SOURCE_ID,
        "release_id": RELEASE_ID,
        "input_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        "output": str(output),
        "output_sha256": output_sha256,
        "records": count,
        "skipped_lines": skipped,
        "complete": limit is None,
        "supervision_tier": "weak_pretraining",
        "automatic_acceptance": False,
    }
    manifest_path = output.with_name("manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=Path(os.getenv("RXN2_RAW_ROOT", r"I:\My Drive\RXN2\data\raw")) / SOURCE_ID / RELEASE_ID)
    parser.add_argument("--output", type=Path, default=Path(os.getenv("RXN2_DRIVE_ROOT", r"I:\My Drive\RXN2")) / "data" / "processed" / SOURCE_ID / RELEASE_ID / "weak-reactions.jsonl")
    parser.add_argument("--seven-zip", default="7z")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.snapshot, args.output, args.seven_zip, args.limit), indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
