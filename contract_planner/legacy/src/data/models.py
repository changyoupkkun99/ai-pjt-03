from dataclasses import asdict, dataclass, field


@dataclass
class LegalItem:
    item_id: str
    law_id: str
    master_id: str
    law_name: str
    article_no: str
    article_title: str
    category: str
    keywords: str
    effective_date: str
    source_url: str
    text: str
    collected_at: str
    document_id: str
    source: str = "법제처 국가법령정보센터"

    def index_text(self) -> str:
        return (
            f"법률명: {self.law_name}\n조문: {self.article_no}\n조문 제목: {self.article_title}\n"
            f"분류: {self.category}\n핵심 키워드: {self.keywords}\n조문 내용:\n{self.text}"
        )

    def to_dict(self):
        return asdict(self)


@dataclass
class Candidate:
    document_id: str
    document_type: str
    api_id: str
    title: str
    source_url: str
    law_name: str = ""
    summary: str = ""
    query: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class LegalDocument:
    document_id: str
    document_type: str
    title: str
    text: str
    source_url: str
    law_name: str = ""
    article_no: str = ""
    effective_date: str = ""
    collected_at: str = ""
    source: str = "법제처 국가법령정보센터"
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
