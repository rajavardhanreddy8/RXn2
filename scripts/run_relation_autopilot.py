#!/usr/bin/env python3
"""Unattended local supervisor for RXN2 Colab checkpoints and graph refresh."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_status(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    return (datetime.now(UTC) - datetime.fromisoformat(value)).total_seconds()


def run(args: argparse.Namespace) -> bool:
    status = read_status(args.run_root / "status.json")
    stale = age_seconds(status.get("updated_at"))
    supervisor = {
        "updated_at": datetime.now(UTC).isoformat(), "colab_state": status.get("state", "not_started"),
        "completed": status.get("completed", 0), "total": status.get("total"),
    }
    if stale is not None and stale > args.stale_minutes * 60 and status.get("state") not in {"completed"}:
        supervisor["state"] = "colab_restart_required"
        supervisor["reason"] = f"No Colab checkpoint update for {round(stale / 60, 1)} minutes"
    else:
        supervisor["state"] = "monitoring"
    manifests = list((args.run_root / "manifest").glob("part-*.json"))
    if manifests:
        command = [sys.executable, str(ROOT / "scripts" / "import_colab_relation_parts.py"), "--db", str(args.db), "--run-root", str(args.run_root), "--apply"]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        supervisor["import_exit_code"] = completed.returncode
        supervisor["import_output"] = (completed.stdout or completed.stderr)[-3000:]
    if status.get("state") == "completed":
        command = [sys.executable, str(ROOT / "scripts" / "build_relation_graph.py"), "export", "--db", str(args.db), "--output-dir", str(args.graph_output)]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        supervisor["graph_export_exit_code"] = completed.returncode
        supervisor["graph_export_output"] = (completed.stdout or completed.stderr)[-3000:]
        supervisor["state"] = "completed" if completed.returncode == 0 else "graph_export_failed"
    args.run_root.mkdir(parents=True, exist_ok=True)
    path = args.run_root / "supervisor-status.json"
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(supervisor, indent=2, sort_keys=True), encoding="utf-8")
    partial.replace(path)
    print(json.dumps(supervisor, indent=2, sort_keys=True))
    return supervisor["state"] == "completed"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "curated" / "rxn2-provisional.sqlite")
    parser.add_argument("--run-root", type=Path, default=Path(r"I:\My Drive\RXN2\relation-extraction\overnight-v2"))
    parser.add_argument("--graph-output", type=Path, default=ROOT / "data" / "processed" / "relation-graph" / "overnight-v2")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--stale-minutes", type=int, default=20)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        if run(args) or args.once:
            return
        time.sleep(max(15, args.seconds))


if __name__ == "__main__":
    main()
