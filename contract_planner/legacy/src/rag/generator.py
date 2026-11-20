import re

from src.rag.prompts import ANSWER_SYSTEM, CONTRACT_SYSTEM

UNKNOWN = "제공된 법률 자료에서는 확인되지 않습니다."
CITATION = re.compile(r"\[근거\s*(\d+)\]")


def validate_answer(payload, evidence, contract=False):
    answer = payload.get("answer", "")
    if not isinstance(answer, str):
        answer = ""
    numbers = [int(n) for n in CITATION.findall(answer)]
    actual = {e["evidence_number"]: e for e in evidence}
    valid = bool(answer.strip()) and payload.get("supported") is True and bool(numbers)
    valid = valid and all(n in actual for n in numbers)
    risk = payload.get("risk_level") if contract else "해당 없음"
    if contract:
        # An unrecognized/missing risk or an explicit deferral must never silently
        # turn an asserted 'safe' answer into a supported legal review.
        valid = valid and risk == "주의 필요"
    # The server derives citations from the actual answer, never trusting a model-supplied list.
    if not valid:
        return {"answer": UNKNOWN, "cited_source_ids": [], "sources": [], "supported": False,
                "risk_level": "판단 보류" if contract else "해당 없음"}
    cited = list(dict.fromkeys(numbers))
    return {"answer": answer.strip(), "cited_source_ids": [actual[n]["evidence_id"] for n in cited],
            "sources": [actual[n] for n in cited], "supported": True, "risk_level": risk}


class Generator:
    def __init__(self, llm):
        self.llm = llm

    def generate(self, question, evidence, history=None, contract=False):
        if not evidence:
            return validate_answer({}, [], contract)
        context = [{"evidence_id": e["evidence_id"], "number": e["evidence_number"],
                    "title": e["metadata"]["title"], "source_type": e["metadata"]["source_type"],
                    "document_type": e["metadata"].get("document_type", "law"),
                    "effective_date": e["metadata"].get("effective_date", ""),
                    "body": e["text"]} for e in evidence]
        recent = [m["content"][:1500] for m in (history or [])[-6:] if m.get("role") == "user"]
        payload = self.llm.complete(CONTRACT_SYSTEM if contract else ANSWER_SYSTEM,
                                    {"question": question, "recent_user_questions": recent, "evidence": context}, role="answer")
        result = validate_answer(payload, evidence, contract)
        if getattr(self.llm, "name", "") == "gms" and "_gms_model" in payload:
            result["model"] = payload["_gms_model"]
        return result
