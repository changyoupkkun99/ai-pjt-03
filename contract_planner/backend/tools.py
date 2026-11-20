"""Schema-checked adapters. Replay and HTTP share the previous DRF parser."""

import json
import os
from datetime import date
from pathlib import Path
from threading import RLock
from time import monotonic
from urllib.parse import urlencode
from typing import Literal
import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import ROOT
from .evidence_search import search_gms
from .providers import ProviderError
from config import Settings
from src.data.legal_api import LegalAPI
from src.data.models import Candidate
from src.utils.errors import AuthenticationError, ServiceError

FIX = ROOT / "fixtures"


def fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8-sig"))


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Query(Input):
    query: str = Field(min_length=1, max_length=120)
    kind: Literal["law", "prec", "expc"] = "law"


class Detail(Input):
    api_id: str = Field(pattern=r"^\d{1,12}$")
    kind: Literal["law", "prec", "expc"]
    topic: Literal["return", "sign", "risk", "repair"]


class Calendar(Input):
    year: int = Field(ge=2000, le=2100)


class City(Input):
    name: str = Field(min_length=1, max_length=50)


class Weather(Input):
    latitude: float = Field(ge=33, le=39)
    longitude: float = Field(ge=124, le=132)


class Rag(Input):
    question: str = Field(min_length=1, max_length=700)


class Failure(BaseModel):
    code: str
    message: str


class Result(BaseModel):
    ok: bool
    data: list[dict] = Field(default_factory=list)
    error: Failure | None = None
    source: str
    basis: str
    cached: bool = False


ERRORS = {
    "BAD_INPUT": "입력 형식을 확인해 주세요.",
    "NO_RESULTS": "조건에 맞는 자료가 없습니다.",
    "MISSING_KEY": "LEGAL_API_KEY에 승인된 법제처 OC를 설정하세요.",
    "UPSTREAM": "외부 자료를 확인할 수 없습니다.",
    "NO_INDEX": "2회차 벡터 색인을 연결해 주세요.",
    "NO_FIXTURE": "해당 조건으로 저장한 응답이 없습니다.",
    "NO_ATTRIBUTE": "판단에 필요한 속성이 제공되지 않았습니다.",
    "RAG_UNAVAILABLE": "벡터 색인과 임베딩 설정을 확인해 주세요.",
}


class ToolFailure(Exception):
    pass


class ReplayLegal(LegalAPI):
    def _request(self, endpoint, params, refresh=False):
        kind = params["target"]
        if endpoint == "lawSearch.do" and params["query"] == "주택임대차보호법":
            return fixture(
                {
                    "law": "001_law.json",
                    "prec": "002_prec.json",
                    "expc": "003_expc.json",
                }[kind]
            )
        if endpoint == "lawSearch.do" and kind == "law" and params["query"] == "민법":
            return fixture("015_law.json")
        if params.get("MST") == "284415":
            return fixture("027_law.json")
        if params.get("MST") == "276291":
            return fixture("013_law.json")
        if kind == "prec" and params.get("ID") == "618185":
            return fixture("014_prec.json")
        raise ToolFailure("NO_FIXTURE")


class SourceRow(BaseModel):
    url: str = Field(pattern="^https://")


class CandidateRow(SourceRow):
    id: str
    title: str
    kind: str
    summary: str


class EvidenceRow(SourceRow):
    id: str
    title: str
    kind: str
    text: str
    effective: str


class HolidayRow(SourceRow):
    date: str
    title: str


class CityRow(SourceRow):
    title: str
    latitude: float
    longitude: float


class ForecastRow(SourceRow):
    date: str
    rain_percent: float = Field(ge=0, le=100)


OUTPUT_ROWS = {
    "candidates": CandidateRow,
    "detail": EvidenceRow,
    "rag": EvidenceRow,
    "holidays": HolidayRow,
    "city": CityRow,
    "weather": ForecastRow,
}

DEFS = {
    "candidates": (
        Query,
        "계약에 관련된 현행 법령·판례·해석례 후보가 필요할 때",
        "법제처 국가법령정보센터",
    ),
    "detail": (
        Detail,
        "후보의 판본 식별자로 조문·시행일 또는 사건 본문을 확인할 때",
        "법제처 국가법령정보센터",
    ),
    "holidays": (Calendar, "개인 점검일이 한국 공휴일인지 확인할 때", "Nager.Date"),
    "city": (
        City,
        "현장 점검을 요청한 도시의 좌표가 필요할 때",
        "Open-Meteo Geocoding · GeoNames",
    ),
    "weather": (
        Weather,
        "현장 점검 후보일의 일별 강수확률을 확인할 때",
        "Open-Meteo Forecast",
    ),
    "rag": (
        Rag,
        "계약 특약의 위험이나 법률 근거를 색인에서 찾아야 할 때",
        "2회차 ChromaDB · BGE-M3",
    ),
}


class Tools:
    def __init__(self, mode, runtime, gms_store=None):
        if mode not in {"snapshot", "mixed", "live"}:
            raise ValueError("DATA_MODE는 snapshot, mixed 또는 live로 설정하세요.")
        self.mode = mode
        settings = Settings(
            data_dir=runtime,
            legal_api_key=os.getenv("LEGAL_API_KEY", ""),
            api_timeout=6,
            api_max_retries=0,
        )
        self.legal = (ReplayLegal if mode != "live" else LegalAPI)(settings)
        self.cache = {}
        self.lock = RLock()
        self.rag_store = None
        self.gms_store = gms_store

    def registry(self):
        return [
            {
                "name": k,
                "description": v[1],
                "source": v[2],
                "scope": "local" if k == "rag" else "external",
                "input": v[0].model_json_schema(),
                "output": Result.model_json_schema(),
                "data_item": OUTPUT_ROWS[k].model_json_schema(),
            }
            for k, v in DEFS.items()
        ]

    def invoke(self, name, args):
        schema, _, source = DEFS[name]
        basis = (
            "로컬 색인"
            if name == "rag"
            else "2회차 수집본 2026-10-23"
            if self.mode != "live" and name in {"candidates", "detail"}
            else "공개 API 저장 응답 2026-11-20"
            if self.mode == "snapshot"
            else "실시간 조회"
        )
        try:
            model = schema.model_validate(args)
            key = (name, model.model_dump_json())
            with self.lock:
                entry = self.cache.get(key)
                if entry and entry[0] > monotonic():
                    return entry[1].model_copy(update={"cached": True})
            data = []
            if name == "rag" and self.gms_store is not None:
                try:
                    data = search_gms(model.question, self.gms_store)
                except ProviderError:
                    pass
                if data:
                    source, basis = (
                        "GMS · text-embedding-3-large",
                        "2회차 수집본 2026-10-23",
                    )
            if not data:
                data = getattr(self, name)(model)
            if not data:
                raise ToolFailure("NO_RESULTS")
            try:
                data = [
                    OUTPUT_ROWS[name].model_validate(row).model_dump() for row in data
                ]
            except ValidationError:
                raise ToolFailure("NO_ATTRIBUTE") from None
            result = Result(ok=True, data=data, source=source, basis=basis)
            with self.lock:
                if len(self.cache) >= 256:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = (monotonic() + 1800, result)
            return result
        except ValidationError:
            code = "BAD_INPUT"
        except ToolFailure as exc:
            code = str(exc)
        except AuthenticationError:
            code = (
                "MISSING_KEY" if not self.legal.settings.legal_api_key else "UPSTREAM"
            )
        except (
            requests.RequestException,
            ServiceError,
            ValueError,
            KeyError,
            TypeError,
            OSError,
            ImportError,
        ):
            code = "RAG_UNAVAILABLE" if name == "rag" else "UPSTREAM"
        except Exception:
            code = "RAG_UNAVAILABLE" if name == "rag" else "UPSTREAM"
        return Result(
            ok=False,
            error=Failure(code=code, message=ERRORS[code]),
            source=source,
            basis=basis,
        )

    def candidates(self, p):
        rows, _ = self.legal.search(p.query, p.kind, display=5)
        return [
            {
                "id": r.api_id,
                "kind": r.document_type,
                "title": r.title,
                "url": r.source_url,
                "summary": r.summary,
            }
            for r in rows[:5]
        ]

    def detail(self, p):
        cand = Candidate(f"{p.kind}:{p.api_id}", p.kind, p.api_id, "", "")
        docs = self.legal.fetch(cand)
        terms = {
            "return": ("보증금의 회수", "임대차기간", "계약의 갱신"),
            "sign": ("대항력", "임대인의 정보 제시"),
            "risk": ("강행규정", "보증금의 회수", "대항력"),
            "repair": ("임대인의 의무", "임차인의 상환청구권", "임차인의 통지의무"),
        }[p.topic]
        if p.kind == "law":
            docs = [
                d for d in docs if d.effective_date and any(t in d.title for t in terms)
            ]
            if not docs:
                raise ToolFailure("NO_ATTRIBUTE")
        from src.data.legal_api import public_document_url

        if p.topic == "risk":
            docs.sort(key=lambda d: "강행규정" not in d.title)
        return [
            {
                "id": d.document_id,
                "title": d.title or d.extra.get("case_number", ""),
                "text": d.text[:550],
                "url": d.source_url or public_document_url(p.kind, p.api_id),
                "effective": d.effective_date,
                "kind": p.kind,
            }
            for d in docs[:3]
        ]

    def holidays(self, p):
        url = f"https://date.nager.at/api/v3/PublicHolidays/{p.year}/KR"
        if self.mode == "snapshot":
            if not (FIX / f"holidays-{p.year}.json").is_file():
                raise ToolFailure("NO_FIXTURE")
            data = fixture(f"holidays-{p.year}.json")
        else:
            data = get_json(url)
        if (
            not isinstance(data, list)
            or not data
            or any(
                x.get("countryCode") != "KR"
                or date.fromisoformat(x["date"]).year != p.year
                for x in data
            )
        ):
            raise ToolFailure("NO_ATTRIBUTE")
        return [{"date": x["date"], "title": x["localName"], "url": url} for x in data]

    def city(self, p):
        url = "https://geocoding-api.open-meteo.com/v1/search"
        if self.mode == "snapshot":
            if p.name.lower() not in {"대전", "daejeon"}:
                raise ToolFailure("NO_FIXTURE")
            data = fixture("city.json")
        else:
            data = get_json(
                url, {"name": p.name, "countryCode": "KR", "count": 3, "language": "ko"}
            )
        return [
            {
                "title": x["name"],
                "latitude": x["latitude"],
                "longitude": x["longitude"],
                "url": "https://www.geonames.org/" + str(x["id"]) + "/",
            }
            for x in data.get("results", [])
            if x["country_code"] == "KR"
        ][:1]

    def weather(self, p):
        url = "https://api.open-meteo.com/v1/forecast"
        if self.mode == "snapshot":
            center = fixture("city.json")["results"][0]
            if (
                abs(center["latitude"] - p.latitude) > 0.01
                or abs(center["longitude"] - p.longitude) > 0.01
            ):
                raise ToolFailure("NO_FIXTURE")
            data = fixture("weather.json")
        else:
            data = get_json(
                url,
                {
                    **p.model_dump(),
                    "daily": "precipitation_probability_max",
                    "timezone": "Asia/Seoul",
                    "forecast_days": 7,
                },
            )
        daily = data["daily"]
        return [
            {
                "date": d,
                "rain_percent": v,
                "url": url
                + "?"
                + urlencode(
                    {
                        **p.model_dump(),
                        "daily": "precipitation_probability_max",
                        "timezone": "Asia/Seoul",
                    }
                ),
            }
            for d, v in zip(
                daily["time"], daily["precipitation_probability_max"], strict=True
            )
            if v is not None
        ]

    def rag(self, p):
        from src.rag.retriever import Retriever

        settings = Settings(
            data_dir=Path(os.getenv("RAG_DATA_DIR", ROOT / ".runtime/rag"))
        )
        if not (settings.chroma_dir / "chroma.sqlite3").is_file():
            raise ToolFailure("NO_INDEX")
        from src.vectorstore.chroma_store import ChromaStore
        from src.vectorstore.embeddings import LocalEmbeddings

        with self.lock:
            if self.rag_store is None:
                self.rag_store = ChromaStore(settings, LocalEmbeddings(settings))
            store = self.rag_store
        if not store.collections[settings.meta_collection].count():
            raise ToolFailure("NO_INDEX")
        anchors = store.search(settings.meta_collection, p.question, k=1)
        if not anchors:
            return []
        hits = Retriever(settings, store).retrieve(
            p.question, anchors[0]["id"], top_k=3
        )
        return [
            {
                "id": h["id"],
                "title": h["metadata"]["title"],
                "url": h["metadata"]["source_url"],
                "text": h["text"][:550],
                "effective": h["metadata"].get("effective_date", ""),
                "kind": "law",
            }
            for h in hits
        ]


def get_json(url, params=None):
    r = requests.get(url, params=params, timeout=(3, 6))
    r.raise_for_status()
    return r.json()
