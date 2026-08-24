#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app.db import initialize
from apps.api.app.graph_projection import rebuild_graph_projection


if __name__ == "__main__":
    initialize()
    print(json.dumps(rebuild_graph_projection(), indent=2, sort_keys=True))
