from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.reports import analyze


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("잔금일: 2026-11-30\n대출 실행 예정일: 2026-12-02", "높음"),
        ("잔금일: 2026-11-30\n대출 실행 예정일: 2026-11-30", "낮음"),
        ("잔금일: 2026-02-30", "확인 불가"),
        ("보증금: 20,000만원\n보증금: 210,000,000원", "높음"),
        ("보증금: 20,000만원\n보증금: 200,000,000원", "보통"),
        ("임차인은 모든 수선비를 부담한다.", "주의"),
        ("임차인은 보증금 반환을 포기하지 않는다.", "확인 불가"),
        ("소유자와 임대인이 일치한다.", "확인 불가"),
    ],
)
def test_level_bound_to_input(text, level):
    assert analyze(text)[0]["level"] == level


def test_korean_deposit_units():
    assert analyze("보증금 2억원\n보증금: 20,000만원")[0]["level"] == "보통"
    assert analyze("보증금 2억 원\n보증금: 21,000만원")[0]["level"] == "높음"


def test_report_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    sid, other = str(uuid4()), str(uuid4())
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(
            "/api/reports",
            json={
                "session_id": sid,
                "clauses": "임차인이 모든 수선비를 부담한다.\n등기 내역 확인",
                "ai": True,
            },
        )
        assert r.status_code == 201
        draft = r.json()
        assert draft["unverified"] is True and len(draft["findings"]) == 1
        assert draft["findings"][0]["source"]["title"].startswith("민법 제623조")
        assert "mock" in draft["explanation"]
        url = f"/api/reports/{draft['id']}"
        body = {
            "title": "편집한 보고서",
            "requests": ["수선 기준을 구분해 주세요."],
            "revision": 1,
        }
        assert c.get(url, params={"session_id": other}).status_code == 404
        assert c.put(url, params={"session_id": other}, json=body).status_code == 404
        assert c.get("/api/reports", params={"session_id": other}).json() == []
        assert c.put(url, params={"session_id": sid}, json=body).status_code == 200
        assert c.put(url, params={"session_id": sid}, json=body).status_code == 409
    with TestClient(create_app(tmp_path)) as c:
        detail = c.get(url, params={"session_id": sid}).json()
        assert detail["findings"][0]["request"] == body["requests"][0]
        assert detail["original"] == draft["original"]
        assert (
            c.get("/api/reports", params={"session_id": sid}).json()[0]["revision"] == 1
        )
        assert (
            c.put(
                url,
                params={"session_id": sid},
                json=body | {"revision": 2, "requests": []},
            ).status_code
            == 422
        )
        assert (
            c.post(
                "/api/reports", json={"session_id": sid, "clauses": "   "}
            ).status_code
            == 422
        )


def test_missing_provider_preserves_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GMS_API_KEY", "fake-access-for-test")
    with TestClient(create_app(tmp_path)) as c:
        payload = {
            "session_id": str(uuid4()),
            "clauses": "임차인은 모든 수선비를 부담한다.",
            "ai": True,
        }
        r = c.post("/api/reports", json=payload)
        assert r.status_code == 201 and "키가 설정되지 않아" in r.json()["explanation"]
        assert (
            c.post(
                "/api/reports", json=payload | {"clauses": "fake-access-for-test"}
            ).status_code
            == 422
        )
