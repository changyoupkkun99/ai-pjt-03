from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class RAGState(TypedDict, total=False):
    question: str
    selected_item_id: str
    documents: list
    answer: str
    cited_source_ids: list[str]
    sources: list
    supported: bool
    risk_level: str
    history: list
    contract: bool
    top_k: int
    search_type: str
    progress: object


def build_graph(retriever, generator):
    def retrieve(state):
        state.get("progress", lambda _: None)("문서 검색 중")
        return {"documents": retriever.retrieve(state["question"], state["selected_item_id"],
                                                 state.get("top_k"), state.get("search_type"))}

    def generate(state):
        state.get("progress", lambda _: None)("답변 생성 중")
        return generator.generate(state["question"], state["documents"], state.get("history"), state.get("contract", False))

    graph = StateGraph(RAGState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
