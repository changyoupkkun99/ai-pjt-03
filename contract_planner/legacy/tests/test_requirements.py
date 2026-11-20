"""Audit regressions: requirement failures, independent of external service availability."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from config import ROOT, Settings
from src.data.legal_api import LegalAPI
from src.rag.generator import UNKNOWN, validate_answer
import src.services.legal_rag as service_module
from src.utils.errors import ServiceError


def test_f2012_empty_external_collection_has_explicit_guidance(monkeypatch, settings, item):
    service = SimpleNamespace(settings=settings, items=[item], item_by_id={item.item_id: item},
                              store=SimpleNamespace(counts=lambda: {
                                  settings.meta_collection: 1, settings.docs_collection: 0}))
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(service_module, "LegalRAGService", lambda _: service)
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    messages = [x.value for x in list(app.info) + list(app.warning)]
    assert any("외부 문서" in text and "색인" in text for text in messages)
    assert not app.exception


class ThrottledResponse:
    status_code = 429
    ok = False

    def __init__(self, retry_after):
        self.headers = {"Retry-After": retry_after}


class SuccessfulResponse:
    status_code = 200
    ok = True
    headers = {}

    def json(self):
        return {"LawSearch": {"resultCode": "00", "law": [], "totalCnt": "0"}}


class SequenceSession:
    def __init__(self, responses):
        self.headers, self.calls, self.responses = {}, 0, iter(responses)

    def get(self, *args, **kwargs):
        self.calls += 1
        return next(self.responses)


@pytest.mark.parametrize("header", ["120", "http-date"])
def test_nf2006_long_retry_after_is_not_retried_early(monkeypatch, settings, header):
    if header == "http-date":
        header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    session = SequenceSession([ThrottledResponse(header), SuccessfulResponse()])
    sleeps = []
    monkeypatch.setattr("src.data.legal_api.time.sleep", lambda delay: sleeps.append(delay))
    api = LegalAPI(replace(settings, api_max_retries=1), session)
    with pytest.raises(ServiceError, match="대기|제한|혼잡"):
        api.search("임대차")
    assert session.calls == 1


def test_f2010_invalid_risk_category_is_not_accepted_as_supported():
    source = {"evidence_number": 1, "evidence_id": "E1", "text": "제공된 법률 자료"}
    result = validate_answer({"answer": "이 조항은 무조건 안전합니다. [근거 1]", "supported": True,
                              "risk_level": "안전"}, [source], contract=True)
    assert result["supported"] is False
    assert result["risk_level"] == "판단 보류"
    assert result["answer"] == UNKNOWN


def test_nf2002_legal_cache_is_scoped_to_the_api_endpoint(settings):
    first_session = SequenceSession([SuccessfulResponse()])
    LegalAPI(settings, first_session).search("임대차")
    second_session = SequenceSession([SuccessfulResponse()])
    other_settings = replace(settings, legal_api_base_url="https://other.example.invalid/DRF/")
    LegalAPI(other_settings, second_session).search("임대차")
    assert second_session.calls == 1


@pytest.mark.parametrize("retry_after", ["2", "invalid-header"])
def test_nf2006_short_retry_after_and_backoff_succeed_with_bounded_calls(monkeypatch, settings, retry_after):
    sleeps = []
    monkeypatch.setattr("src.data.legal_api.time.sleep", lambda delay: sleeps.append(delay))
    session = SequenceSession([ThrottledResponse(retry_after), SuccessfulResponse()])
    result, count = LegalAPI(replace(settings, api_max_retries=1), session).search("임대차")
    assert result == [] and count == 0 and session.calls == 2
    assert max(sleeps) >= (2 if retry_after == "2" else 1)
