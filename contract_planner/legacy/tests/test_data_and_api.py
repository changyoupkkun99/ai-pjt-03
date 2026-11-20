from dataclasses import replace
import json
import pytest

from config import ROOT
from src.data.legal_api import LegalAPI, article_identity, article_text, sanitize_response
from src.data.metadata_loader import load_documents, load_items
from src.utils.cache import DiskCache
from src.utils.errors import AuthenticationError


class FakeResponse:
    status_code = 200
    ok = True

    def json(self):
        return {"LawSearch": {"resultCode": "00", "totalCnt": "1", "law": {
            "법령일련번호": "276291", "법령ID": "001248", "법령명한글": "주택임대차보호법", "시행일자": "20260102",
        }}}


class FakeSession:
    def __init__(self):
        self.headers, self.requests = {}, []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse()


def test_official_snapshot_contains_at_least_100_unique_actual_articles():
    items = load_items(ROOT / "data/processed/law_items.jsonl")
    docs = load_documents(ROOT / "data/processed/source_documents.jsonl")
    assert len(items) >= 100
    assert len({i.item_id for i in items}) == len(items)
    assert all(i.text and i.article_title and i.source_url.startswith("https://www.law.go.kr/") for i in items)
    assert all(i.document_id in {d.document_id for d in docs} for i in items)
    assert {"law", "prec", "expc"} <= {d.document_type for d in docs}
    assert all("OC=" not in d.source_url for d in docs)
    assert all(label in items[0].index_text() for label in ("법률명:", "조문:", "조문 제목:", "분류:", "핵심 키워드:", "조문 내용:"))


def test_nested_article_paragraphs_items_and_subitems_are_preserved():
    article = {"조문번호": "3", "조문가지번호": "2", "조문내용": "제3조의2(보증금의 회수)",
               "항": {"항내용": "② 대항요건과 확정일자", "호": [{"호내용": "1. 은행", "목": {"목내용": "가. 세부"}}]}}
    assert article_identity(article) == ("000302", "제3조의2")
    assert article_text(article).splitlines() == ["제3조의2(보증금의 회수)", "② 대항요건과 확정일자", "1. 은행", "가. 세부"]


def test_api_credential_is_injected_and_identical_query_uses_cache(settings):
    session = FakeSession()
    api = LegalAPI(settings, session)
    first, _ = api.search("주택임대차보호법")
    second, _ = api.search("주택임대차보호법")
    assert first == second
    assert len(session.requests) == 1
    assert session.requests[0][1]["params"]["OC"] == settings.legal_api_key
    assert not any(settings.legal_api_key in p.read_text(encoding="utf-8") for p in (settings.data_dir / "cache").rglob("*.json"))


def test_missing_legal_key_is_clear_without_a_network_request(settings):
    session = FakeSession()
    with pytest.raises(AuthenticationError, match="LEGAL_API_KEY"):
        LegalAPI(replace(settings, legal_api_key=""), session).search("임대차")
    assert session.requests == []


def test_response_urls_are_sanitized():
    payload = {"link": "/DRF/lawService.do?OC=private-value&ID=123&type=HTML", "OC": "private-value"}
    cleaned = sanitize_response(payload)
    assert "private-value" not in json.dumps(cleaned)
    assert "ID=123" in cleaned["link"]


def test_cache_handles_empty_results_and_corrupt_files(tmp_path):
    cache = DiskCache(tmp_path)
    cache.set("query", "same", [])
    assert cache.get("query", "same") == []
    cache._path("query", "same").write_text("corrupt")
    assert cache.get("query", "same") is None
