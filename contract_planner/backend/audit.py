"""Deterministic assessment of one run. Diagnosis is an explicit, separate operation."""

from collections import Counter
from .providers import ProviderError


def assess(events, limit):
    starts = [e for e in events if e["event"] == "stage.start"]
    complete = any(
        e["event"] == "stage.end" and e["stage"] == "respond" for e in events
    )
    allowed = set()
    warnings = []
    seen = Counter()
    replans = 0
    for event in events:
        kind = event["event"]
        if kind == "plan.selected":
            allowed = set(event["tools"])
        elif kind == "replan":
            replans += 1
        elif kind == "tool.request":
            key = (event["tool"], event["input_id"])
            seen[key] += 1
            if seen[key] == 2:
                warnings.append(
                    {"code": "REPEAT_TOOL", "seq": event["seq"], "tool": key[0]}
                )
            if key[0] not in allowed:
                warnings.append(
                    {"code": "UNPLANNED_TOOL", "seq": event["seq"], "tool": key[0]}
                )
    if replans and replans >= max(1, limit):
        warnings.append({"code": "REPLAN_LIMIT", "count": replans})
    status = (
        "warning" if warnings else "normal" if starts and complete else "insufficient"
    )
    return {
        "status": status,
        "warnings": warnings,
        "replans": replans,
        "complete": complete,
    }


def diagnose(assessment, provider):
    if not assessment["warnings"]:
        return None
    try:
        data = provider.complete(
            "실행 경고를 읽고 담당 영역과 수정 방법을 세 문장 이내로 설명하세요. JSON 객체의 diagnosis 문자열만 반환하세요.",
            {"warnings": assessment["warnings"][:8]},
            role="diagnosis",
        )
        note = data.get("diagnosis")
        if not isinstance(note, str) or not note.strip():
            raise ProviderError("FORMAT_ERROR")
        result = {"provider": provider.name, "text": note.strip()[:600]}
        if provider.name == "gms":
            result["model"] = data["_gms_model"]
        return result
    except ProviderError as exc:
        return {"provider": provider.name, "error": str(exc)}


class LoggingAgent:
    def __init__(self, store, limit):
        self.store = store
        self.limit = limit

    def receive(self, event):
        self.store.record(event)
        if event.get("stage") == "respond" and event["event"] == "stage.end":
            sid, run = event["session_id"], event["run_id"]
            events = [e for e in self.store.logs(sid) if e["run_id"] == run]
            self.store.save_assessment(sid, run, assess(events, self.limit))
