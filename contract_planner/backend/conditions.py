import re
from calendar import monthrange
from datetime import date, timedelta

CITIES = {
    "대전": "Daejeon",
    "서울": "Seoul",
    "부산": "Busan",
    "광주": "Gwangju",
    "대구": "Daegu",
    "인천": "Incheon",
    "울산": "Ulsan",
    "세종": "Sejong",
}
DAYS = "월화수목금토일"
DATE = re.compile(
    r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일|(\d{4})-(\d{2})-(\d{2})"
)
TIME = re.compile(
    r"(오전|오후|저녁|밤|아침)?\s*(\d{1,2})\s*(?:시(?:\s*(\d{1,2})\s*분|\s*(반))?|:(\d{2}))\s*(이후|부터|이전|까지|전에|전)?"
)
RELATIVE_DEADLINE = re.compile(
    r"(오늘|내일|모레|(?:이번|다음)\s*주\s*[월화수목금토일]요일|이번\s*달\s*말)\s*까지"
)


def resolve_deadline(phrase, today):
    if not isinstance(phrase, str) or not RELATIVE_DEADLINE.fullmatch(phrase):
        return None
    words = re.sub(r"\s+", "", phrase[:-2])
    if words in {"오늘", "내일", "모레"}:
        return today + timedelta(days={"오늘": 0, "내일": 1, "모레": 2}[words])
    if words == "이번달말":
        return today.replace(day=monthrange(today.year, today.month)[1])
    return today + timedelta(
        days=(
            -today.weekday()
            + DAYS.index(words[-3])
            + (7 if words.startswith("다음") else 0)
        )
    )


def minutes(value):
    h, m = map(int, value.split(":"))
    return h * 60 + m


def clock(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def parse(message, previous, today):
    text = message.strip()
    if re.fullmatch(r"(안녕(?:하세요)?|도움말|사용법|고마워|감사합니다)[!.? ]*", text):
        return "help", previous, ""
    c = dict(previous)
    if (
        "반환" in text
        or "만료" in text
        or "끝나" in text
        or "끝나요" in text
        or "돌려받" in text
    ):
        c["topic"] = "return"
    elif any(x in text for x in ("체결", "계약 전", "계약전", "계약 예정")):
        c["topic"] = "sign"
    is_review = any(x in text for x in ("특약", "위험조항", "위험 조항", "계약 문구"))
    if any(x in text for x in ("현장", "방문", "임장")):
        c["visit"] = True
    if "서류만" in text or "방문 취소" in text:
        c["visit"] = False
    is_fact = any(
        x in text for x in ("판례", "해석례", "법률 근거", "조문", "법적 기한")
    )
    explicit_plan = any(
        x in text for x in ("일정", "계획", "준비", "점검일", "바꿔", "변경", "현장")
    )
    intent = (
        "review" if is_review else "fact" if is_fact and not explicit_plan else "plan"
    )
    if "계약" in text or "전세" in text or "임대차" in text:
        c.setdefault("topic", "sign")
    for word in ("전세", "월세"):
        if word in text:
            c["contract_type"] = word
    if "판례" in text or "해석례" in text:
        c["kind"] = "prec" if "판례" in text else "expc"
    elif is_review or explicit_plan or "조문" in text:
        c["kind"] = "law"
    for ko, en in CITIES.items():
        if ko in text:
            c["city"] = en
            c["city_ko"] = ko
    try:
        for hit in DATE.finditer(text):
            y, m, d, iy, im, iday = hit.groups()
            day = date(int(y or iy or today.year), int(m or im), int(d or iday))
            if not (y or iy) and day < today:
                return intent, c, "지난 날짜입니다. 계약 연도를 포함해 다시 알려주세요."
            part = text[hit.end() : hit.end() + 10]
            before = text[max(0, hit.start() - 7) : hit.start()]
            key = (
                "first"
                if re.match(r"\s*(부터|에\s*(현장|점검|방문))", part)
                or re.search(r"(점검일|시작일)\s*[은는]?\s*$", before)
                else "deadline"
            )
            c[key] = day.isoformat()
        if "내일부터" in text:
            c["first"] = (today + timedelta(days=1)).isoformat()
        elif "오늘부터" in text:
            c["first"] = today.isoformat()
        for hit in TIME.finditer(text):
            period, h, m, half, colon, qualifier = hit.groups()
            hour = int(h)
            minute = int(colon or m or (30 if half else 0))
            if (period and not 1 <= hour <= 12) or hour >= 24 or minute >= 60:
                raise ValueError()
            if period:
                hour = hour % 12 + (12 if period in {"오후", "저녁", "밤"} else 0)
            c["end" if qualifier in {"전", "전에", "이전", "까지"} else "start"] = (
                clock(hour * 60 + minute)
            )
    except ValueError:
        return (
            intent,
            c,
            "날짜나 시간이 달력 범위를 벗어났습니다. 연-월-일과 24시간제로 알려주세요.",
        )
    if "평일" in text:
        c["days"] = list(range(5))
    elif "주말" in text:
        c["days"] = [5, 6]
    elif "매일" in text or "요일 무관" in text:
        c.pop("days", None)
    elif ds := re.findall(r"([월화수목금토일])요일", text):
        c["days"] = [DAYS.index(d) for d in ds]
    if rain := re.search(r"강수(?:확률| 확률)\s*(\d{1,3})\s*%\s*이하", text):
        c["rain"] = int(rain[1])
        if c["rain"] > 100:
            return intent, c, "강수확률은 0~100% 범위로 알려주세요."
    if dur := re.search(r"(\d{1,3})\s*분\s*(?:동안|씩|확보|점검|만 가능)", text):
        c["duration"] = int(dur[1])
        if not 1 <= c["duration"] <= 240:
            return intent, c, "점검 시간은 1~240분 범위로 알려주세요."
    if any(
        x in text
        for x in ("예산", "만원 이하", "공휴일만", "제외하고", "빼고", "야간 불가")
    ):
        return (
            intent,
            c,
            "해당 조건은 현재 계산할 수 없습니다. 요일, 가능 시간, 기한과 방문 도시를 지정해 주세요.",
        )
    if intent == "plan":
        if not c.get("topic"):
            return (
                intent,
                c,
                "계약 체결 전 점검인지, 만료·보증금 반환 준비인지 알려주세요.",
            )
        if not c.get("deadline"):
            return intent, c, "계약일 또는 만료일을 연-월-일로 알려주세요."
        if c.get("visit") and not c.get("city"):
            return (
                intent,
                c,
                "현장 점검 도시를 알려주세요. 지원 도시는 "
                + ", ".join(CITIES)
                + "입니다.",
            )
        if c.get("rain") is not None and not c.get("visit"):
            return (
                intent,
                c,
                "강수확률을 적용할 현장 점검 도시와 방문 여부를 알려주세요.",
            )
    return intent, c, ""


class KeywordIssues:
    def complete(self, system, payload, role="query"):
        clause = payload["contract_clause"]
        keywords = {
            "수선": "임대인의 수선의무",
            "원상복구": "원상회복 의무",
            "보증금": "보증금 반환",
            "갱신": "계약 갱신",
            "대항력": "임차인의 대항력",
        }
        return {"issues": [issue for word, issue in keywords.items() if word in clause]}
