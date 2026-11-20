"""Minimal provider protocol compatible with the previous Generator/ContractReview."""

import hashlib
import json
import math
import os
from collections import OrderedDict
from threading import Lock
from typing import Protocol
import requests


class ProviderError(Exception):
    pass


GMS_MODELS = ("gpt-6-astra", "gpt-6-sol", "claude-fable-5-1", "claude-opus-5-5")


def json_answer(answer):
    parsed = json.loads(
        answer.strip().removeprefix("```json").removesuffix("```").strip()
    )
    if not isinstance(parsed, dict):
        raise ValueError()
    return parsed


def gms_completion(store, payload, input_estimate, timeout, parse=str, effort="low"):
    key = os.getenv("GMS_API_KEY", "").strip()
    if not key:
        raise ProviderError("GMS_KEY_MISSING")
    models = tuple(
        dict.fromkeys(model for model in (payload.get("model"), *GMS_MODELS) if model)
    )
    for model in models:
        try:
            answer, actual_model = gms_attempt(
                store, payload | {"model": model}, input_estimate, timeout, key, effort
            )
            return parse(answer), actual_model
        except ValueError:
            last_error = ProviderError("GMS_FORMAT")
        except ProviderError as exc:
            last_error = exc
    raise last_error


def gms_attempt(store, payload, input_estimate, timeout, key, effort):
    model = payload["model"]
    max_tokens = payload["max_tokens"]
    reservation = store.reserve_gms(model, input_estimate, max_tokens)

    headers = {"Authorization": "Bearer " + key}
    if model.startswith("claude-"):
        url = "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1/messages"
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    else:
        url = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/chat/completions"
        system = payload["system"]
        messages = [{"role": "developer", "content": system}]
        for message in payload["messages"]:
            content = message["content"]
            if isinstance(content, list):
                content = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:"
                            + part["source"]["media_type"]
                            + ";base64,"
                            + part["source"]["data"],
                            "detail": "high",
                        },
                    }
                    if part["type"] == "image"
                    else part
                    for part in content
                ]
            messages.append({"role": message["role"], "content": content})
        payload = dict(
            model=model,
            messages=messages,
            reasoning_effort=effort,
            max_completion_tokens=max_tokens,
        )

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=timeout, allow_redirects=False
        )
    except requests.RequestException:
        raise ProviderError("GMS_UNAVAILABLE") from None
    if not response.ok or response.is_redirect:
        if 400 <= response.status_code < 500:
            store.finish_gms(reservation)
        raise ProviderError(f"GMS_HTTP_{response.status_code}")
    try:
        data = response.json()
        usage = data.get("usage") or {}
        anthropic = model.startswith("claude-")
        names = (
            ("input_tokens", "output_tokens")
            if anthropic
            else ("prompt_tokens", "completion_tokens")
        )
        used = [usage.get(name) for name in names]
        if all(type(value) is int and value >= 0 for value in used):
            store.finish_gms(reservation, used)
        if anthropic:
            reason = data.get("stop_reason")
            answer = "\n".join(
                part["text"] for part in data["content"] if part["type"] == "text"
            )
        else:
            choice = data["choices"][0]
            reason = choice["finish_reason"]
            answer = choice["message"]["content"]
        if (
            reason not in {"end_turn", "stop"}
            or not isinstance(answer, str)
            or not answer.strip()
        ):
            raise ValueError()
        actual = data.get("model")
        return answer.strip(), actual if isinstance(actual, str) and actual.startswith(
            model
        ) else model
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ProviderError("GMS_FORMAT") from None


def gms_embeddings(store, texts):
    key = os.getenv("GMS_API_KEY", "").strip()
    if not key:
        raise ProviderError("GMS_KEY_MISSING")
    model = "text-embedding-3-large"
    reservation = store.reserve_gms(model, 100 + sum(map(len, texts)), 0)
    try:
        response = requests.post(
            "https://gms.ssafy.io/gmsapi/api.openai.com/v1/embeddings",
            headers={"Authorization": "Bearer " + key},
            json={"model": model, "input": texts, "dimensions": 512},
            timeout=(4, 15),
            allow_redirects=False,
        )
        if not response.ok or response.is_redirect:
            if 400 <= response.status_code < 500:
                store.finish_gms(reservation)
            raise ProviderError(f"GMS_HTTP_{response.status_code}")
        data = response.json()
        used = (data.get("usage") or {}).get("prompt_tokens")
        if type(used) is int and used >= 0:
            store.finish_gms(reservation, (used, 0))
        rows = sorted(data["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError()
        vectors = [row["embedding"] for row in rows]
        if any(
            len(vector) != 512 or any(not math.isfinite(x) for x in vector)
            for vector in vectors
        ):
            raise ValueError()
        return vectors
    except (
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        requests.RequestException,
    ):
        raise ProviderError("GMS_EMBEDDING_UNAVAILABLE") from None


class Provider(Protocol):
    name: str

    def complete(self, system: str, payload: dict, role: str = "answer") -> dict: ...


class MockProvider:
    name = "mock"

    def complete(self, system, payload, role="answer"):
        if "warnings" in payload:
            return {
                "diagnosis": "모의 진단입니다. 메인 Agent의 도구 순서와 대안 후보 탐색 범위를 확인하세요."
            }
        if "contract_clause" in payload:
            return {"issues": ["특약 문구와 관련 조문의 적용 범위 확인"]}
        first = payload["evidence"][0]
        return {
            "answer": f"[근거 1] {first['title']} 원문과 계약서 문구를 대조하세요. (모의 검토)",
            "supported": True,
            "risk_level": "주의 필요",
        }


class ClaudeProvider:
    name = "anthropic"
    model_env = "CLAUDE_MODEL"
    default_model = "claude-fable-5-1"

    def __init__(self, store):
        self.store = store
        self.model = os.getenv(self.model_env, self.default_model)
        self.cache = OrderedDict()
        self.lock = Lock()

    def complete(self, system, payload, role="answer"):
        body = json.dumps(payload, ensure_ascii=False)
        if len(body) > 4200 or len(system) > 4000:
            raise ProviderError("INPUT_LIMIT")
        messages = {
            "model": self.model,
            "max_tokens": 1000 if role == "report" else 500,
            "system": system,
            "messages": [{"role": "user", "content": body}],
        }
        digest = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False).encode()
        ).hexdigest()
        with self.lock:
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest]
            try:
                parsed = self.answer(messages, len(system) + len(body))
            except (requests.RequestException, KeyError, TypeError, ValueError):
                raise ProviderError("FORMAT_ERROR") from None
            self.cache[digest] = parsed
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            return parsed

    def answer(self, messages, input_size):
        return json_answer(self.message(messages, input_size))

    def message(self, messages, input_size):
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("CLAUDE_KEY_MISSING")
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json=messages,
                timeout=(3, 15),
                allow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
            answer = "".join(
                x.get("text", "") for x in data["content"] if x["type"] == "text"
            )
            usage = data.get("usage") or {}
            estimated = "input_tokens" not in usage or "output_tokens" not in usage
            self.store.measure(
                self.name,
                self.model,
                usage.get("input_tokens", (input_size + 1) // 2),
                usage.get("output_tokens", (len(answer) + 1) // 2),
                estimated,
            )
            return answer
        except (requests.RequestException, KeyError, TypeError, ValueError):
            raise ProviderError("CLAUDE_UNAVAILABLE") from None


class GmsProvider(ClaudeProvider):
    name = "gms"
    model_env = "GMS_TEXT_MODEL"
    default_model = GMS_MODELS[0]

    def answer(self, messages, input_size):
        payload = messages | {
            "max_tokens": 2200 if messages["max_tokens"] == 1000 else 1400
        }
        parsed, model = gms_completion(
            self.store, payload, 256 + input_size, (5, 35), parse=json_answer
        )
        return parsed | {"_gms_model": model}


def get_provider(store):
    name = os.getenv("LLM_PROVIDER", "auto")
    if name == "auto":
        name = "gms" if os.getenv("GMS_API_KEY", "").strip() else "anthropic"
    if name == "gms":
        return GmsProvider(store)
    if name == "anthropic":
        return ClaudeProvider(store)
    if name == "mock":
        return MockProvider()
    raise ValueError("LLM_PROVIDER는 auto, gms, anthropic 또는 mock으로 설정하세요.")
