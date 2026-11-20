from dataclasses import replace
import hashlib

import numpy as np
import pytest

from config import Settings
from src.data.models import LegalDocument, LegalItem
from src.vectorstore.chroma_store import ChromaStore


class TinyEmbeddings:
    """Deterministic test-only vectors; the running service always uses a local learned model."""
    fingerprint = "test-only-deterministic-v1"

    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += len(texts)
        result = []
        for text in texts:
            vector = np.zeros(16, dtype=float)
            for token in text.split():
                vector[hashlib.sha256(token.encode()).digest()[0] % 16] += 1
            if not vector.any():
                vector[0] = 1
            result.append((vector / np.linalg.norm(vector)).tolist())
        return result

    def embed_query(self, text):
        return self.embed_documents([text])[0]


@pytest.fixture
def settings(tmp_path):
    return replace(Settings(), data_dir=tmp_path, legal_api_key="fixture-oc", gms_api_key="fixture-gms",
                   api_min_interval=0, api_max_retries=0, min_similarity=-1, chunk_size=120, chunk_overlap=20)


@pytest.fixture
def embeddings():
    return TinyEmbeddings()


@pytest.fixture
def store(settings, embeddings):
    return ChromaStore(settings, embeddings)


@pytest.fixture
def item():
    return LegalItem("article:001248:000300", "001248", "276291", "주택임대차보호법", "제3조", "대항력 등",
                     "주택 임대차", "대항력 주민등록 인도", "20260102",
                     "https://www.law.go.kr/법령/주택임대차보호법/제3조",
                     "임차인이 주택의 인도와 주민등록을 마친 때에는 그 다음 날부터 제삼자에 대하여 효력이 생긴다.",
                     "2026-10-23", "law:001248:000300")


@pytest.fixture
def document(item):
    return LegalDocument(item.document_id, "law", "주택임대차보호법 제3조 (대항력 등)", item.text,
                         item.source_url, item.law_name, item.article_no, item.effective_date, item.collected_at,
                         extra={"law_id": item.law_id, "master_id": item.master_id,
                                "article_title": item.article_title, "article_code": "000300"})
