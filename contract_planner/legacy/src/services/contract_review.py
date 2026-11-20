from src.rag.prompts import ISSUE_SYSTEM
from src.utils.errors import ResponseFormatError


class ContractReview:
    def __init__(self, llm):
        self.llm = llm

    def extract_issues(self, clause):
        result = self.llm.complete(ISSUE_SYSTEM, {"contract_clause": clause})
        issues = result.get("issues")
        if not isinstance(issues, list) or not all(isinstance(x, str) for x in issues):
            raise ResponseFormatError("계약 문장의 쟁점 추출 형식이 올바르지 않습니다.")
        return list(dict.fromkeys(x.strip()[:150] for x in issues if x.strip()))[:4]
