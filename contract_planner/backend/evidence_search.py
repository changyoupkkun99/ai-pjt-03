"""Shortlist the archived statute collection locally, then rank with GMS embeddings."""

import math
import re
from functools import lru_cache

from src.data.metadata_loader import load_items

from . import ROOT
from .providers import gms_embeddings


def grams(text):
    return {
        part[i : i + 2]
        for part in re.findall(r"[가-힣a-z0-9]+", text.lower())
        for i in range(len(part) - 1)
    }


@lru_cache(maxsize=1)
def statutes():
    return [
        (item, grams(item.law_name + " " + item.article_title), grams(item.text))
        for item in load_items(ROOT / "legacy/data/processed/law_items.jsonl")
    ]


def search_gms(question, store):
    query = grams(question)
    compact_question = re.sub(r"\s+", "", question)
    scored = []
    for item, title, body in statutes():
        reference = re.sub(r"\s+", "", item.law_name + item.article_no)
        named = bool(re.search(re.escape(reference) + r"(?!의\d)", compact_question))
        score = 3 * len(query & title) + len(query & body) + 40 * named
        if score:
            scored.append((score, item, named))
    shortlist = sorted(scored, key=lambda candidate: candidate[0], reverse=True)[:8]
    if not shortlist:
        return []
    excerpts = [
        item.law_name
        + " "
        + item.article_no
        + " "
        + item.article_title
        + " "
        + item.text[:250]
        for _, item, _ in shortlist
    ]
    q, *docs = gms_embeddings(store, [question, *excerpts])
    qnorm = math.hypot(*q)
    if not qnorm or any(not math.hypot(*doc) for doc in docs):
        return []
    ranks = sorted(
        range(len(shortlist)),
        key=lambda i: (
            shortlist[i][2],
            sum(a * b for a, b in zip(q, docs[i])) / (qnorm * math.hypot(*docs[i])),
        ),
        reverse=True,
    )
    return [
        dict(
            id=item.item_id,
            title=f"{item.law_name} {item.article_no} ({item.article_title})",
            url=item.source_url,
            text=item.text[:550],
            effective=item.effective_date,
            kind="law",
        )
        for item in (shortlist[i][1] for i in ranks[:3])
    ]
