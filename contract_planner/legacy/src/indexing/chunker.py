"""Split inside one article only; prefer paragraph/호 boundaries before a hard cut."""

from config import Settings
from src.data.models import LegalDocument
from src.utils.hashing import text_hash


def split_text(text: str, size=1000, overlap=200):
    if not 0 <= overlap < size:
        raise ValueError("overlap must be smaller than size")
    text = text.strip()
    start, chunks = 0, []
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            minimum = start + max(overlap + 1, size // 2)
            boundary = max(text.rfind("\n", minimum, end), text.rfind(". ", minimum, end))
            if boundary >= minimum:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def document_chunks(document: LegalDocument, item_id: str, query: str, settings: Settings):
    """Stable item+document+index IDs; never truncate a UTF-8 byte sequence."""
    seen = set()
    for index, text in enumerate(split_text(document.text, settings.chunk_size, settings.chunk_overlap)):
        digest = text_hash(text)
        if digest in seen:
            continue
        seen.add(digest)
        metadata = {
            "chunk_id": f"{item_id}:{document.document_id}:{index}", "document_id": document.document_id,
            "document_type": document.document_type, "title": document.title,
            "law_name": document.law_name, "article_no": document.article_no,
            "source": document.source, "source_url": document.source_url, "source_type": "외부 문서",
            "effective_date": document.effective_date, "collected_at": document.collected_at,
            "query": query, "related_item_id": item_id, "chunk_index": index,
            "text_hash": digest, "index_version": f"{settings.chunk_size}:{settings.chunk_overlap}",
            "document_hash": text_hash(document.text),
        }
        for name in ("case_number", "decision_date", "court", "referenced_articles", "referenced_cases"):
            metadata[name] = str(document.extra.get(name, ""))
        # Store the full paragraph verbatim; prefixes aid embedding without changing quoted evidence.
        yield {"id": metadata["chunk_id"], "text": text, "metadata": metadata,
               "embedding_text": f"{document.title}\n{text}"}
