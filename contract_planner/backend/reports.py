"""Evidence-bound clause checks and local, session-scoped report drafts."""

import json
import re
from datetime import date
from functools import lru_cache
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from src.rag.generator import Generator
from src.data.metadata_loader import load_items
from . import ROOT
from .providers import ProviderError
from .store import now

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)
]


class Draft(BaseModel):
    session_id: UUID
    clauses: Text
    ai: bool = False


class Edits(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    requests: list[
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
        ]
    ] = Field(max_length=1200)
    revision: int = Field(ge=1)


@lru_cache(maxsize=1)
def references():
    keys = {"article:001706:062300", "article:001248:000302", "article:001248:001000"}
    items = {}
    for row in load_items(ROOT / "legacy/data/processed/law_items.jsonl"):
        if row.item_id in keys:
            items[row.item_id] = dict(
                title=f"{row.law_name} {row.article_no} ({row.article_title})",
                url=row.source_url,
                text=row.text[:350],
                collected=row.collected_at[:10],
            )
    return items


DATE_FIELD = re.compile(
    r"(잔금일|대출\s*실행(?:\s*예정)?일)\s*[:：은는]?\s*(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?!\d)"
)
DEPOSIT = re.compile(
    r"^(?:계약\s*)?(?:임대차\s*)?보증금\s*[:：]?\s*(\d{1,3}(?:,\d{3}){1,5}|\d{1,16})\s*(억|만)?\s*원\s*[.]?$"
)
SALE_PAYMENT = re.compile(r"^((?:\d+차)?중도금|잔금)[:：]")
PAYMENT_DATE = re.compile(r"(\d{4})[-./년](\d{1,2})[-./월](\d{1,2})(?!\d)")


def finding(level, title, quote, reason, request, source=None):
    return dict(
        level=level,
        title=title,
        quote=quote,
        reason=reason,
        request=request,
        source=source,
    )


def is_sale(text):
    compact = re.sub(r"\s+", "", text)
    return "매매계약서" in compact or ("매도인" in compact and "매수인" in compact)


def analyze(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 160:
        raise HTTPException(
            422,
            "한 번에 160개 문단까지 검토할 수 있습니다. 검토할 부분을 선택해 주세요.",
        )
    sale = is_sale(text)
    result = []
    dated = {"잔금일": [], "대출 실행일": []}
    amounts = []
    sale_dates = []
    sale_amounts = {}
    sources = references()
    for line in lines:
        before = len(result)
        compact = re.sub(r"\s+", "", line)
        if "[판독불가]" in compact:
            result.append(
                finding(
                    "확인 불가",
                    "사진 원본 확인",
                    line,
                    "판독하기 어려운 부분이 있어 이 문단의 조건을 확인할 수 없습니다.",
                    "해당 부분의 선명한 원본을 요청하고 금액·날짜·문구를 대조해 주세요.",
                )
            )
            continue
        payment = SALE_PAYMENT.match(compact) if sale else None
        if sale and (
            price := re.match(
                r"^(매매대금|계약금|(?:\d+차)?중도금|잔금):[₩￦](\d[\d,]{0,19})(?:\||$)",
                compact,
            )
        ):
            sale_amounts.setdefault(price[1], []).append(
                (int(price[2].replace(",", "")), line)
            )
        if payment and (payment_date := PAYMENT_DATE.search(compact)):
            label = payment[1]
            try:
                day = date(*map(int, payment_date.groups()))
                order = (
                    100
                    if label == "잔금"
                    else int(label.split("차")[0])
                    if "차" in label
                    else 1
                )
                sale_dates.append((order, day, line))
            except ValueError:
                result.append(
                    finding(
                        "확인 불가",
                        "매매 지급일",
                        line,
                        "유효한 날짜를 확인할 수 없습니다.",
                        "원본 지급일을 확인해 주세요.",
                    )
                )
        for match in DATE_FIELD.finditer(line):
            label, year, month, day = match.groups()
            label = "잔금일" if label == "잔금일" else "대출 실행일"
            try:
                dated[label].append((date(int(year), int(month), int(day)), line))
            except ValueError:
                result.append(
                    finding(
                        "확인 불가",
                        label,
                        line,
                        "입력한 날짜가 달력에 없습니다.",
                        f"{label}의 정확한 연·월·일을 알려 주세요.",
                    )
                )
        amount = DEPOSIT.fullmatch(line)
        if amount:
            amounts.append(
                (
                    int(amount[1].replace(",", ""))
                    * {None: 1, "만": 10000, "억": 100000000}[amount[2]],
                    line,
                )
            )
        if sale and re.search(
            r"누수|보일러|수선|수리비|보수비|(?:건물|설비|시설)의?하자", compact
        ):
            result.append(
                finding(
                    "주의",
                    "매매 하자·수리 책임",
                    line,
                    "하자나 설비 수리에 관한 문구입니다. 인도 전후의 책임, 기간과 예외를 원문에서 확인해 주세요.",
                    "누수·설비 고장별 책임 주체와 적용 기간, 예외 및 인도 시 점검 방법을 명시해 주세요.",
                )
            )
        elif re.search(r"수선|수리비|보수비", compact):
            blanket = bool(
                re.search(r"전액|모든|일체|전부", compact)
                and "임차인" in compact
                and not re.search(r"않|아니|제외|예외|부담하지|부담없", compact)
            )
            result.append(
                finding(
                    "주의" if blanket else "보통",
                    "수선비 부담 범위",
                    line,
                    "임차인과 포괄적 비용 부담 표현이 함께 있어 원인별 책임과 예외를 확인할 필요가 있습니다."
                    if blanket
                    else "수선 범위와 당사자별 비용 부담 기준을 확인할 항목입니다.",
                    "고장 원인과 수선 항목별로 비용을 부담할 당사자와 예외를 계약서에 명시해 주세요.",
                    sources.get("article:001706:062300"),
                )
            )
        if (
            not sale
            and "보증금" in compact
            and re.search("포기|반환하지않|반환불가", compact)
            and not re.search("포기하지않|포기할수없|포기하지아니", compact)
        ):
            result.append(
                finding(
                    "주의",
                    "보증금 반환 조건",
                    line,
                    "반환을 제한할 수 있는 표현이 포함되어 적용 조건을 확인해야 합니다.",
                    "보증금 반환 조건과 공제 사유를 구체적으로 적고, 포괄적인 포기 문구의 수정을 검토해 주세요.",
                    sources.get("article:001248:000302"),
                )
            )
        if re.search(r"해제|해지", compact) and re.search(
            r"할수없|하지못|불가|금지|제한한다", compact
        ):
            result.append(
                finding(
                    "주의",
                    "계약 종료 조건",
                    line,
                    "계약 종료 권리를 제한하는 표현의 범위와 예외를 확인해야 합니다.",
                    "계약 해제·해지가 가능한 경우와 필요한 절차를 구체적으로 적어 주세요.",
                    None if sale else sources.get("article:001248:001000"),
                )
            )
        if sale and re.search(
            r"말소.*(?:한다|하기로|하여야|해야)|부담(?:등)?을제거.*이전", compact
        ):
            result.append(
                finding(
                    "보통",
                    "권리 말소 약정",
                    line,
                    "계약 문구에서 권리 말소 또는 부담 제거 약정을 확인했습니다.",
                    "말소할 권리와 처리 기한을 명시하고 이행 후 최신 등기사항증명서를 제공해 주세요.",
                )
            )
        elif sale and re.search(r"현(?:재)?(?:시설|건물)상태.*(?:계약|인수)", compact):
            result.append(
                finding(
                    "보통",
                    "현 상태 인수 특약",
                    line,
                    "계약 문구에 시설 상태를 기준으로 체결한다는 특약이 적혀 있습니다.",
                    "인도 전에 확인한 시설 상태와 별도로 합의한 수리 항목을 명시해 주세요.",
                )
            )
        elif sale and re.search(r"공과금.*(?:잔금|인도).*정산", compact):
            result.append(
                finding(
                    "보통",
                    "공과금 정산 약정",
                    line,
                    "계약 문구에 공과금을 정산하는 시점이 적혀 있습니다.",
                    "정산 대상 항목과 최종 검침 내역을 확인해 주세요.",
                )
            )
        elif re.search(r"등기|소유자|근저당|보증보험|HUG", compact, re.I):
            result.append(
                finding(
                    "확인 불가",
                    "증빙 자료 확인",
                    line,
                    "입력 문구만으로 실제 등기 상태나 보증 심사 결과를 대조할 수 없습니다.",
                    "해당 문구의 근거가 되는 최신 등기사항증명서 또는 보증 심사 자료를 제공해 주세요.",
                )
            )
        if (
            before == len(result)
            and not amount
            and not payment
            and not DATE_FIELD.search(line)
        ):
            result.append(
                finding(
                    "확인 불가",
                    "추가 검토가 필요한 문구",
                    line,
                    "지원하는 점검 규칙만으로 이 문구를 판정할 수 없습니다.",
                    "이 조항의 적용 조건과 근거 자료를 확인해 주세요.",
                )
            )
    if amounts:
        conflict = len({x[0] for x in amounts}) > 1
        result.append(
            finding(
                "높음" if conflict else "보통",
                "보증금 기재액 대조",
                "\n".join(x[1] for x in amounts),
                "동일한 보증금 항목에 서로 다른 금액이 기재되어 있습니다."
                if conflict
                else "입력된 보증금 표기를 확인했습니다. 합의 금액과 대조가 필요합니다.",
                "합의한 보증금을 확인하고 계약서의 보증금 표기를 일치시켜 주세요.",
            )
        )
    if any(dated.values()):
        dates = [{x[0] for x in values} for values in dated.values()]
        quotes = "\n".join(
            dict.fromkeys(q for values in dated.values() for _, q in values)
        )
        if any(len(values) > 1 for values in dates):
            level, reason, request = (
                "높음",
                "동일한 일정 항목에 서로 다른 날짜가 적혀 있습니다.",
                "최종 합의한 잔금일과 대출 실행일을 확인하고 표기를 일치시켜 주세요.",
            )
        elif not all(dates):
            level, reason, request = (
                "확인 불가",
                "잔금일과 대출 실행일 중 하나가 없어 선후 관계를 계산할 수 없습니다.",
                "잔금일과 대출 실행 예정일을 모두 알려 주세요.",
            )
        else:
            difference = (next(iter(dates[1])) - next(iter(dates[0]))).days
            level = "높음" if difference > 0 else "낮음"
            reason = (
                f"입력한 대출 실행일이 잔금일보다 {difference}일 늦습니다."
                if difference > 0
                else "입력한 대출 실행일이 잔금일과 같거나 앞섭니다. 날짜 순서만 점검했습니다."
            )
            request = (
                "잔금 지급 전 자금 마련이 가능하도록 두 일정의 조정을 검토해 주세요."
                if difference > 0
                else "실제 실행 예정 시각과 잔금 지급 시각을 확인해 주세요."
            )
        result.append(finding(level, "잔금·대출 일정", quotes, reason, request))
    if (
        "매매대금" in sale_amounts
        and len(sale_amounts) >= 3
        and all(len(rows) == 1 for rows in sale_amounts.values())
    ):
        total = sale_amounts["매매대금"][0][0]
        known = sum(
            rows[0][0] for label, rows in sale_amounts.items() if label != "매매대금"
        )
        if known == total:
            result.append(
                finding(
                    "낮음",
                    "기재된 매매대금 합계",
                    "\n".join(rows[0][1] for rows in sale_amounts.values()),
                    f"읽을 수 있는 지급 금액의 합계 {known:,}원이 기재된 매매대금과 같습니다.",
                    "원본 지급표와 대조해 주세요.",
                )
            )
    if len({order for order, _, _ in sale_dates}) >= 2:
        sale_dates.sort()
        conflict = any(
            before[1] > after[1] or (before[0] == after[0] and before[1] != after[1])
            for before, after in zip(sale_dates, sale_dates[1:])
        )
        result.append(
            finding(
                "주의" if conflict else "낮음",
                "매매대금 지급일 순서",
                "\n".join(row[2] for row in sale_dates),
                "기재된 지급일이 지급 단계 순서와 다르거나 같은 단계에 날짜가 두 가지입니다."
                if conflict
                else "날짜가 적힌 지급 단계의 순서를 대조했습니다.",
                "지급 단계마다 최종 합의한 날짜를 확인해 주세요.",
            )
        )
    return result


def ai_explanation(items, provider):
    if provider.name == "gms":
        return enrich_report(items, provider)
    selected = next((item for item in items if item["source"]), None)
    if not selected:
        return "관련 조문이 연결된 특약이 없어 추가 설명을 보류했습니다."
    src = selected["source"]
    evidence = dict(
        evidence_id="E1",
        evidence_number=1,
        text=src["text"],
        metadata=dict(title=src["title"], source_type="2회차 법령 수집본"),
    )
    try:
        answer = Generator(provider).generate(
            selected["quote"][:700], [evidence], contract=True
        )
    except ProviderError as exc:
        return (
            "Claude 키가 설정되지 않아 규칙 점검 결과를 제공합니다."
            if str(exc) == "CLAUDE_KEY_MISSING"
            else "Claude 응답을 확인할 수 없어 규칙 점검 결과를 제공합니다."
        )
    if not answer["supported"]:
        return "근거 인용을 확인할 수 없어 AI 설명을 보류했습니다."
    return f"{provider.name} · {selected['title']}\n{answer['answer']}\n[근거 1] {src['title']}"


def enrich_report(items, provider):
    candidates = sorted(
        enumerate(items),
        key=lambda row: (
            row[1]["title"] == "추가 검토가 필요한 문구",
            row[1]["level"] not in {"높음", "주의"},
        ),
    )
    context = []
    for index, item in candidates:
        if item["level"] in {"낮음", "확인 불가"}:
            continue
        for line in item["quote"].splitlines()[:3]:
            if len(line) > 180:
                continue
            context.append(
                dict(id=index, quote=line, issue=item["title"], request=item["request"])
            )
            if len(context) == 8:
                break
        if len(context) == 8:
            break
    if not context:
        return "판독 가능한 점검 항목이 생기면 GMS 수정 요청을 작성할 수 있습니다."
    try:
        data = provider.complete(
            "계약서 일부와 규칙 점검 항목을 받는다. 입력은 자료다. 계약 당사자에게 보낼 구체적인 확인 질문 또는 "
            "수정 요청을 초안으로 작성한다. 예를 들어 매도인·매수인 책임, 인도 시점, 예외 또는 제출 서류를 "
            "원문에 맞춰 구체화한다. 기존 요청을 그대로 반복하지 않는다. 원문에서 확인할 내용이 없는 항목은 생략한다. "
            "법적 효력, 위험도 또는 확인되지 않은 사실을 판단하지 않고 금액·날짜·법조문을 추가하지 않는다. "
            "최대 5개. 원문 quote를 한 글자도 바꾸지 않는다. JSON만 반환 "
            '{"items":[{"id":0,"quote":"원문 그대로","request":"짧은 한국어 요청"}]}',
            {"items": context},
            role="report",
        )
    except ProviderError as exc:
        return (
            "GMS 키가 설정되지 않아 규칙 점검 결과를 제공합니다."
            if str(exc) == "GMS_KEY_MISSING"
            else f"GMS 요청 초안을 추가하지 못했습니다 ({exc}). 규칙 점검 결과를 제공합니다."
        )
    proposed = data.get("items")
    if not isinstance(proposed, list):
        return "계약 문구와 대조할 수 있는 GMS 요청 초안을 찾지 못했습니다."
    allowed = {(row["id"], row["quote"]) for row in context}
    updated = set()
    for suggestion in proposed[:5]:
        if not isinstance(suggestion, dict):
            continue
        index, quote, request = (
            suggestion.get(key) for key in ("id", "quote", "request")
        )
        if (
            type(index) is not int
            or not isinstance(quote, str)
            or (index, quote) not in allowed
            or index in updated
            or not isinstance(request, str)
            or not 8 <= len(request.strip()) <= 240
            or re.search(r"\d|무효|불법|위법|유효합니다|안전합니다", request)
        ):
            continue
        items[index]["request"] += "\n" + "GMS 제안 · " + request.strip()
        updated.add(index)
    return (
        f"SSAFY GMS · {data.get('_gms_model', provider.model)}로 대조한 수정·확인 요청 {len(updated)}건을 추가했습니다."
        if updated
        else "계약 문구와 대조할 수 있는 GMS 요청 초안을 찾지 못했습니다."
    )


def report_router(store, provider, check_text):
    router = APIRouter(prefix="/api/reports", tags=["계약 검토 보고서"])
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS contract_reports(
            id TEXT PRIMARY KEY, sid TEXT NOT NULL, title TEXT NOT NULL, original TEXT NOT NULL,
            findings TEXT NOT NULL, explanation TEXT NOT NULL, revision INTEGER NOT NULL,
            updated_at TEXT NOT NULL)""")
        db.execute(
            "CREATE INDEX IF NOT EXISTS reports_session ON contract_reports(sid,updated_at)"
        )

    def decode(row):
        if not row:
            raise HTTPException(404, "이 대화의 보고서를 찾을 수 없습니다.")
        data = dict(row)
        data.pop("sid")
        items = json.loads(data["findings"])
        data["findings"] = [item for item in items if item["level"] != "확인 불가"]
        data["unverified"] = len(items) != len(data["findings"])
        return data

    @router.post("", status_code=201)
    def create(draft: Draft):
        check_text(draft.clauses)
        items = analyze(draft.clauses)
        note = ai_explanation(items, provider) if draft.ai else ""
        rid = str(uuid4())
        with store.connect() as db:
            db.execute(
                "INSERT INTO contract_reports VALUES(?,?,?,?,?,?,?,?)",
                (
                    rid,
                    str(draft.session_id),
                    "부동산 매매 계약 검토 보고서"
                    if is_sale(draft.clauses)
                    else "부동산 계약 검토 보고서",
                    draft.clauses,
                    json.dumps(items, ensure_ascii=False),
                    note,
                    0,
                    now(),
                ),
            )
            return decode(
                db.execute(
                    "SELECT * FROM contract_reports WHERE id=?", (rid,)
                ).fetchone()
            )

    @router.get("")
    def listing(session_id: UUID):
        with store.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,title,revision,updated_at FROM contract_reports WHERE sid=? ORDER BY updated_at DESC,rowid DESC",
                    (str(session_id),),
                )
            ]

    @router.get("/{rid}")
    def read(rid: UUID, session_id: UUID):
        with store.connect() as db:
            return decode(
                db.execute(
                    "SELECT * FROM contract_reports WHERE id=? AND sid=?",
                    (str(rid), str(session_id)),
                ).fetchone()
            )

    @router.put("/{rid}")
    def save(rid: UUID, session_id: UUID, edits: Edits):
        check_text("\n".join([edits.title, *edits.requests]))
        args = (str(rid), str(session_id))
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM contract_reports WHERE id=? AND sid=?", args
            ).fetchone()
            report = decode(row)
            if edits.revision != report["revision"] + 1:
                raise HTTPException(
                    409, "다른 창에서 수정했습니다. 저장된 보고서를 다시 열어 주세요."
                )
            items = json.loads(row["findings"])
            editable = (
                items
                if len(edits.requests) == len(items)
                else [item for item in items if item["level"] != "확인 불가"]
            )
            if len(edits.requests) != len(editable):
                raise HTTPException(422, "항목별 요청 문구를 모두 입력해 주세요.")
            for item, request in zip(editable, edits.requests):
                item["request"] = request
            db.execute(
                "UPDATE contract_reports SET title=?,findings=?,revision=?,updated_at=? WHERE id=? AND sid=?",
                (
                    edits.title,
                    json.dumps(items, ensure_ascii=False),
                    edits.revision,
                    now(),
                    *args,
                ),
            )
            return decode(
                db.execute(
                    "SELECT * FROM contract_reports WHERE id=? AND sid=?", args
                ).fetchone()
            )

    return router
