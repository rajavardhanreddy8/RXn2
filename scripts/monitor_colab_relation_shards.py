#!/usr/bin/env python3
"""Display aggregate live status for sharded RXN2 Colab relation extraction."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def status(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def show(root: Path) -> None:
    items = [status(path) for path in sorted((root / "status").glob("shard-*.json"))]
    if not items:
        print("RXN2 sharded extraction: no active shard status files yet")
        return
    completed = sum(item.get("completed", 0) for item in items)
    total = sum(item.get("total", 0) for item in items)
    print(f"RXN2 shards | completed {completed}/{total} | shards {len(items)}")
    for item in items:
        print(f"  {item.get('shard')}: {item.get('state')} {item.get('completed', 0)}/{item.get('total', 0)} errors={item.get('errors', 0)} updated={item.get('updated_at', '-')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(r"I:\My Drive\RXN2\relation-extraction"))
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
