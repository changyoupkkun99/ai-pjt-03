"""Local, cached dense BGE-M3 embedding; no external generation API credits."""

from threading import RLock
import json
import sqlite3

from config import Settings
from src.utils.errors import ServiceError
from src.utils.hashing import object_hash


class LocalEmbeddings:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = RLock()
        self._cache_path = settings.data_dir / "cache" / "embeddings.sqlite3"
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._cache_path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")

    @property
    def fingerprint(self):
        return f"sentence-transformers:{self.settings.embedding_model}:{self.settings.embedding_max_length}:normalized"

    def _load(self):
        if self._model is None:
            try:
                import gc
                import torch
                from sentence_transformers import SentenceTransformer
                device = self.settings.embedding_device
                if device == "auto":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                def load_on(target):
                    # Original BGE-M3 implementation also used fp16 on GPU.
                    kwargs = {"dtype": torch.float16} if target == "cuda" else {}
                    return SentenceTransformer(self.settings.embedding_model, device=target,
                                               trust_remote_code=False, model_kwargs=kwargs)
                try:
                    self._model = load_on(device)
                except torch.OutOfMemoryError:
                    if self.settings.embedding_device != "auto" or device != "cuda":
                        raise
                    gc.collect()
                    torch.cuda.empty_cache()
                    self._model = load_on("cpu")
                self._model.max_seq_length = self.settings.embedding_max_length
            except Exception:
                raise ServiceError(
                    "로컬 임베딩 모델을 준비하지 못했습니다. 의존성 설치와 모델 다운로드 연결을 확인하세요. "
                    "메모리가 부족하면 EMBEDDING_DEVICE=cpu와 작은 배치 크기를 설정하세요."
                ) from None
        return self._model

    def embed_documents(self, texts):
        if not texts:
            return []
        with self._lock:
            keys = [object_hash([self.fingerprint, text]) for text in texts]
            cached = {}
            with sqlite3.connect(self._cache_path) as connection:
                for start in range(0, len(keys), 500):
                    batch = keys[start:start + 500]
                    rows = connection.execute("SELECT key, vector FROM vectors WHERE key IN (" + ",".join("?" for _ in batch) + ")", batch)
                    cached.update({key: json.loads(value) for key, value in rows})
            missing = list(dict.fromkeys(key for key in keys if key not in cached))
            if not missing:
                return [cached[key] for key in keys]
            text_by_key = dict(zip(keys, texts))
            model = self._load()
            try:
                try:
                    vectors = model.encode([text_by_key[key] for key in missing], batch_size=self.settings.embedding_batch_size,
                                           normalize_embeddings=True, show_progress_bar=False).tolist()
                except Exception as exc:
                    import torch
                    if not isinstance(exc, torch.OutOfMemoryError):
                        raise
                    torch.cuda.empty_cache()
                    vectors = model.encode([text_by_key[key] for key in missing], batch_size=1,
                                           normalize_embeddings=True, show_progress_bar=False).tolist()
                cached.update(dict(zip(missing, vectors)))
                with sqlite3.connect(self._cache_path) as connection:
                    connection.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                                           [(key, json.dumps(cached[key])) for key in missing])
                return [cached[key] for key in keys]
            except Exception:
                raise ServiceError("로컬 임베딩에 실패했습니다. 배치 크기와 장치 메모리를 확인하세요.") from None

    def embed_query(self, text):
        return self.embed_documents([text])[0]
