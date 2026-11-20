from src.indexing.chunker import document_chunks
from src.utils.hashing import text_hash


class Indexer:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    def index_metadata(self, items, sync=False):
        records = []
        for item in items:
            metadata = {k: v for k, v in item.to_dict().items() if k != "text"}
            metadata.update({"title": f"{item.law_name} {item.article_no} ({item.article_title})",
                             "document_type": "law", "source_type": "항목 메타데이터",
                             "text_hash": text_hash(item.text), "body_text": item.text})
            records.append({"id": item.item_id, "text": item.index_text(), "metadata": metadata})
        changed = self.store.upsert(self.settings.meta_collection, records)
        if sync:
            # Only complete snapshot commands opt in: a partial upsert must never prune other items.
            collection = self.store.collections[self.settings.meta_collection]
            existing = collection.get(include=[])["ids"]
            stale = sorted(set(existing) - {r["id"] for r in records})
            if stale:
                collection.delete(ids=stale)
                for item_id in stale:
                    self.store.collections[self.settings.docs_collection].delete(where={"related_item_id": item_id})
        return changed

    def index_documents(self, documents, item_id, query=""):
        changed = 0
        seen = set()
        for document in documents:
            key = (document.document_id, document.source_url, text_hash(document.text))
            if key in seen:
                continue
            seen.add(key)
            chunks = list(document_chunks(document, item_id, query, self.settings))
            changed += self.store.sync_document(item_id, document.document_id, chunks)
        return changed
