"""Exercise real adapters and the graph. Never invokes a paid generation endpoint."""

import argparse, json, os, sys, tempfile
from datetime import datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import create_app
from src.data.legal_api import KST


def models():
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
    states = {}
    for provider, var, url, headers in [
        (
            "anthropic",
            "ANTHROPIC_API_KEY",
            "https://api.anthropic.com/v1/models/" + model,
            {"anthropic-version": "2023-06-01"},
        ),
        (
            "gms",
            "GMS_API_KEY",
            "https://gms.ssafy.io/gmsapi/api.openai.com/v1/models",
            {},
        ),
    ]:
        key = os.getenv(var)
        if not key:
            states[provider] = {"status": "NO_CREDENTIAL", "generation_calls": 0}
            continue
        headers.update(
            {"x-api-key": key}
            if provider == "anthropic"
            else {"Authorization": "Bearer " + key}
        )
        try:
            r = requests.get(url, headers=headers, timeout=8, allow_redirects=False)
            r.raise_for_status()
            data = r.json()
            ids = (
                [data.get("id", "")]
                if provider == "anthropic"
                else [x.get("id", "") for x in data.get("data", [])]
            )
            states[provider] = {
                "status": "catalog_read",
                "sonnet_ids": [x for x in ids if "sonnet" in x.lower()],
                "generation_calls": 0,
            }
        except (requests.RequestException, ValueError, TypeError):
            states[provider] = {"status": "CATALOG_UNAVAILABLE", "generation_calls": 0}
    return states


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["mixed", "rag", "providers"])
    mode = parser.parse_args().mode
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update(
            DATA_MODE="mixed" if mode == "mixed" else "snapshot", LLM_PROVIDER="mock"
        )
        agent = create_app(tmp).state.agent
        if mode == "providers":
            output = models()
        else:
            if mode == "rag":
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                message = "보증금 반환과 대항력의 법률 근거를 찾아줘."
            else:
                today = datetime.now(KST).date()
                from datetime import timedelta

                message = f"대전 현장 점검, {(today + timedelta(days=6)).isoformat()} 계약 만료 준비. 강수확률 10% 이하"
            t = perf_counter()
            result = agent.run(str(uuid4()), message)
            output = {
                "message": message,
                "mode": mode,
                "elapsed_ms": round((perf_counter() - t) * 1000, 2),
                "result": result,
                "verified": result["status"] == "ready"
                if mode == "mixed"
                else any(
                    x["event"] == "tool.result" and x["tool"] == "rag" and x["ok"]
                    for x in result["events"]
                )
                and any(s["tool"] == "rag" and s["url"] for s in result["sources"]),
            }
        (ROOT / f"docs/integration-{mode}.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(mode, output.get("verified", output if mode == "providers" else None))
        if mode != "providers":
            assert output["verified"]
