"""Index the bundled official snapshot without requiring a legal API credential."""

from config import Settings
from src.data.metadata_loader import load_documents, load_items
from src.indexing.indexer import Indexer
from src.utils.errors import ServiceError
from src.vectorstore.chroma_store import ChromaStore
from src.vectorstore.embeddings import LocalEmbeddings


def index_snapshot(settings):
    items = load_items(settings.processed_dir / "law_items.jsonl")
    documents = {d.document_id: d for d in load_documents(settings.processed_dir / "source_documents.jsonl")}
    if len(items) < 100:
        raise ServiceError("100건 이상의 공식 수집본을 먼저 준비하세요.")
    store = ChromaStore(settings, LocalEmbeddings(settings))
    indexer = Indexer(settings, store)
    changed_meta = indexer.index_metadata(items, sync=True)
    changed_docs = 0
    for item in items:
        changed_docs += indexer.index_documents([documents[item.document_id]], item.item_id, item.law_name)
    print({"changed_meta": changed_meta, "changed_docs": changed_docs, "counts": store.counts()}, flush=True)
    return store.counts()


if __name__ == "__main__":
    try:
        index_snapshot(Settings.from_env())
    except ServiceError as exc:
        print(str(exc))
        raise SystemExit(1) from None
