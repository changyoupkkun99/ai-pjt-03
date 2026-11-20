"""Index the bundled official snapshot, or a small housing-law slice for a smoke test."""

import argparse
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from backend import ROOT
from config import Settings
from src.data.metadata_loader import load_items, load_documents
from src.indexing.indexer import Indexer
from src.vectorstore.chroma_store import ChromaStore
from src.vectorstore.embeddings import LocalEmbeddings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sample", action="store_true", help="주택임대차보호법의 관련 조문 6개만 색인"
    )
    args = parser.parse_args()
    settings = Settings(data_dir=Path(os.getenv("RAG_DATA_DIR", ROOT / ".runtime/rag")))
    items = load_items(ROOT / "legacy/data/processed/law_items.jsonl")
    if args.sample:
        items = [
            x
            for x in items
            if x.law_name == "주택임대차보호법"
            and x.article_no
            in {"제3조", "제3조의2", "제4조", "제6조", "제6조의3", "제10조"}
        ]
    docs = {
        d.document_id: d
        for d in load_documents(ROOT / "legacy/data/processed/source_documents.jsonl")
    }
    store = ChromaStore(settings, LocalEmbeddings(settings))
    index = Indexer(settings, store)
    index.index_metadata(items)
    for item in items:
        index.index_documents([docs[item.document_id]], item.item_id)
    print(store.counts())
