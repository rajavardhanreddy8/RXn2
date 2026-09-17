#!/usr/bin/env python3
"""Resumably acquire one bounded EPO manifest with transient-failure retries."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
from pathlib import Path

from scripts.acquire_epo_ops import (
    access_token,
    acquire,
    batch_publications,
    now,
)
from scripts.local_automation import load_env_file


ROOT = Path(__file__).resolve().parents[1]
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


def retryable(error: Exception) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in TRANSIENT_HTTP_CODES
    return isinstance(error, (OSError, RuntimeError))


def write_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def acquire_bounded_batch(
    publications: list[str], output: Path, key: str, secret: str,
    include_description: bool, retry_rounds: int, retry_delay: float,
    state_output: Path,
) -> list[dict]:
    pending = list(publications)
    latest: dict[str, dict] = {}
    for round_number in range(1, retry_rounds + 1):
        token = access_token(key, secret)
        retry_publications = []
        for publication in pending:
            try:
                result = acquire(publication, output, token, include_description)
            except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as error:
                result = {
                    "publication": publication, "status": "failed",
                    "error": str(error), "retryable": retryable(error),
                }
                if result["retryable"]:
                    retry_publications.append(publication)
            latest[publication] = result
            write_state(
                state_output,
                {
                    "schema_version": "rxn2-epo-acquisition-agent-state-v1",
                    "updated_at": now(), "round": round_number,
                    "bounded_publications": publications,
                    "pending_retry": retry_publications,
                    "results": [latest[value] for value in publications if value in latest],
                    "safety": {
                        "discovers_new_patents": False,
                        "scrapes_search_interfaces": False,
                        "manifest_bounded": True,
                    },
                },
            )
        pending = retry_publications
        if not pending or round_number == retry_rounds:
            break
        time.sleep(retry_delay)
    return [latest[value] for value in publications]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--include-description", action="store_true")
    parser.add_argument("--retry-rounds", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=30.0)
    parser.add_argument("--state-output", type=Path)
    args = parser.parse_args()
    if args.retry_rounds < 1 or args.retry_delay < 0:
        raise SystemExit("retry rounds must be positive and retry delay cannot be negative")
    load_env_file(args.env_file)
    key = os.getenv("EPO_OPS_CONSUMER_KEY", "").strip()
    secret = os.getenv("EPO_OPS_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        raise SystemExit("EPO OPS credentials are missing")
    publications = batch_publications(args.batch)
    if not publications:
        raise SystemExit("The bounded batch contains no publications")
    state_output = args.state_output or args.output / "acquisition-agent-state.json"
    results = acquire_bounded_batch(
        publications, args.output, key, secret, args.include_description,
        args.retry_rounds, args.retry_delay, state_output,
    )
    print(json.dumps({"bounded_publications": len(publications), "results": results}, indent=2))
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
