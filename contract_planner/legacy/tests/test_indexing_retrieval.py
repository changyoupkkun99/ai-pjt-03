from dataclasses import replace

import pytest

from src.indexing.chunker import document_chunks, split_text
from src.indexing.indexer import Indexer
from src.rag.deduplicator import deduplicate
from src.rag.retriever import Retriever
from src.utils.errors import ServiceError
from src.vectorstore.chroma_store import ChromaStore


def test_korean_chunking_preserves_tail_and_limits():
    text = "① 주택의 인도와 주민등록.\n" * 25 + "마지막 문장 🏠"
    chunks = split_text(text, 100, 20)
    assert chunks[-1].endswith("마지막 문장 🏠")
    assert all(len(chunk) <= 100 and "�" not in chunk for chunk in chunks)
    assert len(chunks) > 1
    with pytest.raises(ValueError):
        split_text(text, 100, 100)


def test_stable_chunk_ids_include_both_item_and_document(settings, document):
    one = list(document_chunks(document, "item-a", "대항력", settings))
    two = list(document_chunks(document, "item-b", "대항력", settings))
    assert {r["id"] for r in one}.isdisjoint({r["id"] for r in two})
    assert list(document_chunks(document, "item-a", "대항력", settings)) == one


def test_reindex_does_not_add_chunks_or_repeat_embedding(settings, store, embeddings, item, document):
    indexer = Indexer(settings, store)
    assert indexer.index_metadata([item]) == 1
    assert indexer.index_documents([document], item.item_id) > 0
    counts, calls = store.counts(), embeddings.calls
    assert indexer.index_metadata([item]) == 0
    assert indexer.index_documents([document], item.item_id) == 0
    assert store.counts() == counts
    assert embeddings.calls == calls


def test_rechunk_removes_only_stale_chunks_of_its_document(settings, store, item, document):
    long_document = replace(document, text="대항력 주민등록 " * 100)
    indexer = Indexer(settings, store)
    indexer.index_documents([long_document], item.item_id)
    indexer.index_documents([document], "other-item")
    bigger = replace(settings, chunk_size=500, chunk_overlap=50)
    Indexer(bigger, store).index_documents([long_document], item.item_id)
    collection = store.collections[settings.docs_collection]
    own = collection.get(where={"related_item_id": item.item_id})
    assert len(own["ids"]) == len(list(document_chunks(long_document, item.item_id, "", bigger)))
    assert len(collection.get(where={"related_item_id": "other-item"})["ids"]) == 1


def test_distinct_chunks_of_same_url_survive_deduplication():
    hits = [{"id": "a", "text": "본문 가", "metadata": {"source_url": "same"}},
            {"id": "b", "text": "본문 나", "metadata": {"source_url": "same"}},
            {"id": "c", "text": "본문 가", "metadata": {"source_url": "other"}}]
    assert [h["id"] for h in deduplicate(hits)] == ["a", "b"]


@pytest.mark.parametrize("search_type", ["similarity", "mmr"])
def test_two_collection_search_keeps_selected_metadata_and_topk(settings, store, item, document, search_type):
    indexer = Indexer(settings, store)
    indexer.index_metadata([item, replace(item, item_id="another", text="다른 법률 주제 내용")])
    indexer.index_documents([replace(document, text="외부 법률 원문 대항력 관련 판결")], item.item_id)
    result = Retriever(settings, store).retrieve("대항력 주민등록", item.item_id, 3, search_type)
    assert result[0]["id"] == item.item_id
    assert {r["metadata"]["source_type"] for r in result} == {"항목 메타데이터", "외부 문서"}
    assert len(result) <= 3
    assert [r["evidence_number"] for r in result] == list(range(1, len(result) + 1))


def test_embedding_model_change_requires_a_separate_index(settings, store, embeddings):
    embeddings.fingerprint = "changed-model"
    with pytest.raises(ServiceError, match="임베딩 설정"):
        ChromaStore(settings, embeddings)


def test_full_snapshot_sync_prunes_retired_items_but_partial_upsert_does_not(settings, store, item, document):
    indexer = Indexer(settings, store)
    second = replace(item, item_id="retired")
    indexer.index_metadata([item, second])
    indexer.index_documents([document], second.item_id)
    indexer.index_metadata([item])
    assert store.get(settings.meta_collection, second.item_id)
    indexer.index_metadata([item], sync=True)
    assert store.get(settings.meta_collection, second.item_id) is None
    assert store.collections[settings.docs_collection].get(where={"related_item_id": second.item_id})["ids"] == []
