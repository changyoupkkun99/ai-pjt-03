from dataclasses import replace

import pytest

from src.data.collector import Collector
from src.indexing.indexer import Indexer
from src.rag.generator import Generator, UNKNOWN, validate_answer
from src.rag.graph import build_graph
from src.rag.llm import ModelClient, parse_json
from src.rag.retriever import Retriever
from src.services.history import history_for
from src.services.item_discovery import ItemDiscovery
from src.utils.errors import AuthenticationError, ResponseFormatError


class FakeLLM:
    def __init__(self, payloads):
        self.payloads, self.calls = iter(payloads), []

    def complete(self, system, payload, role="query"):
        self.calls.append((role, payload))
        return next(self.payloads)


def evidence(number=1):
    return {"id": "a", "evidence_id": f"E{number}", "evidence_number": number, "text": "공식 법률 원문",
            "metadata": {"title": "법령 제3조", "source_type": "항목 메타데이터"}}


@pytest.mark.parametrize("payload", [
    {"answer": "임차인은 무조건 승소한다 [근거 99]", "supported": True},
    {"answer": "근거 없는 단정", "supported": True},
    {"answer": "내용 [근거 1]", "supported": False},
    {"answer": 123, "supported": True},
])
def test_unsupported_answers_and_invented_citations_are_blocked(payload):
    result = validate_answer(payload, [evidence()])
    assert result["answer"] == UNKNOWN
    assert result["sources"] == []


def test_only_actually_cited_evidence_is_returned():
    result = validate_answer({"answer": "설명 [근거 2]", "supported": True, "cited_source_ids": ["E1", "E999"]}, [evidence(), evidence(2)])
    assert result["cited_source_ids"] == ["E2"]
    assert [x["evidence_number"] for x in result["sources"]] == [2]


def test_no_evidence_does_not_call_answer_model():
    llm = FakeLLM([])
    result = Generator(llm).generate("아무 질문", [])
    assert result["answer"] == UNKNOWN and not llm.calls


def test_graph_has_exact_retrieve_generate_path(settings, store, item):
    Indexer(settings, store).index_metadata([item])
    llm = FakeLLM([{"answer": "설명 [근거 1]", "supported": True}])
    graph = build_graph(Retriever(settings, store), Generator(llm))
    assert set(graph.get_graph().nodes) == {"__start__", "retrieve", "generate", "__end__"}
    result = graph.invoke({"question": "대항력", "selected_item_id": item.item_id})
    assert result["cited_source_ids"] == ["E1"]


def test_selected_original_is_indexed_even_if_llm_excludes_every_candidate(settings, store, item, document):
    class API:
        network_calls = 0

        def search(self, *args, **kwargs):
            return [], 0

        def fetch(self, candidate):
            return [document]

    indexer = Indexer(settings, store)
    llm = FakeLLM([{"queries": ["대항력", "전입신고", "주민등록"]}, {"selected_document_ids": []}])
    collector = Collector(settings, API(), llm, indexer, [item], [document])
    trace = collector.collect(item, "대항력이 있나요?")
    assert f"law:{item.law_id}" in trace["selected_document_ids"]
    assert store.counts()[settings.docs_collection] > 0
    cached = collector.collect(item, "대항력이 있나요?")
    assert cached["cache_hit"] is True
    assert len(llm.calls) == 2


def test_item_and_mode_conversations_are_isolated():
    state = {}
    history_for(state, "a").append({"role": "user", "content": "first"})
    assert history_for(state, "b") == []
    assert history_for(state, "a", "contract") == []
    assert history_for(state, "a")[0]["content"] == "first"


def test_discovery_filters_unknown_ids_and_duplicates(settings, store, item):
    Indexer(settings, store).index_metadata([item])
    llm = FakeLLM([{"queries": ["대항력", "주민등록"]}, {"items": [{"item_id": item.item_id, "reason": "주민등록 요건"},
                               {"item_id": "made-up", "reason": "bad"},
                               {"item_id": item.item_id, "reason": "duplicate"}]}])
    results = ItemDiscovery(settings, store, llm, [item]).discover("대항력 요건")
    assert len(results) == 1 and results[0]["item"].item_id == item.item_id


def test_missing_gms_key_is_handled_before_a_model_call(settings):
    with pytest.raises(AuthenticationError, match="GMS_API_KEY"):
        ModelClient(replace(settings, gms_api_key="")).check_connection()


def test_json_parser_accepts_fences_and_rejects_non_objects():
    assert parse_json('```json\n{"queries": []}\n```') == {"queries": []}
    with pytest.raises(ResponseFormatError):
        parse_json("non-json")
    with pytest.raises(ResponseFormatError):
        parse_json("[]")


def test_discovery_normalizes_long_conditions_and_caches_selection(settings, item):
    class Store:
        def __init__(self):
            self.queries = []

        def search(self, collection_name, question, k):
            self.queries.append(question)
            return [{"id": item.item_id, "text": item.index_text()}] if question == "주민등록" else []

    store = Store()
    llm = FakeLLM([{"queries": ["주민등록"]}, {"items": [{"item_id": item.item_id, "reason": "대항력 요건"}]}])
    discovery = ItemDiscovery(settings, store, llm, [item])
    first = discovery.discover("집에 들어간 뒤 전입 절차가 필요한가요?")
    second = discovery.discover("집에 들어간 뒤 전입 절차가 필요한가요?")
    assert first == second and first[0]["item"].item_id == item.item_id
    assert store.queries == ["집에 들어간 뒤 전입 절차가 필요한가요?", "주민등록"]
    assert len(llm.calls) == 2
