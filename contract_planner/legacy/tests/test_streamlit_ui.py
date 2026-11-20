from dataclasses import replace
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from config import ROOT, Settings
import src.services.legal_rag as service_module


def make_app(monkeypatch, settings, items):
    fake = SimpleNamespace(settings=settings, items=items, item_by_id={i.item_id: i for i in items},
                           store=SimpleNamespace(counts=lambda: {settings.meta_collection: len(items), settings.docs_collection: len(items)}))
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(service_module, "LegalRAGService", lambda _: fake)
    return AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)


def test_ui_displays_disclaimer_and_missing_ai_key_without_traceback(monkeypatch, settings, item):
    app = make_app(monkeypatch, replace(settings, gms_api_key=""), [item])
    assert not app.exception
    assert any("전문가의 법률 자문을 대신하지 않습니다" in x.value for x in app.info)
    assert any("GMS_API_KEY" in x.value for x in app.warning)
    assert app.chat_input[0].disabled


def test_ui_item_switch_restores_only_that_items_history(monkeypatch, settings, item):
    second = replace(item, item_id="article:second", law_name="민법", article_no="제623조", article_title="임대인의 의무")
    app = make_app(monkeypatch, settings, [item, second])
    app.session_state["conversations"] = {f"chat:{item.item_id}": [{"role": "user", "content": "첫 항목 질문"}],
                                          f"chat:{second.item_id}": [{"role": "user", "content": "둘째 항목 질문"}]}
    app.selectbox(key="law_filter").select("민법").run()
    assert not app.exception
    text = "\n".join(x.value for x in app.markdown)
    assert "둘째 항목 질문" in text and "첫 항목 질문" not in text
    app.selectbox(key="law_filter").select(item.law_name).run()
    text = "\n".join(x.value for x in app.markdown)
    assert "첫 항목 질문" in text and "둘째 항목 질문" not in text


def test_ui_empty_snapshot_provides_prepare_command(monkeypatch, settings):
    app = make_app(monkeypatch, settings, [])
    assert not app.exception
    assert any("데이터가 없습니다" in x.value for x in app.warning)
    assert any("scripts.prepare_data" in x.value for x in app.code)


def test_discovered_item_navigation_selects_its_law_and_chat(monkeypatch, settings, item):
    second = replace(item, item_id="article:second", law_name="민법", article_no="제623조", article_title="임대인의 의무")
    app = make_app(monkeypatch, settings, [item, second])
    app.radio(key="page").set_value("조건으로 찾기").run()
    app.session_state["pending_item_id"] = second.item_id
    app.run()
    assert not app.exception
    assert app.radio(key="page").value == "법률 질문"
    assert app.selectbox(key="law_filter").value == "민법"
    assert app.selectbox(key="selected_item_id").value == second.item_id
