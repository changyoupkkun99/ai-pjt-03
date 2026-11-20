"""Central configuration. Model IDs were verified against GMS on 2026-10-23."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DISCLAIMER = (
    "본 서비스는 공개된 법률 자료를 기반으로 정보를 제공하며, "
    "변호사 등 전문가의 법률 자문을 대신하지 않습니다."
)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    gms_api_key: str = field(default="", repr=False)
    gms_base_url: str = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/"
    query_model: str = "gpt-6-luna"
    answer_model: str = "gpt-6.1-sol"
    legal_api_key: str = field(default="", repr=False)
    legal_api_base_url: str = "https://www.law.go.kr/DRF/"
    legal_api_mode: str = "live"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "auto"
    embedding_batch_size: int = 8
    embedding_max_length: int = 1024
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 6
    fetch_k: int = 30
    search_type: str = "similarity"
    mmr_lambda: float = 0.7
    min_similarity: float = 0.30
    api_timeout: float = 60
    api_min_interval: float = 0.4
    api_max_retries: int = 2
    api_cache_ttl_hours: int = 24
    max_selected_documents: int = 4
    data_dir: Path = ROOT / "data"
    meta_collection: str = "realestate_law_meta"
    docs_collection: str = "realestate_law_docs"

    def __post_init__(self):
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("CHUNK_OVERLAP은 0 이상, CHUNK_SIZE 미만이어야 합니다.")
        if self.search_type not in {"similarity", "mmr"}:
            raise ValueError("SEARCH_TYPE은 similarity 또는 mmr이어야 합니다.")
        if self.legal_api_mode not in {"example", "live"}:
            raise ValueError("LEGAL_API_MODE는 example 또는 live이어야 합니다.")
        if not 0 <= self.mmr_lambda <= 1 or not -1 <= self.min_similarity <= 1:
            raise ValueError("검색 점수 설정 범위를 확인하세요.")
        if min(self.top_k, self.fetch_k, self.embedding_batch_size, self.max_selected_documents) < 1:
            raise ValueError("건수 설정은 1 이상이어야 합니다.")
        if self.api_min_interval < 0 or self.api_timeout <= 0 or self.api_max_retries < 0:
            raise ValueError("API 시간 및 재시도 설정 범위를 확인하세요.")

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        defaults = cls()
        values = {}
        for name, f in cls.__dataclass_fields__.items():
            if name in {"meta_collection", "docs_collection"}:
                continue
            value = env(name.upper(), str(getattr(defaults, name)))
            original = getattr(defaults, name)
            if isinstance(original, int):
                value = int(value)
            elif isinstance(original, float):
                value = float(value)
            elif isinstance(original, Path):
                value = Path(value).expanduser().resolve()
            values[name] = value
        return cls(**values)
