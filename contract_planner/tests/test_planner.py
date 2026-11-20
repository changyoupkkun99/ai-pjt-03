from datetime import datetime
from uuid import uuid4
import pytest

from backend.main import create_app
from backend.providers import ClaudeProvider, ProviderError
from src.data.legal_api import KST
from fastapi.testclient import TestClient

BASE = "전세계약이 12월 31일에 끝나는데 보증금 반환을 준비해야 해. 평일에는 오후 6시 이후만 시간이 있어."
CASES = [
    ("조건 충족", BASE, "ready", 0),
    ("재계획", "11월 21일부터 평일 오후 6시 이후 12월 31일 계약 만료 준비", "ready", 2),
    (
        "재계획 상한",
        "11월 20일부터 12월 31일 계약 만료 준비. 오후 6시부터 오후 6시 10분까지",
        "limit",
        3,
    ),
    ("Tool 실패", "2027-01-03부터 2027-01-10 계약 만료 준비", "unverified", 0),
    ("Tool 불필요", "안녕하세요", "help", 0),
    (
        "날씨 제약",
        "대전 현장 점검 11월 20일부터 12월 31일 계약 만료. 강수확률 10% 이하",
        "ready",
        1,
    ),
    ("정보 부족", "보증금 반환 준비", "clarify", 0),
    ("특약 검토", "특약에 보증금 반환을 포기한다고 적혀 있어", "references", 0),
]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_MODE", "snapshot")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("MAX_REPLANS", "3")
    app = create_app(tmp_path)
    app.state.agent.clock = lambda: datetime(2026, 11, 20, 8, 0, tzinfo=KST)
    return app


@pytest.mark.parametrize("name,msg,expected,replans", CASES)
def test_scenario(app, name, msg, expected, replans):
    r = app.state.agent.run(str(uuid4()), msg)
    assert (r["status"], r["attempts"]) == (expected, replans)
    assert bool(r["draft"]) == (expected == "ready")
    if name == "Tool 실패":
        assert "NO_FIXTURE" in [e["code"] for e in r["errors"]]
    if replans:
        assert len(r["rejected"]) >= replans
        assert any(e["event"] == "tool.reused" for e in r["events"])
        requests = [
            (e["tool"], e["input_id"])
            for e in r["events"]
            if e["event"] == "tool.request"
        ]
        assert len(requests) == len(set(requests))
    if expected in {"help", "clarify"}:
        assert not r["jobs"]
    if name == "특약 검토":
        assert (
            "rag" in r["jobs"]
            and r["sources"]
            and any(e["code"] == "NO_INDEX" for e in r["errors"])
        )
        assert any(
            e["event"] == "llm.decision" and e["actor"] == "mock" for e in r["events"]
        )


def test_followup_and_subscriber_failure(app):
    a = app.state.agent
    a.bus.subscribers.clear()
    a.bus.subscribers.append(lambda _: 1 / 0)
    sid = str(uuid4())
    first = a.run(sid, BASE)
    second = a.run(sid, "오후 7시 이후로 바꿔줘")
    assert second["conditions"] == first["conditions"] | {"start": "19:00"}
    assert second["draft"]["start"] == "19:00"
    assert a.run(str(uuid4()), "오후 7시 이후로 바꿔줘")["status"] == "clarify"


def test_tools_inputs_failures_and_cache(app):
    tools = app.state.agent.tools
    for spec in tools.registry():
        assert tools.invoke(spec["name"], {"extra": "bad"}).error.code == "BAD_INPUT"
    for kind in ["law", "prec", "expc"]:
        candidates = tools.invoke(
            "candidates", {"query": "주택임대차보호법", "kind": kind}
        )
        assert candidates.ok and candidates.data[0]["id"]
    args = {"api_id": "276291", "kind": "law", "topic": "return"}
    assert tools.invoke("detail", args).data[0]["effective"]
    assert tools.invoke("detail", args).cached
    assert tools.invoke("city", {"name": "Seoul"}).error.code == "NO_FIXTURE"
    assert tools.invoke("rag", {"question": "대항력"}).error.code == "NO_INDEX"


def test_no_secret_or_raw_exception(app, monkeypatch):
    agent = app.state.agent
    monkeypatch.delenv("LEGAL_API_KEY", raising=False)
    from backend.tools import Tools

    agent.tools = Tools("live", agent.store.path.parent)
    msg = "법률 근거 조문 확인"
    r = agent.run(str(uuid4()), msg)
    assert r["status"] == "unverified" and r["errors"][0]["code"] == "MISSING_KEY"
    assert "Traceback" not in str(r)
    assert not any("question" in e.get("arguments", {}) for e in r["events"])


def test_api_and_plan_conflicts(app):
    with TestClient(app) as c:
        sid, other = str(uuid4()), str(uuid4())
        reply = c.post("/api/chat", json={"session_id": sid, "message": BASE}).json()
        p = reply["draft"]
        url = "/api/plans"
        params = {"session_id": sid}

        def save(plan):
            res = c.post(url, params=params, json=plan)
            assert res.status_code == 201
            return res.json()

        a = save(p)
        assert not a["conflicts"]
        b = save(p)
        assert b["conflicts"][0]["id"] == a["plan"]["id"]
        pid = a["plan"]["id"]
        endpoint = f"{url}/{pid}"
        assert c.get(endpoint, params={"session_id": other}).status_code == 404
        assert c.get(endpoint, params=params).json()["created_at"]
        change = c.put(
            endpoint, params=params, json=p | {"start": p["end"], "end": "20:00"}
        )
        assert change.status_code == 200 and not change.json()["conflicts"]
        assert (
            c.put(endpoint, params=params, json=p | {"end": "00:00"}).status_code == 422
        )
        assert c.delete(endpoint, params=params).status_code == 204
        assert len(c.get(url, params=params).json()) == 1
        c.post("/api/chat", json={"session_id": other, "message": "안녕"})
        assert len(c.get("/api/logs").json()) == 2
        events = c.get("/api/logs/" + sid).json()
        assert events == reply["events"]
        assert c.put("/api/logs/" + sid, json={}).status_code == 405
        assert c.delete("/api/logs/" + sid).status_code == 204
        assert c.get("/api/logs/" + other).status_code == 200
        assert c.get("/api/usage").json()["gms"]["calls"] == 0
        assert c.get("/openapi.json").status_code == 200


def test_claude_isolated_usage_and_cache(app, monkeypatch):
    store = app.state.agent.store
    llm = ClaudeProvider(store)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GMS_API_KEY", "fixture-gms")
    with pytest.raises(ProviderError, match="CLAUDE_KEY_MISSING"):
        llm.complete("system", {})
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": [{"type": "text", "text": '{"ok":true}'}],
                "usage": {"input_tokens": 9, "output_tokens": 3},
            }

    def fake(url, **kwargs):
        assert url.startswith("https://api.anthropic.com/")
        assert kwargs["headers"]["x-api-key"] != "fixture-gms"
        calls.append(url)
        return Response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-direct")
    monkeypatch.setattr("backend.providers.requests.post", fake)
    for _ in range(2):
        assert llm.complete("system", {}) == {"ok": True}
    assert len(calls) == 1 and store.usage()["anthropic"]["input_tokens"] == 9
    assert store.usage()["gms"]["calls"] == 0


def test_facts_and_invalid_calendar(app):
    a = app.state.agent
    assert a.run(str(uuid4()), "보증금 반환 판례")["sources"][0]["url"].startswith(
        "https://"
    )
    assert a.run(str(uuid4()), "2026-02-30 계약 만료")["status"] == "clarify"
    r = a.run(str(uuid4()), "전세 계약 만료 12월 31일 오후 25시 이후")
    assert r["status"] == "clarify"


def test_anomaly_and_separate_diagnosis(app):
    from backend.audit import assess, diagnose
    from backend.providers import MockProvider

    assert assess([], 3)["status"] == "insufficient"
    e = {"seq": 1, "event": "tool.request", "tool": "weather", "input_id": "a"}
    bad = assess([e, dict(e, seq=2), {"event": "replan"}], 1)
    assert {x["code"] for x in bad["warnings"]} == {
        "UNPLANNED_TOOL",
        "REPEAT_TOOL",
        "REPLAN_LIMIT",
    }
    assert diagnose({"warnings": []}, None) is None
    assert diagnose(bad, MockProvider())["provider"] == "mock"
    with TestClient(app) as c:
        sid = str(uuid4())
        r = c.post("/api/chat", json={"session_id": sid, "message": CASES[2][1]}).json()
        url = f"/api/logs/{sid}/{r['run_id']}"
        assert c.get(url + "/assessment").json()["assessment"]["status"] == "warning"
        assert c.get(url + "/assessment").json()["diagnosis"] is None
        diagnosed = c.post(url + "/diagnosis").json()
        assert diagnosed["diagnosis"]["text"]
        assert c.get(url + "/assessment").json() == diagnosed
        assert c.get("/api/logs/" + sid).json() == r["events"]


def test_credentials_rejected_before_graph(app, monkeypatch):
    sid = str(uuid4())
    secret = "test-value-never-forward"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    with TestClient(app) as c:
        r = c.post(
            "/api/chat", json={"session_id": sid, "message": "특약 검토 " + secret}
        )
        assert r.status_code == 422 and secret not in r.text
        assert c.get("/api/logs").json() == []
