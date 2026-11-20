"""Persistent two-collection store with embedding compatibility and idempotent upserts."""

from threading import RLock

import chromadb
from chromadb.config import Settings as ChromaSettings
import numpy as np

from config import Settings
from src.utils.errors import ServiceError
from src.utils.hashing import object_hash


class ChromaStore:
    def __init__(self, settings: Settings, embeddings):
        self.settings, self.embeddings = settings, embeddings
        self._lock = RLock()
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(settings.chroma_dir), settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collections = {}
        for name in (settings.meta_collection, settings.docs_collection):
            collection = self.client.get_or_create_collection(
                name, embedding_function=None,
                metadata={"hnsw:space": "cosine", "embedding_fingerprint": embeddings.fingerprint},
            )
            if collection.metadata.get("embedding_fingerprint") != embeddings.fingerprint:
                raise ServiceError(
                    "저장된 색인과 현재 임베딩 설정이 다릅니다. "
                    "기존 설정을 복원하거나 DATA_DIR을 새 폴더로 지정하여 색인하세요."
                )
            self.collections[name] = collection

    def counts(self):
        return {name: collection.count() for name, collection in self.collections.items()}

    def upsert(self, collection_name, records):
        if not records:
            return 0
        collection = self.collections[collection_name]
        # One source record per ID, stable even when the same query returns it repeatedly.
        records = list({r["id"]: r for r in records}.values())
        changed = []
        with self._lock:
            for start in range(0, len(records), 100):
                batch = records[start:start + 100]
                current = collection.get(ids=[r["id"] for r in batch], include=["metadatas"])
                fingerprints = {identity: meta.get("record_hash") for identity, meta in zip(current["ids"], current["metadatas"])}
                for record in batch:
                    semantic_metadata = {k: v for k, v in record["metadata"].items() if k not in {"collected_at", "query", "record_hash"}}
                    fingerprint = object_hash({"text": record["text"], "metadata": semantic_metadata,
                                               "embedding_text": record.get("embedding_text", record["text"])})
                    if fingerprints.get(record["id"]) != fingerprint:
                        record = {**record, "metadata": {**record["metadata"], "record_hash": fingerprint}}
                        changed.append(record)
            for start in range(0, len(changed), 32):
                batch = changed[start:start + 32]
                vectors = self.embeddings.embed_documents([r.get("embedding_text", r["text"]) for r in batch])
                collection.upsert(ids=[r["id"] for r in batch], documents=[r["text"] for r in batch],
                                  metadatas=[r["metadata"] for r in batch], embeddings=vectors)
        return len(changed)

    def sync_document(self, item_id, document_id, records):
        """Replace only this item/document's stale chunks after a successful upsert."""
        collection = self.collections[self.settings.docs_collection]
        with self._lock:
            old = collection.get(where={"$and": [{"related_item_id": item_id}, {"document_id": document_id}]}, include=[])["ids"]
            changed = self.upsert(self.settings.docs_collection, records)
            stale = sorted(set(old) - {r["id"] for r in records})
            if stale:
                collection.delete(ids=stale)
        return changed

    def get(self, collection_name, identity):
        result = self.collections[collection_name].get(ids=[identity], include=["documents", "metadatas"])
        if not result["ids"]:
            return None
        return {"id": identity, "text": result["documents"][0], "metadata": result["metadatas"][0], "score": 1.0}

    def search(self, collection_name, question, k=10, search_type="similarity", where=None):
        collection = self.collections[collection_name]
        count = collection.count()
        if count == 0:
            return []
        query_vector = self.embeddings.embed_query(question)
        result = collection.query(
            query_embeddings=[query_vector], n_results=min(count, max(k, self.settings.fetch_k)), where=where,
            include=["documents", "metadatas", "distances", "embeddings"],
        )
        hits = [
            {"id": identity, "text": result["documents"][0][i], "metadata": result["metadatas"][0][i],
             "score": float(1 - result["distances"][0][i]), "_embedding": result["embeddings"][0][i]}
            for i, identity in enumerate(result["ids"][0])
            if 1 - result["distances"][0][i] >= self.settings.min_similarity
        ]
        if search_type == "mmr":
            chosen, pool = [], hits.copy()
            while pool and len(chosen) < k:
                def utility(hit):
                    redundancy = max((float(np.dot(hit["_embedding"], x["_embedding"])) for x in chosen), default=0)
                    weight = self.settings.mmr_lambda
                    return weight * hit["score"] - (1 - weight) * redundancy
                winner = max(range(len(pool)), key=lambda i: utility(pool[i]))
                chosen.append(pool.pop(winner))
            hits = chosen
        for hit in hits:
            hit.pop("_embedding", None)
        return hits[:k]
