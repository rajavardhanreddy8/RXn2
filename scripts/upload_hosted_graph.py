"""Idempotently publish RXN2's derived read model to a protected Supabase importer.

The importer token is supplied only through the process environment and is
never written to Git, the database, or a run log.
"""
from __future__ import annotations

import concurrent.futures
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = Path(os.environ.get("SCALEUP_DB_PATH", ROOT / "data/curated/rxn2-provisional.sqlite"))
# The projection helpers read this setting at import time. Keep their snapshot
# calculations pointed at the same curated database as the row exporter.
os.environ.setdefault("SCALEUP_DB_PATH", str(DB_PATH))

from apps.api.app.chemistry import molecule_graph
from apps.api.app.graph_projection import graph_overview, graph_route_map, graph_stats


ENDPOINT = os.environ.get("RXN2_SUPABASE_IMPORT_URL", "").strip()
TOKEN = os.environ.get("RXN2_SUPABASE_IMPORT_TOKEN", "").strip()
TOKEN_FILE = os.environ.get("RXN2_SUPABASE_IMPORT_TOKEN_FILE", "").strip()
CHECKPOINT = ROOT / "data/processed/supabase-hosted-upload.json"

if not TOKEN and TOKEN_FILE:
    TOKEN = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()

if ENDPOINT and "op=" not in ENDPOINT:
    ENDPOINT = f"{ENDPOINT}{'&' if '?' in ENDPOINT else '?'}op=ingest"


def normalise(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in ("properties_json", "atoms", "bonds"):
        if isinstance(item.get(key), str):
            item[key] = json.loads(item[key])
    return item


def post(table: str, rows: list[dict]) -> None:
    body = json.dumps({"table": table, "rows": rows}, separators=(",", ":")).encode()
    request = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json", "x-rxn2-import-token": TOKEN},
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"HTTP {response.status}")
            return
        except (OSError, urllib.error.HTTPError) as error:
            if attempt == 4:
                raise RuntimeError(f"{table} upload failed: {error}") from error
            time.sleep(2 ** attempt)


def load_checkpoint() -> dict[str, object]:
    if not CHECKPOINT.exists():
        return {}
    return json.loads(CHECKPOINT.read_text(encoding="utf-8"))


def save_checkpoint(value: dict[str, object]) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHECKPOINT.with_suffix(".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(CHECKPOINT)


def upload_rows(table: str, rows: list[dict], chunk_size: int, checkpoint: dict[str, object]) -> None:
    chunks = [rows[index:index + chunk_size] for index in range(0, len(rows), chunk_size)]
    done_key = f"{table}_chunks"
    completed_chunks = set(checkpoint.get(done_key, []))
    pending = [(index, batch) for index, batch in enumerate(chunks) if index not in completed_chunks]
    if not pending:
        print(f"{table}: already complete ({len(rows):,})")
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(post, table, batch): index for index, batch in pending}
        for future in concurrent.futures.as_completed(futures):
            future.result()
            completed_chunks.add(futures[future])
            checkpoint[done_key] = sorted(completed_chunks)
            checkpoint[table] = sum(len(chunks[index]) for index in completed_chunks)
            save_checkpoint(checkpoint)
            print(f"{table}: {checkpoint[table]:,}/{len(rows):,}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("all", "nodes", "edges", "snapshots", "structures"), default="all")
    args = parser.parse_args()
    if not ENDPOINT or not TOKEN:
        raise SystemExit("Set RXN2_SUPABASE_IMPORT_URL and RXN2_SUPABASE_IMPORT_TOKEN before running.")
    if not DB_PATH.is_file():
        raise SystemExit(f"Curated graph database not found: {DB_PATH}")
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    checkpoint = load_checkpoint()

    if args.only in ("all", "nodes"):
        nodes = [normalise(row) for row in db.execute("SELECT * FROM graph_node ORDER BY node_id")]
        # Supabase Edge Functions impose request-size limits. Smaller chunks keep the
        # import reliable and still run concurrently.
        upload_rows("rxn2_graph_node", nodes, 50, checkpoint)
    if args.only in ("all", "edges"):
        edges = [normalise(row) for row in db.execute("SELECT * FROM graph_edge ORDER BY edge_id")]
        upload_rows("rxn2_graph_edge", edges, 75, checkpoint)

    snapshots = [
        {"snapshot_key": "stats", "payload": graph_stats()},
        {"snapshot_key": "overview", "payload": graph_overview()},
        {"snapshot_key": "routes", "payload": graph_route_map({"validated", "unresolved"}, collapsed=True)},
    ]
    if args.only in ("all", "snapshots"):
        upload_rows("rxn2_graph_snapshot", snapshots, 1, checkpoint)

    molecules = []
    if args.only in ("all", "structures"):
        for row in db.execute("""SELECT c.compound_id,c.preferred_name,coalesce(cp.standardized_smiles,c.smiles) smiles,
                                    cp.molecular_formula,cp.molecular_weight,c.inchi_key
                             FROM compound c LEFT JOIN compound_property cp USING(compound_id)
                             WHERE coalesce(cp.standardized_smiles,c.smiles) IS NOT NULL ORDER BY c.compound_id"""):
            item = dict(row)
            graph = molecule_graph(item["smiles"])
            molecules.append({**item, "smiles": graph["smiles"], "atoms": graph["atoms"], "bonds": graph["bonds"]})
        upload_rows("rxn2_molecule_structure", molecules, 5, checkpoint)
    print("Hosted graph upload complete.")


if __name__ == "__main__":
    main()
