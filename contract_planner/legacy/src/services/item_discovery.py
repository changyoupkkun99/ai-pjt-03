from src.rag.prompts import DISCOVERY_QUERY_SYSTEM, DISCOVERY_SYSTEM
from src.utils.cache import DiskCache
from src.utils.errors import ResponseFormatError
from src.utils.hashing import object_hash, text_hash


class ItemDiscovery:
    def __init__(self, settings, store, llm, items):
        self.settings, self.store, self.llm = settings, store, llm
        self.items = {item.item_id: item for item in items}
        self.cache = DiskCache(settings.data_dir / "cache" / "discovery", settings.api_cache_ttl_hours)

    def discover(self, condition, limit=8):
        key = {"condition": text_hash(condition), "model": self.settings.query_model,
               "items": object_hash([(i.item_id, text_hash(i.text)) for i in self.items.values()]), "version": 2}
        cached = self.cache.get("selection", key)
        if cached is not None:
            return [{"item": self.items[row["item_id"]], "reason": row["reason"]}
                    for row in cached if row["item_id"] in self.items][:limit]
        normalized = self.llm.complete(DISCOVERY_QUERY_SYSTEM, {"condition": condition})
        queries = normalized.get("queries")
        if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
            raise ResponseFormatError("조건 검색어 정규화 형식이 올바르지 않습니다.")
        # A long everyday sentence can miss a long article embedding. Union a few short
        # semantic queries; the same selection prompt still filters all candidates.
        candidates = {}
        for query in [condition] + [q.strip()[:120] for q in queries if q.strip()][:3]:
            for hit in self.store.search(self.settings.meta_collection, query, k=12):
                candidates.setdefault(hit["id"], hit)
        hits = list(candidates.values())
        if not hits:
            return []
        payload = self.llm.complete(DISCOVERY_SYSTEM, {
            "condition": condition,
            "candidates": [{"item_id": hit["id"], "text": hit["text"][:5000]} for hit in hits],
        })
        selected = payload.get("items")
        if not isinstance(selected, list):
            raise ResponseFormatError("조건 기반 탐색 결과 형식이 올바르지 않습니다.")
        allowed = {hit["id"] for hit in hits}
        seen, results = set(), []
        for row in selected:
            if not isinstance(row, dict):
                continue
            item_id, reason = row.get("item_id"), row.get("reason")
            if item_id in allowed and item_id in self.items and item_id not in seen and isinstance(reason, str):
                seen.add(item_id)
                results.append({"item": self.items[item_id], "reason": reason})
        self.cache.set("selection", key, [{"item_id": row["item"].item_id, "reason": row["reason"]} for row in results])
        return results[:limit]
