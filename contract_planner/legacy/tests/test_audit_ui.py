"""UI fault injection/rendering tests, distinct from the live service audit."""

from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from config import ROOT, Settings
from src.data.metadata_loader import save_jsonl
from src.rag.generator import UNKNOWN, validate_answer
from src.rag.prompts import ANSWER_SYSTEM
import src.services.legal_rag as service_module


def source_for(item, number=1):
    return {"id": item.item_id, "text": item.text, "evidence_id": f"E{number}", "evidence_number": number,
            "metadata": {"title": f"{item.law_name} {item.article_no}", "source_type": "항목 메타데이터",
                         "document_type": "law", "source": item.source, "source_url": item.source_url}}


def response_for(item, answer=None):
    result = validate_answer({"answer": answer or f"테스트 응답 {item.item_id} [근거 1]", "supported": True}, [source_for(item)])
    return result | {"documents": [source_for(item)], "elapsed_seconds": 0.1,
                     "trace": {"warnings": [], "cache_hit": True}}


def app_with_service(monkeypatch, settings, service):
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(service_module, "LegalRAGService", lambda _: service)
    return AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)


def fake_service(settings, items, ask=None):
    return SimpleNamespace(settings=settings, items=items, item_by_id={i.item_id: i for i in items},
                           store=SimpleNamespace(counts=lambda: {settings.meta_collection: len(items), settings.docs_collection: 1}),
                           ask=ask or (lambda question, item_id, **kwargs: response_for(items[0])))


def test_f2008_only_cited_source_number_title_and_original_url_are_rendered(monkeypatch, settings, item):
    other = replace(item, item_id="other", article_no="제99조", article_title="사용하지 않은 근거")
    selected = validate_answer({"answer": "검증용 응답 [근거 2]", "supported": True}, [source_for(other), source_for(item, 2)])
    result = selected | {"documents": [source_for(other), source_for(item, 2)], "elapsed_seconds": 0.1,
                         "trace": {"warnings": [], "cache_hit": True}}
    service = fake_service(settings, [item], lambda *args, **kwargs: result)
    app = app_with_service(monkeypatch, settings, service)
    app.chat_input[0].set_value("인용 표시 검증").run()
    assert not app.exception
    labels = [e.label for e in app.expander]
    assert any("[근거 2]" in label and item.article_no in label for label in labels)
    assert not any("[근거 1]" in label or "제99조" in label for label in labels)
    assert any(link.proto.url == item.source_url for link in app.get("link_button"))


def test_f2008_uncited_answer_displays_explicit_no_citation_notice(monkeypatch, settings, item):
    result = validate_answer({}, []) | {"documents": [], "elapsed_seconds": 0.1,
                                        "trace": {"warnings": [], "cache_hit": True}}
    service = fake_service(settings, [item], lambda *args, **kwargs: result)
    app = app_with_service(monkeypatch, settings, service)
    app.chat_input[0].set_value("문서에 없는 질문").run()
    assert any(UNKNOWN in e.value for e in app.markdown)
    assert any("인용된 법률 근거가 없습니다" in e.value for e in app.caption)
    assert not app.exception


def test_f2009_submit_a_then_b_then_return_to_a_preserves_separate_conversations(monkeypatch, settings, item):
    second = replace(item, item_id="article:b", article_no="제3조의3", article_title="임차권등기명령")
    items = {i.item_id: i for i in (item, second)}
    calls = []

    def ask(question, item_id, **kwargs):
        calls.append((question, item_id, len(kwargs["history"])))
        return response_for(items[item_id])

    app = app_with_service(monkeypatch, settings, fake_service(settings, list(items.values()), ask))
    app.chat_input[0].set_value("항목 A의 질문").run()
    app.selectbox(key="selected_item_id").select(second.item_id).run()
    app.chat_input[0].set_value("항목 B의 질문").run()
    text = "\n".join(e.value for e in app.markdown)
    assert "항목 B의 질문" in text and "항목 A의 질문" not in text
    app.selectbox(key="selected_item_id").select(item.item_id).run()
    text = "\n".join(e.value for e in app.markdown)
    assert "항목 A의 질문" in text and "항목 B의 질문" not in text
    assert calls == [("항목 A의 질문", item.item_id, 0), ("항목 B의 질문", second.item_id, 0)]
    assert not app.exception


def test_f2011_actual_card_button_navigates_to_its_item_chat(monkeypatch, settings, item):
    second = replace(item, item_id="article:b", article_no="제3조의3", article_title="임차권등기명령")
    service = fake_service(settings, [item, second])
    service.discovery = SimpleNamespace(discover=lambda _: [{"item": second, "reason": "조건에 맞는 항목"}])
    app = app_with_service(monkeypatch, settings, service)
    app.radio(key="page").set_value("조건으로 찾기").run()
    app.text_input[0].set_value("전세보증금을 돌려받지 못했을 때 관련된 법률")
    next(b for b in app.button if b.label == "관련 법률 찾기").click().run()
    next(b for b in app.button if b.label == "이 조문으로 질문하기 →").click().run()
    assert app.radio(key="page").value == "법률 질문"
    assert app.selectbox(key="selected_item_id").value == second.item_id
    assert not app.exception


def test_f2012_missing_legal_key_and_no_discovery_results_are_visible(monkeypatch, settings, item):
    settings = replace(settings, legal_api_key="")
    service = fake_service(settings, [item])
    service.discovery = SimpleNamespace(discover=lambda _: [])
    app = app_with_service(monkeypatch, settings, service)
    assert any("LEGAL_API_KEY" in w.value for w in app.warning)
    app.radio(key="page").set_value("조건으로 찾기").run()
    app.text_input[0].set_value("관련 없는 조건")
    next(b for b in app.button if b.label == "관련 법률 찾기").click().run()
    assert any("항목을 찾지 못했습니다" in i.value for i in app.info)
    assert not app.exception


def test_f2012_fresh_real_database_initializes_empty_and_provides_guidance(monkeypatch, settings, item):
    save_jsonl(settings.processed_dir / "law_items.jsonl", [item])
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert any("메타데이터 색인이 없습니다" in w.value for w in app.warning)
    assert settings.chroma_dir.exists()
    assert [m.value for m in app.metric] == ["0", "0"]
    assert not app.exception


def test_nf2004_unexpected_model_error_does_not_expose_secrets_prompt_or_trace(monkeypatch, settings, item):
    sentinel = "audit-sensitive-sentinel-value"

    def failed_ask(*args, **kwargs):
        raise RuntimeError(sentinel + "\n" + ANSWER_SYSTEM + "\nTraceback (most recent call last)")

    app = app_with_service(monkeypatch, settings, fake_service(settings, [item], failed_ask))
    app.chat_input[0].set_value("오류 주입 테스트").run()
    text = "\n".join(str(e.value) for group in (app.markdown, app.error, app.caption) for e in group)
    assert sentinel not in text and ANSWER_SYSTEM not in text and "Traceback" not in text
    assert any("처리 중 오류가 발생했습니다" in e.value for e in app.error)
    assert not app.exception


@pytest.mark.parametrize("document_type,label", [("law", "법령·조문"), ("prec", "판례"), ("expc", "법령해석례")])
def test_f2016_actual_source_snapshot_type_and_public_url_are_rendered(monkeypatch, settings, item, document_type, label):
    # Reuse an actual official snapshot record, while replacing only the remote answer call.
    records = (json.loads(line) for line in (ROOT / "data/processed/source_documents.jsonl").read_text(encoding="utf-8").splitlines())
    document = next(row for row in records if row["document_type"] == document_type)
    source = {"id": document["document_id"], "text": document["text"], "evidence_id": "E1", "evidence_number": 1,
              "metadata": {**document, **document.get("extra", {}), "source_type": "외부 문서"}}
    result = validate_answer({"answer": "실제 수집본 표시 검증 [근거 1]", "supported": True}, [source])
    result |= {"documents": [source], "elapsed_seconds": 0.1, "trace": {"warnings": [], "cache_hit": True}}
    app = app_with_service(monkeypatch, settings, fake_service(settings, [item], lambda *args, **kwargs: result))
    app.chat_input[0].set_value("출처 종류 표시 확인").run()
    assert any(f"외부 문서 · {label}" in e.value for e in app.caption)
    assert any(link.proto.url == document["source_url"] for link in app.get("link_button"))
    assert any(document["title"] in e.label for e in app.expander)
    assert not app.exception
