"""Official DRF API adapter, based on live JSON probes and the official guides.

Observed roots: LawSearch / 법령 / PrecSearch / PrecService / Expc / ExpcService.
No credential or example OC value is embedded in this module.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import math
import re
from threading import Lock
import time
from urllib.parse import quote

import requests

from config import Settings
from src.data.models import Candidate, LegalDocument
from src.utils.cache import DiskCache
from src.utils.errors import AuthenticationError, ResponseFormatError, ServiceError
from src.utils.hashing import public_url, text_hash

KST = timezone(timedelta(hours=9))
SEARCH_ROOTS = {"law": ("LawSearch", "law"), "prec": ("PrecSearch", "prec"), "expc": ("Expc", "expc")}


def retry_after_seconds(value):
    """Read both RFC Retry-After forms without reducing the provider's waiting period."""
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 0
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(str(value))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0, math.ceil(when.timestamp() - time.time()))
        except (TypeError, ValueError, OverflowError):
            return 0


def as_list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def clean_text(value) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def article_text(article: dict) -> str:
    """Preserve all nested 항/호/목 in source order (not just 조문내용)."""
    parts = []

    def visit(node):
        if isinstance(node, list):
            for element in node:
                visit(element)
        elif isinstance(node, dict):
            for name in ("조문내용", "항내용", "호내용", "목내용"):
                if node.get(name):
                    parts.extend(clean_text(x) for x in as_list(node[name]))
            for name in ("항", "호", "목"):
                visit(node.get(name))

    visit(article)
    return "\n".join(dict.fromkeys(parts))


def article_identity(article):
    number, branch = int(article["조문번호"]), int(article.get("조문가지번호") or 0)
    return f"{number:04d}{branch:02d}", f"제{number}조" + (f"의{branch}" if branch else "")


def public_document_url(document_type, api_id, law_name="", article_no=""):
    if document_type == "law":
        path = f"https://www.law.go.kr/법령/{quote(law_name, safe='')}"
        return path + (f"/{quote(article_no, safe='')}" if article_no else "")
    page, parameter = ("precInfoP.do", "precSeq") if document_type == "prec" else ("expcInfoP.do", "expcSeq")
    return f"https://www.law.go.kr/LSW/{page}?{parameter}={quote(str(api_id))}"


def sanitize_response(value):
    if isinstance(value, dict):
        return {k: sanitize_response(v) for k, v in value.items() if k.lower() not in {"oc", "apikey"}}
    if isinstance(value, list):
        return [sanitize_response(v) for v in value]
    if isinstance(value, str) and re.search(r"[?&]OC=", value, re.I):
        return public_url(value)
    return value


class LegalAPI:
    def __init__(self, settings: Settings, session=None):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "AnsimContract-LegalRAG/1.0 (SSAFY educational project)"})
        self.cache = DiskCache(settings.data_dir / "cache" / "legal", settings.api_cache_ttl_hours)
        self._lock = Lock()
        self._last_call = 0.0
        self.network_calls = 0

    def _request(self, endpoint, params, refresh=False):
        if not self.settings.legal_api_key:
            raise AuthenticationError("법률 API 인증값이 없습니다. .env의 LEGAL_API_KEY에 승인된 OC 인증값을 설정하세요.")
        cache_key = {"base_url": self.settings.legal_api_base_url.rstrip("/"),
                     "endpoint": endpoint, "params": params, "scope": text_hash(self.settings.legal_api_key)}
        if not refresh:
            cached = self.cache.get("response", cache_key)
            if cached is not None:
                return cached
        request_params = {**params, "OC": self.settings.legal_api_key, "type": "JSON"}
        for attempt in range(self.settings.api_max_retries + 1):
            try:
                with self._lock:
                    time.sleep(max(0, self.settings.api_min_interval - (time.monotonic() - self._last_call)))
                    self._last_call = time.monotonic()
                    self.network_calls += 1
                    response = self.session.get(
                        self.settings.legal_api_base_url.rstrip("/") + "/" + endpoint,
                        params=request_params, timeout=self.settings.api_timeout,
                    )
                if response.status_code in {401, 403}:
                    raise AuthenticationError("법률 API 인증에 실패했습니다. OC 승인 상태와 사용 권한을 확인하세요.")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.settings.api_max_retries:
                        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
                        if retry_after > 30:
                            raise ServiceError("법률 API의 대기 제한이 길어 이번 요청을 중단했습니다. 안내된 대기 후 다시 시도하세요.")
                        time.sleep(max(2 ** attempt, retry_after))
                        continue
                    raise ServiceError("법률 API가 혼잡하거나 호출 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
                if not response.ok:
                    raise ServiceError("법률 API 요청에 실패했습니다. 접속 주소와 요청 설정을 확인하세요.")
                try:
                    payload = response.json()
                except ValueError:
                    raise ResponseFormatError("법률 API가 JSON 문서를 반환하지 않았습니다. OC 승인 상태를 확인하세요.") from None
                if not isinstance(payload, dict):
                    raise ResponseFormatError("법률 API 응답 형식이 변경되었습니다.")
                for root in payload.values():
                    if isinstance(root, dict) and root.get("resultCode", "00") not in {"00", "0", 0}:
                        raise ServiceError("법률 API에서 정상 문서를 반환하지 않았습니다. 인증 및 요청 설정을 확인하세요.")
                payload = sanitize_response(payload)
                self.cache.set("response", cache_key, payload)
                return payload
            except (requests.Timeout, requests.ConnectionError):
                if attempt < self.settings.api_max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise ServiceError("법률 API에 연결하지 못했습니다. 네트워크 상태를 확인하세요.") from None
            except requests.RequestException:
                raise ServiceError("법률 API 요청을 완료하지 못했습니다.") from None

    def search(self, query: str, document_type="law", page=1, display=10, search=1, refresh=False):
        if document_type not in SEARCH_ROOTS:
            raise ValueError("지원하지 않는 법률 문서 유형입니다.")
        params = {"target": document_type, "query": query, "page": page, "display": min(display, 100), "search": search}
        if document_type == "prec":
            # The official guide states that NTS case bodies are HTML only.
            params["datSrcNm"] = "대법원"
        payload = self._request("lawSearch.do", params, refresh=refresh)
        root_name, records_name = SEARCH_ROOTS[document_type]
        root = payload.get(root_name)
        if not isinstance(root, dict):
            raise ResponseFormatError("법률 API 목록 응답 형식이 예상과 다릅니다.")
        results = []
        for row in as_list(root.get(records_name)):
            if document_type == "law":
                if row.get("현행연혁코드") not in {None, "", "현행"}:
                    continue
                if str(row.get("시행일자", "")) > datetime.now(KST).strftime("%Y%m%d"):
                    continue
                identity, title = str(row["법령일련번호"]), row["법령명한글"]
                doc_id, law_name = f"law:{row['법령ID']}", title
                summary = f"시행일 {row.get('시행일자', '')} / {row.get('소관부처명', '')}"
            elif document_type == "prec":
                identity, title = str(row["판례일련번호"]), row["사건명"]
                doc_id, law_name = f"prec:{identity}", ""
                summary = f"{row.get('법원명', '')} {row.get('사건번호', '')} {row.get('선고일자', '')}"
            else:
                identity, title = str(row["법령해석례일련번호"]), row["안건명"]
                doc_id, law_name = f"expc:{identity}", ""
                summary = f"{row.get('안건번호', '')} {row.get('회신일자', '')} {row.get('회신기관명', '')}"
            results.append(Candidate(doc_id, document_type, identity, title,
                                     public_document_url(document_type, identity, law_name), law_name, summary, query))
        return results, int(root.get("totalCnt") or 0)

    def fetch(self, candidate: Candidate, refresh=False) -> list[LegalDocument]:
        kind = candidate.document_type
        payload = self._request("lawService.do", {
            "target": kind, "MST" if kind == "law" else "ID": candidate.api_id,
        }, refresh=refresh)
        now = datetime.now(KST).isoformat(timespec="seconds")
        if kind == "law":
            root = payload.get("법령")
            if not isinstance(root, dict) or "기본정보" not in root:
                raise ResponseFormatError("법령 본문이 제공되지 않았습니다.")
            basic = root["기본정보"]
            law_name, law_id = basic["법령명_한글"], str(basic["법령ID"])
            documents = []
            for article in as_list(root.get("조문", {}).get("조문단위")):
                if article.get("조문여부") != "조문" or "삭제" in str(article.get("조문제목", "")):
                    continue
                effective = str(article.get("조문시행일자") or basic.get("시행일자") or "")
                if effective and effective > datetime.now(KST).strftime("%Y%m%d"):
                    continue
                code, number = article_identity(article)
                text = article_text(article)
                if not text or re.match(r"제\d+조(?:의\d+)?\s*삭제", text) or re.fullmatch(r"제\d+조(?:의\d+)?", text):
                    continue
                documents.append(LegalDocument(
                    document_id=f"law:{law_id}:{code}", document_type="law",
                    title=f"{law_name} {number} ({article.get('조문제목', '')})", text=text,
                    source_url=public_document_url("law", candidate.api_id, law_name, number),
                    law_name=law_name, article_no=number, effective_date=effective, collected_at=now,
                    extra={"law_id": law_id, "master_id": candidate.api_id,
                           "article_title": article.get("조문제목", ""), "article_code": code},
                ))
            return documents
        expected = "PrecService" if kind == "prec" else "ExpcService"
        root = payload.get(expected)
        if not isinstance(root, dict):
            raise ResponseFormatError("해당 판례 또는 해석례의 JSON 본문이 제공되지 않습니다.")
        fields = ("판시사항", "판결요지", "참조조문", "참조판례", "판례내용") if kind == "prec" else ("질의요지", "회답", "이유")
        text = "\n\n".join(f"{name}\n{clean_text(root[name])}" for name in fields if root.get(name))
        if not text:
            raise ResponseFormatError("해당 문서에 색인할 본문이 없습니다.")
        extra = {"case_number": str(root.get("사건번호", root.get("안건번호", ""))),
                 "decision_date": str(root.get("선고일자", root.get("해석일자", ""))),
                 "court": str(root.get("법원명", root.get("해석기관명", ""))),
                 "referenced_articles": clean_text(root.get("참조조문", "")),
                 "referenced_cases": clean_text(root.get("참조판례", ""))}
        # Keep the original structured fields for law <-> case linkage and future adapters.
        extra.update({name: clean_text(root[name]) for name in fields if root.get(name)})
        return [LegalDocument(candidate.document_id, kind, candidate.title, text,
                              candidate.source_url, collected_at=now, extra=extra)]
