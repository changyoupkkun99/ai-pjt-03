import json
from pathlib import Path

from src.data.models import LegalDocument, LegalItem
from src.utils.errors import ServiceError
from src.utils.hashing import text_hash


# Scope filters are domain data, rather than dependencies of retrieval or generation.
LAW_SCOPES = [
    ("주택임대차보호법", "주택 임대차", None),
    ("주택임대차보호법 시행령", "주택 임대차", None),
    ("상가건물 임대차보호법", "상가 임대차", None),
    ("상가건물 임대차보호법 시행령", "상가 임대차", None),
    ("민법", "계약·권리·임대차", [(98, 111), (186, 191), (211, 245), (303, 319), (356, 372), (536, 654)]),
    ("공인중개사법", "중개·확인설명", None),
    ("부동산 거래신고 등에 관한 법률", "거래 신고", None),
    ("전세사기피해자 지원 및 주거안정에 관한 특별법", "전세사기 피해 지원", None),
    ("집합건물의 소유 및 관리에 관한 법률", "집합건물", None),
    ("부동산등기법", "등기·권리관계", None),
]


def item_from_document(document: LegalDocument, category: str) -> LegalItem:
    return LegalItem(
        item_id=document.document_id.replace("law:", "article:", 1),
        law_id=document.extra["law_id"], master_id=document.extra["master_id"],
        law_name=document.law_name, article_no=document.article_no,
        article_title=document.extra["article_title"], category=category,
        keywords=f"{document.law_name}, {document.extra['article_title']}, {category}",
        effective_date=document.effective_date, source_url=document.source_url, text=document.text,
        collected_at=document.collected_at, document_id=document.document_id,
    )


def save_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".jsonl.tmp")
    temp.write_text("\n".join(json.dumps(x.to_dict(), ensure_ascii=False) for x in records) + "\n", encoding="utf-8")
    temp.replace(path)


def load_items(path: Path) -> list[LegalItem]:
    if not path.exists():
        return []
    try:
        items = [LegalItem(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (ValueError, TypeError, KeyError):
        raise ServiceError("법률 항목 파일을 읽지 못했습니다. 데이터 준비 명령을 다시 실행하세요.") from None
    seen_ids, seen_text, unique = set(), set(), []
    for item in items:
        digest = text_hash(item.index_text())
        if item.item_id not in seen_ids and digest not in seen_text:
            seen_ids.add(item.item_id)
            seen_text.add(digest)
            unique.append(item)
    return unique


def load_documents(path: Path) -> list[LegalDocument]:
    if not path.exists():
        return []
    return [LegalDocument(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
