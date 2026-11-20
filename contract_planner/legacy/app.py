"""SSAFY step7_skeleton.py expanded with real collection, Chroma, and LangGraph."""

import streamlit as st

from config import DISCLAIMER, Settings
from src.services.history import history_for
from src.services.legal_rag import LegalRAGService
from src.utils.errors import ServiceError
from src.utils.hashing import object_hash, text_hash

st.set_page_config(page_title="안심계약 · 법률 RAG", page_icon="⚖️", layout="wide")
st.markdown("""<style>
    .block-container {max-width: 1180px; padding-top: 4rem;}
    [data-testid="stMetric"] {background: white; border: 1px solid #DDE8E5; border-radius: 12px; padding: 12px;}
    [data-testid="stSidebar"] {border-right: 1px solid #DDE8E5;}
    .stChatMessage {border: 1px solid #E5ECEA; border-radius: 12px;}
    h1 {letter-spacing: -0.045em;}
    footer {visibility: hidden;}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def get_service(_settings, signature):
    return LegalRAGService(_settings)


def render_sources(sources):
    if not sources:
        st.caption("답변에 인용된 법률 근거가 없습니다.")
        return
    st.caption("답변이 실제로 인용한 법률 근거")
    types = {"law": "법령·조문", "prec": "판례", "expc": "법령해석례"}
    for source in sources:
        meta, number = source["metadata"], source["evidence_number"]
        with st.expander(f"[근거 {number}] {meta['title']}"):
            st.caption(f"{meta['source_type']} · {types.get(meta.get('document_type'), '문서')} · {meta['source']}")
            if meta.get("effective_date"):
                st.caption(f"시행일: {meta['effective_date']} · 수집일: {meta.get('collected_at', '')[:10]}")
            if meta.get("case_number"):
                st.caption(f"사건·안건번호: {meta['case_number']} · {meta.get('court', '')} · {meta.get('decision_date', '')}")
            st.text(source["text"])
            st.link_button("국가법령정보센터 원문 보기 ↗", meta["source_url"])
            if meta.get("referenced_articles"):
                st.caption("참조조문: " + meta["referenced_articles"])


def navigate(item_id):
    st.session_state.pending_item_id = item_id


def render_chat(service, item, top_k, search_type):
    st.subheader("법률 근거로 질문하기")
    st.caption("전입신고, 보증금 반환, 계약갱신 등 궁금한 내용을 입력하세요.")
    messages = history_for(st.session_state.conversations, item.item_id)
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_sources(message.get("sources", []))
    question = st.chat_input("선택한 법률 항목에 질문해 보세요", disabled=not service.settings.gms_api_key,
                             max_chars=12000, key=f"question_{item.item_id}")
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.status("공식 법률 자료를 확인하고 있습니다", expanded=True) as status:
            try:
                result = service.ask(question, item.item_id, history=messages.copy(), top_k=top_k,
                                     search_type=search_type, progress=lambda label: status.update(label=label))
                status.update(label=f"답변 준비 완료 · {result['elapsed_seconds']:.1f}초", state="complete", expanded=False)
            except ServiceError as exc:
                status.update(label="요청을 완료하지 못했습니다", state="error")
                st.error(str(exc))
                return
            except Exception:
                status.update(label="요청을 완료하지 못했습니다", state="error")
                st.error("처리 중 오류가 발생했습니다. 설정을 확인하고 다시 시도하세요.")
                return
        st.markdown(result["answer"])
        render_sources(result["sources"])
        if result["trace"]["warnings"]:
            st.warning("외부 자료 수집 일부를 완료하지 못해 확보된 공식 자료로 답변했습니다.")
        st.caption(f"검색 근거 {len(result['documents'])}건 · 인용 {len(result['sources'])}건 · "
                   + ("수집 캐시 사용" if result["trace"]["cache_hit"] else "공식 API 자료 확인"))
    messages.extend([{"role": "user", "content": question},
                     {"role": "assistant", "content": result["answer"], "sources": result["sources"]}])


def render_contract(service, item, top_k, search_type):
    st.subheader("계약 위험조항 검토")
    st.caption("계약 문장에서 쟁점을 찾고, 검색된 조문과 판례를 근거로 주의할 점을 설명합니다.")
    with st.form("contract_form"):
        clause = st.text_area("검토할 계약 문장", height=140, max_chars=12000,
                              placeholder="주택 내 모든 하자의 수리비는 임차인이 부담한다.")
        submitted = st.form_submit_button("법률 근거로 검토", type="primary", disabled=not service.settings.gms_api_key)
    if submitted:
        st.session_state.setdefault("contract_results", {}).pop(item.item_id, None)
        with st.status("계약 문장을 검토하고 있습니다", expanded=True) as status:
            try:
                result = service.ask(clause, item.item_id, contract=True, top_k=top_k, search_type=search_type,
                                     progress=lambda label: status.update(label=label))
                result["input_clause"] = clause
                st.session_state.contract_results[item.item_id] = result
                status.update(label="계약조항 검토 완료", state="complete", expanded=False)
            except ServiceError as exc:
                status.update(label="검토를 완료하지 못했습니다", state="error")
                st.error(str(exc))
            except Exception:
                status.update(label="검토를 완료하지 못했습니다", state="error")
                st.error("계약조항 검토 중 오류가 발생했습니다. 잠시 후 다시 시도하세요.")
    result = st.session_state.get("contract_results", {}).get(item.item_id)
    if result:
        st.caption("검토한 계약 문장")
        st.write(result.get("input_clause", ""))
        st.warning(result["risk_level"])
        if result["issues"]:
            st.caption("검토 쟁점: " + " · ".join(result["issues"]))
        st.markdown(result["answer"])
        render_sources(result["sources"])


def render_discovery(service):
    st.subheader("상황에 맞는 법률 찾기")
    st.caption("자연어로 상황을 설명하면 관련 조문을 검색하고 조건에 맞는 항목을 선별합니다.")
    with st.form("discovery"):
        condition = st.text_input("어떤 상황인가요?", placeholder="전세보증금을 돌려받지 못하고 이사해야 합니다", max_chars=2000)
        submitted = st.form_submit_button("관련 법률 찾기", type="primary", disabled=not service.settings.gms_api_key)
    if submitted:
        st.session_state.discovery_results = []
        try:
            with st.spinner("관련 조문을 검색하고 조건을 확인하고 있습니다..."):
                st.session_state.discovery_results = service.discovery.discover(condition) if condition.strip() else []
        except ServiceError as exc:
            st.error(str(exc))
        except Exception:
            st.error("조건 기반 탐색을 완료하지 못했습니다. 잠시 후 다시 시도하세요.")
    results = st.session_state.get("discovery_results", [])
    if submitted and not results:
        st.info("입력 조건을 뒷받침하는 법률 항목을 찾지 못했습니다. 상황을 구체적으로 입력하세요.")
    columns = st.columns(2)
    for i, result in enumerate(results):
        item = result["item"]
        with columns[i % 2], st.container(border=True):
            st.markdown(f"**{item.law_name} {item.article_no}**")
            st.caption(item.article_title)
            st.write(result["reason"])
            st.button("이 조문으로 질문하기 →", key=f"discover_{item.item_id}", on_click=navigate, args=(item.item_id,))


def render_comparison(service, item, top_k):
    st.subheader("검색 방식 비교")
    question = st.text_input("같은 질문으로 유사도 검색과 MMR 비교", key="comparison_query")
    if st.button("검색 결과 비교", disabled=not question.strip()):
        try:
            with st.spinner("색인된 법률 근거를 검색하고 있습니다..."):
                results = {kind: service.retriever.retrieve(question, item.item_id, top_k, kind)
                           for kind in ("similarity", "mmr")}
            for column, kind in zip(st.columns(2), ("similarity", "mmr")):
                with column:
                    st.markdown(f"**{'유사도 검색' if kind == 'similarity' else 'MMR 검색'}**")
                    for hit in results[kind]:
                        st.write(f"{hit['evidence_number']}. {hit['metadata']['title']}")
                        st.caption(f"유사도 {hit['score']:.3f} · {hit['metadata']['source_type']}")
        except ServiceError as exc:
            st.error(str(exc))
        except Exception:
            st.error("검색 비교를 완료하지 못했습니다.")
    evaluation_path = service.settings.data_dir.parent / "docs" / "evaluation_results.md"
    if evaluation_path.exists():
        with st.expander("대표 질문 RAG 전후 평가 결과"):
            st.markdown(evaluation_path.read_text(encoding="utf-8"))


def main():
    st.caption("SSAFY AI CORE · LEGAL KNOWLEDGE SERVICE")
    st.title("안심계약 법률 RAG")
    st.info(DISCLAIMER, icon="⚖️")
    try:
        settings = Settings.from_env()
        signature = object_hash({**{k: str(v) for k, v in vars(settings).items() if "key" not in k},
                                 "gms": text_hash(settings.gms_api_key), "legal": text_hash(settings.legal_api_key)})
        service = get_service(settings, signature)
    except (ServiceError, ValueError) as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("서비스 초기화에 실패했습니다. 의존성 설치 및 데이터 경로를 확인하세요.")
        return
    st.session_state.setdefault("conversations", {})
    if not service.items:
        st.warning("법률 데이터가 없습니다. 먼저 데이터 준비 명령을 실행하세요.")
        st.code("python -m scripts.prepare_data", language="powershell")
        return
    if not settings.gms_api_key:
        st.warning("AI API Key가 없습니다. .env의 GMS_API_KEY를 설정하고 앱을 다시 시작하세요.")
    if not settings.legal_api_key:
        st.warning("법률 API 인증값이 없습니다. .env의 LEGAL_API_KEY를 설정하세요. 확보된 공식 수집본은 열람할 수 있습니다.")
    if settings.legal_api_mode == "example":
        st.caption("공식 예제 API로 개발·검증 중 · 운영 시 승인된 본인 OC 인증값으로 교체해야 합니다.")
    pending = st.session_state.pop("pending_item_id", None)
    if pending in service.item_by_id:
        st.session_state.law_filter = service.item_by_id[pending].law_name
        st.session_state.selected_item_id = pending
        st.session_state.page = "법률 질문"
    with st.sidebar:
        st.markdown("### 나의 계약, 근거부터")
        page = st.radio("기능", ["법률 질문", "계약조항 검토", "조건으로 찾기", "검색 비교·평가"], key="page")
        law_names = list(dict.fromkeys(item.law_name for item in service.items))
        law_filter = st.selectbox("법률", law_names, key="law_filter")
        available = [item.item_id for item in service.items if item.law_name == law_filter]
        if st.session_state.get("selected_item_id") not in available:
            st.session_state.selected_item_id = available[0]
        item_id = st.selectbox("법률 항목", available, key="selected_item_id",
                               format_func=lambda x: f"{service.item_by_id[x].article_no} · {service.item_by_id[x].article_title}")
        search_type = st.selectbox("검색 방식", ["similarity", "mmr"], index=0 if settings.search_type == "similarity" else 1)
        top_k = st.slider("Top-K 근거 수", 2, 12, min(max(settings.top_k, 2), 12))
        counts = service.store.counts()
        st.metric("Meta 색인", counts[settings.meta_collection])
        st.metric("Document 색인", counts[settings.docs_collection])
        st.caption(f"검색·선별: {settings.query_model}\n\n답변: {settings.answer_model}\n\n로컬 임베딩: {settings.embedding_model}")
        if st.button("이 항목의 대화 지우기"):
            history_for(st.session_state.conversations, item_id).clear()
            st.rerun()
        if st.button("AI 연결 확인"):
            try:
                with st.spinner("두 모델의 연결을 확인하고 있습니다..."):
                    service.llm.check_connection()
                st.success("검색 모델과 답변 모델 연결 성공")
            except ServiceError as exc:
                st.error(str(exc))
            except Exception:
                st.error("AI 연결을 확인하지 못했습니다.")
    item = service.item_by_id[item_id]
    with st.container(border=True):
        st.markdown(f"**선택 법률 · {item.law_name} {item.article_no}**")
        st.caption(f"{item.article_title} · 시행일 {item.effective_date}")
        with st.expander("선택 조문 원문"):
            st.text(item.text)
            st.link_button("공식 원문 보기 ↗", item.source_url)
    if counts[settings.meta_collection] == 0:
        st.warning("메타데이터 색인이 없습니다. python -m scripts.prepare_data를 실행하세요.")
        return
    if counts[settings.docs_collection] == 0:
        st.info("외부 문서 색인이 없습니다. 질문 시 공식 원문을 수집·색인하거나 python -m scripts.index_data를 실행하세요.")
    if page == "법률 질문":
        render_chat(service, item, top_k, search_type)
    elif page == "계약조항 검토":
        render_contract(service, item, top_k, search_type)
    elif page == "조건으로 찾기":
        render_discovery(service)
    else:
        render_comparison(service, item, top_k)


if __name__ == "__main__":
    main()
