from src.utils.hashing import text_hash


def deduplicate(hits):
    """One body once across the two stores; keep distinct chunks from the same document."""
    ids, bodies, source_chunks, output = set(), set(), set(), []
    for hit in hits:
        metadata = hit["metadata"]
        digest = metadata.get("text_hash") or text_hash(hit["text"])
        source_chunk = (metadata.get("source_url", ""), digest)
        if hit["id"] in ids or digest in bodies or source_chunk in source_chunks:
            continue
        ids.add(hit["id"])
        bodies.add(digest)
        source_chunks.add(source_chunk)
        output.append(hit)
    return output
