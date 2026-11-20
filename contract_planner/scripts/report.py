"""Run deterministic scenarios, save their complete trace and compare expected outcomes."""

import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from time import perf_counter

base = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(base), str(base / "tests")]
from test_planner import CASES
from src.data.legal_api import KST
from backend.main import create_app

with tempfile.TemporaryDirectory() as tmp:
    os.environ.update(DATA_MODE="snapshot", LLM_PROVIDER="mock", RAG_DATA_DIR=tmp)
    agent = create_app(tmp).state.agent
    agent.clock = lambda: datetime(2026, 11, 20, 8, tzinfo=KST)
    rows = []
    for name, msg, expected, count in CASES:
        t = perf_counter()
        r = agent.run(str(uuid4()), msg)
        row = dict(
            scenario=name,
            input=msg,
            expected_status=expected,
            expected_replans=count,
            matched=(r["status"], r["attempts"]) == (expected, count),
            elapsed_ms=round(1000 * (perf_counter() - t), 2),
            result=r,
        )
        rows.append(row)
    (base / "docs/scenarios.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (base / "docs/tool-registry.json").write_text(
        json.dumps(agent.tools.registry(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print([(x["scenario"], x["matched"], x["elapsed_ms"]) for x in rows])
    assert all(x["matched"] for x in rows)
