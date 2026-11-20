"""Prompt1 -> official search -> Prompt2 -> selected body -> article-aware indexing."""

from src.data.models import Candidate
from src.rag.prompts import QUERY_SYSTEM, SELECTION_SYSTEM
from src.utils.cache import DiskCache
from src.utils.errors import ResponseFormatError, ServiceError
from src.utils.hashing import text_hash


class Collector:
    def __init__(self, settings, api, llm, indexer, items, documents):
        self.settings, self.api, self.llm, self.indexer = settings, api, llm, indexer
        self.items = {item.item_id: item for item in items}
        self.documents = {document.document_id: document for document in documents}
        self.allowed_law_ids = {item.document_id for item in items}
        self.cache = DiskCache(settings.data_dir / "cache" / "pipeline", settings.api_cache_ttl_hours)

    def collect(self, item, question, history=None, progress=lambda _: None):
        recent = [m["content"][:1000] for m in (history or [])[-6:] if m.get("role") == "user"]
        signature = {"item": item.item_id, "text": text_hash(item.text), "question": text_hash(question),
                     "history": [text_hash(t) for t in recent], "model": self.settings.query_model,
                     "chunk": [self.settings.chunk_size, self.settings.chunk_overlap],
                     "embedding": self.indexer.store.embeddings.fingerprint,
                     "scope": text_hash(self.settings.legal_api_key),
                     "legal_base_url": self.settings.legal_api_base_url.rstrip("/"),
                     "gms_base_url": self.settings.gms_base_url.rstrip("/"),
                     "max_documents": self.settings.max_selected_documents, "version": 2}
        cached = self.cache.get("collection", signature)
        if cached is not None and all(self.indexer.store.get(self.settings.docs_collection, cid)
                                      for cid in cached.get("first_chunk_ids", [])):
            progress("수집 캐시 사용 중")
            return {**cached, "cache_hit": True, "new_chunks": 0, "network_calls": 0}
        progress("검색어 생성 중")
        query_payload = self.llm.complete(QUERY_SYSTEM, {
            "item": {"law_name": item.law_name, "article": item.article_no, "title": item.article_title},
            "question": question, "recent_user_questions": recent,
        })
        generated = query_payload.get("queries")
        if not isinstance(generated, list) or not all(isinstance(q, str) for q in generated):
            raise ResponseFormatError("검색어 추출 결과 형식이 올바르지 않습니다.")
        queries = list(dict.fromkeys([item.law_name] + [q.strip()[:120] for q in generated if q.strip()]))[:5]
        progress("법률 API 검색 중")
        candidates, warnings = {}, []
        calls_before = self.api.network_calls
        # Force the selected law into search and selection, regardless of LLM filtering.
        own_candidate = Candidate(f"law:{item.law_id}", "law", item.master_id, item.law_name,
                                  item.source_url, item.law_name, "선택 항목의 법령 원문", item.law_name)
        candidates[own_candidate.document_id] = own_candidate
        for query in queries:
            for kind in ("law", "prec", "expc"):
                try:
                    found, _ = self.api.search(query, document_type=kind, display=3, search=1 if kind == "law" else 2)
                    for candidate in found:
                        candidates.setdefault(candidate.document_id, candidate)
                except ServiceError as exc:
                    warnings.append(str(exc))
                    # Preserve collected official documents when the live source is unavailable.
                    break
        progress("관련 문서 선별 중")
        payload = self.llm.complete(SELECTION_SYSTEM, {
            "question": question, "selected_item": f"{item.law_name} {item.article_no} {item.article_title}",
            "max_documents": self.settings.max_selected_documents,
            "candidates": [{"document_id": c.document_id, "document_type": c.document_type,
                            "title": c.title, "law_name": c.law_name, "summary": c.summary[:500]}
                           for c in candidates.values()],
        })
        selected = payload.get("selected_document_ids")
        if not isinstance(selected, list) or not all(isinstance(x, str) for x in selected):
            raise ResponseFormatError("문서 선별 결과 형식이 올바르지 않습니다.")
        selected = [x for x in dict.fromkeys(selected) if x in candidates][:self.settings.max_selected_documents]
        if own_candidate.document_id not in selected:
            selected.insert(0, own_candidate.document_id)
        progress("문서 수집 중")
        # The selected article is mandatory even if the live API is down or the LLM excludes it.
        own = self.documents.get(item.document_id)
        collected = {own.document_id: own} if own else {}
        for identity in selected:
            try:
                for document in self.api.fetch(candidates[identity]):
                    if document.document_type == "law" and document.document_id not in self.allowed_law_ids:
                        continue
                    collected[document.document_id] = document
            except ServiceError as exc:
                warnings.append(str(exc))
        if not collected:
            raise ServiceError("색인할 공식 법률 본문을 확보하지 못했습니다. API 설정과 수집본을 확인하세요.")
        progress("문서 색인 중")
        changed = self.indexer.index_documents(list(collected.values()), item.item_id, " | ".join(queries))
        trace = {"queries": queries, "candidate_count": len(candidates), "selected_document_ids": selected,
                 "rejected_document_ids": [x for x in candidates if x not in selected],
                 "collected_document_count": len(collected), "new_chunks": changed,
                 "network_calls": self.api.network_calls - calls_before, "cache_hit": False,
                 "first_chunk_ids": [f"{item.item_id}:{d.document_id}:0" for d in collected.values()],
                 "warnings": list(dict.fromkeys(warnings))}
        # Do not cache incomplete or failed collections as successful.
        if not warnings:
            self.cache.set("collection", signature, trace)
        return trace
