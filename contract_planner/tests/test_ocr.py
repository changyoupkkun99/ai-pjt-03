from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from backend import ocr, providers
from backend.main import create_app


def test_conflicting_transcribed_amounts_need_review():
    good = "계약금: 일억팔천만원 (₩180,000,000)"
    assert ocr.mark_uncertain_amounts(good) == good
    bad = "잔금: 일억 원정 (₩700,000,000)\n2차중도금: 없음 원정 2019년 05월 30일 지급"
    assert ocr.mark_uncertain_amounts(bad).count("[판독 불가]") == 2


def test_photo_to_sale_draft(tmp_path, monkeypatch):
    photo = BytesIO()
    Image.new("RGB", (100, 120), "white").save(photo, "JPEG")
    text = "부동산 매매 계약서\n매도인은 누수 수리비를 부담한다.\n잔금일에 근저당을 말소한다."
    monkeypatch.setattr(ocr, "recognize", lambda image: text)
    sid = str(uuid4())
    with TestClient(create_app(tmp_path)) as client:
        read = client.post("/api/reports/ocr?provider=local", content=photo.getvalue())
        assert read.json()["text"] == text and read.json()["provider"] == "local"
        assert client.get("/api/reports", params={"session_id": sid}).json() == []
        draft = client.post(
            "/api/reports", json={"session_id": sid, "clauses": text}
        ).json()
        assert "매매" in draft["title"]
        assert [x["title"] for x in draft["findings"]] == [
            "매매 하자·수리 책임",
            "권리 말소 약정",
        ]
        assert draft["unverified"] is True
        assert all(x["source"] is None for x in draft["findings"])


def test_unreadable_photo(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path)) as client:
        assert (
            client.post(
                "/api/reports/ocr?provider=local", content=b"not a photo"
            ).status_code
            == 422
        )
        monkeypatch.setattr(ocr, "MAX_BYTES", 4)
        assert client.post("/api/reports/ocr", content=b"12345").status_code == 413
        monkeypatch.setattr(ocr, "read_photo", lambda data: "")
        assert (
            "글자가 없습니다"
            in client.post("/api/reports/ocr?provider=local", content=b"123").json()[
                "detail"
            ]
        )


def test_gms_default_reads_image_and_records_usage(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from backend.reports import analyze

    photo = BytesIO()
    Image.new("RGB", (100, 120), "white").save(photo, "PNG")
    text = "잔금일: [판독 불가]"
    monkeypatch.setenv("GMS_API_KEY", "test-key")
    monkeypatch.setenv("GMS_VISION_MODEL", "claude-opus-5-5")
    response = Mock(ok=True, is_redirect=False)
    response.json.return_value = {
        "usage": {"input_tokens": 123, "output_tokens": 45},
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}],
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(providers.requests, "post", post)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        result = client.post("/api/reports/ocr", content=photo.getvalue())
        assert result.json()["text"] == text and result.json()["provider"] == "gms"
        assert analyze(text)[0]["level"] == "확인 불가"
        assert post.call_args.args[0].startswith("https://gms.ssafy.io/")
        source = post.call_args.kwargs["json"]["messages"][0]["content"][1]["source"]
        assert source["type"] == "base64" and source["data"]
        usage = client.get("/api/usage").json()["gms"]
        assert (usage["input_tokens"], usage["output_tokens"]) == (123, 45)
        app.state.store.measure("gms", "fixture", 30_000, 0, False)
        assert (
            client.post("/api/reports/ocr", content=photo.getvalue()).status_code == 200
        )
        assert post.call_count == 2
        usage = client.get("/api/usage").json()
        assert usage["gms_stop_at"] is None and usage["gms_calls_enabled"]
        assert usage["gms"]["input_tokens"] == 30_246


def test_gms_failure_never_returns_local_text(tmp_path, monkeypatch):
    from unittest.mock import Mock

    photo = BytesIO()
    Image.new("RGB", (50, 50), "white").save(photo, "JPEG")
    monkeypatch.delenv("GMS_API_KEY", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        assert (
            client.post("/api/reports/ocr", content=photo.getvalue()).status_code == 503
        )
        monkeypatch.setenv("GMS_API_KEY", "invalid-demo")
        monkeypatch.setattr(
            providers.requests,
            "post",
            Mock(return_value=Mock(ok=False, status_code=401)),
        )
        result = client.post("/api/reports/ocr", content=photo.getvalue())
        assert result.status_code == 502 and "401" in result.json()["detail"]
        assert client.get("/api/usage").json()["gms"]["calls"] == 0


def test_gms_model_fallback_uses_requested_priority(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from backend.store import Store

    monkeypatch.setenv("GMS_API_KEY", "fixture-key")
    monkeypatch.delenv("GMS_TEXT_MODEL", raising=False)
    unavailable = Mock(ok=False, status_code=404)
    sol = Mock(ok=True, is_redirect=False)
    sol.json.return_value = {
        "choices": [
            {"finish_reason": "stop", "message": {"content": '{"answer":"ok"}'}}
        ],
        "usage": {"prompt_tokens": 18, "completion_tokens": 4},
    }
    post = Mock(side_effect=[unavailable, sol])
    monkeypatch.setattr(providers.requests, "post", post)
    store = Store(tmp_path / "usage.sqlite3")
    result = providers.GmsProvider(store).complete("fixture", {})
    assert result["_gms_model"] == "gpt-6-sol"
    assert [call.kwargs["json"]["model"] for call in post.call_args_list] == [
        "gpt-6-astra",
        "gpt-6-sol",
    ]
    assert store.usage()["gms"]["calls"] == 1
