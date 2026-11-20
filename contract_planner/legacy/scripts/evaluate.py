"""Reproducible >=5-question LLM-only vs RAG comparison; no fabricated scores."""

import argparse
import csv
import json
from pathlib import Path
from time import perf_counter

from config import ROOT, Settings
from src.rag.prompts import JUDGE_SYSTEM
from src.services.legal_rag import LegalRAGService
from src.utils.errors import ServiceError

CASES = [
    ("주택임대차보호법", "제3조", "전입신고를 하지 않으면 대항력이 없나요?", ["대항력", "주민등록", "인도"]),
    ("주택임대차보호법", "제3조의2", "확정일자만 받으면 무조건 보증금을 먼저 받을 수 있나요?", ["확정일자", "대항요건", "후순위"]),
    ("주택임대차보호법", "제3조의3", "임대인이 보증금을 반환하지 않는데 이사를 가야 합니다. 어떤 제도가 있나요?", ["임차권등기명령", "보증금"]),
    ("주택임대차보호법", "제3조의3", "임차권등기명령을 신청만 하고 이사해도 대항력이 유지되나요?", ["임차권등기", "대항력", "등기"]),
    ("민법", "제623조", "주택 내 모든 하자의 수리비를 임차인이 부담한다는 특약은 주의해야 하나요?", ["수선", "임대인", "사용"]),
    ("주택임대차보호법", "제3조", "내일 서울의 정확한 아파트 매매가격은 얼마인가요?", []),
]


def reciprocal_rank(evidence, expected_terms):
    if not expected_terms:
        return None
    for rank, hit in enumerate(evidence, 1):
        if all(term in hit["text"] for term in expected_terms):
            return round(1 / rank, 4)
    return 0.0


def evaluate(settings, output: Path, judge=True):
    service = LegalRAGService(settings)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, (law_name, article_no, question, terms) in enumerate(CASES, 1):
        item = next(i for i in service.items if i.law_name == law_name and i.article_no == article_no)
        print(f"평가 {number}/{len(CASES)}: {law_name} {article_no}", flush=True)
        started = perf_counter()
        baseline = service.llm.complete(
            "대한민국 부동산 법률 질문에 한국어로 답하라. 확실하지 않으면 모른다고 하라. 전문가 자문을 대신하지 않는다.",
            {"question": question}, role="answer", json_output=False,
        )
        baseline_seconds = round(perf_counter() - started, 2)
        rag = service.ask(question, item.item_id, progress=lambda step: print("  " + step, flush=True))
        evidence = [{"title": e["metadata"]["title"], "text": e["text"], "url": e["metadata"]["source_url"]}
                    for e in rag["documents"]]
        judges = {}
        if judge:
            for kind, answer in (("llm_only", baseline), ("rag", rag["answer"])):
                judges[kind] = service.llm.complete(JUDGE_SYSTEM, {
                    "QUESTION": question, "ANSWER": answer, "EVIDENCE": evidence,
                }, role="answer")
        rows.append({"question": question, "item_id": item.item_id, "llm_only_answer": baseline,
                     "rag_answer": rag["answer"], "source_count": len(rag["sources"]),
                     "cited_source_ids": rag["cited_source_ids"], "source_urls": [e["metadata"]["source_url"] for e in rag["sources"]],
                     "retrieval_rr": reciprocal_rank(rag["documents"], terms),
                     "llm_only_seconds": baseline_seconds, "rag_seconds": rag["elapsed_seconds"],
                     "llm_only_judge": judges.get("llm_only"), "rag_judge": judges.get("rag"),
                     "evidence": evidence, "trace": rag["trace"]})
        # Persist every completed case so a later API failure cannot erase evidence of prior work.
        (output / "evaluation_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        write_reports(rows, output, settings)
    return rows


def write_reports(rows, output, settings):
    columns = [k for k in rows[0] if k not in {"evidence", "trace"}]
    with (output / "evaluation_results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], (list, dict)) else row[k] for k in columns})
    lines = ["# RAG 전후 품질 비교", "", f"답변 모델: `{settings.answer_model}` · 검색·선별: `{settings.query_model}`", "",
             "실제 GMS 응답을 기록했다. Judge는 제공된 법률 Evidence만 사용한 자동 평가이며 전문가의 정답 판정은 아니다.",
             "사실 정확성·주장과 근거 일치·불확실성 표현을 각각 0~2점으로 평가했다. RAGAS 수치로 표기하지 않는다.", "",
             "| 질문 | RAG 인용 수 | 검색 RR | LLM-only / RAG 시간(초) |", "|---|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['question']} | {row['source_count']} | {row['retrieval_rr']} | {row['llm_only_seconds']} / {row['rag_seconds']} |")
    for i, row in enumerate(rows, 1):
        lines += ["", f"## {i}. {row['question']}", "", "### LLM Only", "", row["llm_only_answer"], "", "### RAG", "", row["rag_answer"], "", "### 사용한 출처", ""]
        lines.extend(f"- {url}" for url in row["source_urls"])
        if not row["source_urls"]:
            lines.append("인용 근거 없음. 자료에서 확인되지 않는다고 응답했는지 확인한다.")
        for key in ("llm_only_judge", "rag_judge"):
            if row[key] is not None:
                lines += ["", f"### {key}", "", "```json", json.dumps(row[key], ensure_ascii=False, indent=2), "```"]
    (output / "evaluation_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs")
    parser.add_argument("--no-judge", action="store_true")
    args = parser.parse_args()
    try:
        evaluate(Settings.from_env(), args.output, judge=not args.no_judge)
    except ServiceError as exc:
        print(str(exc))
        raise SystemExit(1) from None
