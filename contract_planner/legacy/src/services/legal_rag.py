from time import perf_counter

from src.data.collector import Collector
from src.data.legal_api import LegalAPI
from src.data.metadata_loader import load_documents, load_items
from src.indexing.indexer import Indexer
from src.rag.generator import Generator
from src.rag.graph import build_graph
from src.rag.llm import ModelClient
from src.rag.retriever import Retriever
from src.services.contract_review import ContractReview
from src.services.item_discovery import ItemDiscovery
from src.utils.errors import ServiceError
from src.vectorstore.chroma_store import ChromaStore
from src.vectorstore.embeddings import LocalEmbeddings


class LegalRAGService:
    def __init__(self, settings, embeddings=None, llm=None):
        self.settings = settings
        self.items = load_items(settings.processed_dir / "law_items.jsonl")
        self.item_by_id = {item.item_id: item for item in self.items}
        self.source_documents = load_documents(settings.processed_dir / "source_documents.jsonl")
        self.llm = llm or ModelClient(settings)
        self.store = ChromaStore(settings, embeddings or LocalEmbeddings(settings))
        self.indexer = Indexer(settings, self.store)
        self.api = LegalAPI(settings)
        self.collector = Collector(settings, self.api, self.llm, self.indexer, self.items, self.source_documents)
        self.retriever = Retriever(settings, self.store)
        self.generator = Generator(self.llm)
        self.graph = build_graph(self.retriever, self.generator)
        self.discovery = ItemDiscovery(settings, self.store, self.llm, self.items)
        self.contract_review = ContractReview(self.llm)

    def ask(self, question, item_id, history=None, contract=False, top_k=None, search_type=None, progress=lambda _: None):
        if not isinstance(question, str) or not question.strip():
            raise ServiceError("질문 또는 계약 문장을 입력하세요.")
        if len(question) > 12000:
            raise ServiceError("질문 또는 계약 문장은 12,000자 이내로 입력하세요.")
        if item_id not in self.item_by_id:
            raise ServiceError("법률 항목을 먼저 선택하세요.")
        if not self.settings.gms_api_key:
            raise ServiceError("AI API Key가 없습니다. .env의 GMS_API_KEY를 설정하세요.")
        item = self.item_by_id[item_id]
        started = perf_counter()
        issues = []
        if contract:
            progress("계약 쟁점 추출 중")
            issues = self.contract_review.extract_issues(question)
        question_with_context = question
        if issues:
            question_with_context = f"계약조항: {question}\n검토 쟁점: {', '.join(issues)}"
        # Only prior user text supplies pronoun context; generated answers never become evidence.
        recent = [m["content"][:1000] for m in (history or [])[-4:] if m.get("role") == "user"]
        retrieval_question = "\n".join(recent[-2:] + [question_with_context])
        trace = self.collector.collect(item, question_with_context, history, progress)
        result = self.graph.invoke({
            "question": retrieval_question, "selected_item_id": item_id,
            "history": history or [], "contract": contract, "top_k": top_k or self.settings.top_k,
            "search_type": search_type or self.settings.search_type, "progress": progress,
        })
        return {key: result[key] for key in ("answer", "cited_source_ids", "sources", "supported", "risk_level", "documents")} | {
            "trace": trace, "issues": issues, "elapsed_seconds": round(perf_counter() - started, 2),
        }
