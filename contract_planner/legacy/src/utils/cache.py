"""Atomic disk cache; no API credentials, conversation text, or full prompts in cache keys."""

import json
from pathlib import Path
import time
from uuid import uuid4

from src.utils.hashing import object_hash


class DiskCache:
    def __init__(self, directory: Path, ttl_hours: float = 24):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_hours * 3600
        self.hits = 0
        self.misses = 0

    def _path(self, namespace: str, key) -> Path:
        return self.directory / f"{namespace}_{object_hash(key)}.json"

    def get(self, namespace: str, key):
        path = self._path(namespace, key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if self.ttl_seconds > 0 and time.time() - payload["created"] > self.ttl_seconds:
                self.misses += 1
                return None
            self.hits += 1
            return payload["data"]
        except (OSError, ValueError, KeyError):
            self.misses += 1
            return None

    def set(self, namespace: str, key, data):
        path = self._path(namespace, key)
        temp = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps({"created": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
