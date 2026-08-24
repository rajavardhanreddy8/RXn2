#!/usr/bin/env python3
"""Show live progress written by the RXN2 overnight Colab runner."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def show(root: Path) -> None:
    status = read_json(root / "status.json")
    completed = status.get("completed", 0)
    total = status.get("total", "?")
    print(
        f"RXN2 overnight extraction | completed: {completed}/{total} | "
        f"remaining: {status.get('remaining', '?')} | "
        f"state: {status.get('state', 'not started')} | "
        f"rate/min: {status.get('procedures_per_minute', '-')} | "
        f"ETA: {status.get('estimated_finish_at', '-')} | "
        f"updated: {status.get('updated_at', '-')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=Path(r"I:\My Drive\RXN2\relation-extraction\overnight-v2"),
    )
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--seconds", type=int, default=15)
    args = parser.parse_args()
    while True:
        show(args.root)
        if not args.watch:
            return
        time.sleep(max(2, args.seconds))


if __name__ == "__main__":
    main()