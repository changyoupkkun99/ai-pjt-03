import json
import re

from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError as OpenAIAuthError

from src.utils.errors import AuthenticationError, ResponseFormatError, ServiceError


def parse_json(content):
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    content = str(content).strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.S)
    if match:
        content = match.group(1)
    try:
        payload = json.loads(content)
    except (ValueError, TypeError):
        raise ResponseFormatError("AI가 지정된 JSON 형식으로 응답하지 않았습니다. 다시 시도하세요.") from None
    if not isinstance(payload, dict):
        raise ResponseFormatError("AI 응답이 JSON 객체 형식이 아닙니다.")
    return payload


class ModelClient:
    def __init__(self, settings):
        self.settings = settings
        self._models = {}
        self.calls = {"query": 0, "answer": 0}

    def _get(self, role):
        if not self.settings.gms_api_key:
            raise AuthenticationError("AI API Key가 없습니다. .env의 GMS_API_KEY를 설정하세요.")
        if role not in self._models:
            self._models[role] = ChatOpenAI(
                model=self.settings.query_model if role == "query" else self.settings.answer_model,
                api_key=self.settings.gms_api_key, base_url=self.settings.gms_base_url,
                timeout=self.settings.api_timeout, max_retries=self.settings.api_max_retries,
            )
        return self._models[role]

    def complete(self, system, payload, role="query", json_output=True):
        model = self._get(role)
        try:
            self.calls[role] += 1
            with tracing_context(enabled=False):
                response = model.invoke([
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ])
        except OpenAIAuthError:
            raise AuthenticationError("AI API 인증에 실패했습니다. API Key 활성화 상태와 만료일을 확인하세요.") from None
        except (APIConnectionError, APITimeoutError):
            raise ServiceError("AI API에 연결하지 못했습니다. 네트워크 상태를 확인하고 다시 시도하세요.") from None
        except APIStatusError as exc:
            if exc.status_code == 403:
                raise AuthenticationError("AI API 인증에 실패했습니다. API Key 활성화 상태와 만료일을 확인하세요.") from None
            if exc.status_code in {400, 404}:
                raise ServiceError("AI 모델 호출에 실패했습니다. QUERY_MODEL, ANSWER_MODEL과 GMS 접속 주소를 확인하세요.") from None
            if exc.status_code == 429:
                raise ServiceError("AI API 호출 한도에 도달했습니다. 사용량을 확인하고 잠시 후 다시 시도하세요.") from None
            raise ServiceError("AI API가 응답을 완료하지 못했습니다. 잠시 후 다시 시도하세요.") from None
        content = response.content
        return parse_json(content) if json_output else str(content)

    def check_connection(self):
        result = {}
        for role in ("query", "answer"):
            payload = self.complete('JSON만 반환하라: {"status": "ok"}', {"action": "연결 확인"}, role)
            if payload.get("status") != "ok":
                raise ResponseFormatError("모델 연결 확인 응답이 예상과 다릅니다.")
            result[role] = self.settings.query_model if role == "query" else self.settings.answer_model
        return result
