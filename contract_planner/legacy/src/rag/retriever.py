from src.rag.deduplicator import deduplicate
from src.utils.errors import ServiceError


class Retriever:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    def retrieve(self, question, item_id, top_k=None, search_type=None):
        top_k = top_k or self.settings.top_k
        search_type = search_type or self.settings.search_type
        selected = self.store.get(self.settings.meta_collection, item_id)
        if selected is None:
            raise ServiceError("선택 항목의 메타데이터 색인이 없습니다. 데이터 색인을 먼저 실행하세요.")
        meta = self.store.search(self.settings.meta_collection, question, k=top_k, search_type=search_type)
        documents = self.store.search(self.settings.docs_collection, question, k=top_k * 2,
                                      search_type=search_type, where={"related_item_id": item_id})
        # Mandatory anchor precedes score-sorted candidates so deduplication cannot remove it.
        candidates = sorted(meta + documents, key=lambda hit: hit["score"], reverse=True)
        merged = deduplicate([selected] + candidates)[:top_k]
        for index, evidence in enumerate(merged, 1):
            evidence["evidence_id"] = f"E{index}"
            evidence["evidence_number"] = index
        return merged
