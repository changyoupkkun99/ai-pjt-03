import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    model_validator,
    field_validator,
)

from . import ROOT
from .agent import Agent
from .audit import LoggingAgent, diagnose
from .providers import get_provider
from .ocr import router as ocr_router
from .reports import report_router
from .store import Store
from .tools import Tools

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1200)
]
Time = Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]


class Chat(BaseModel):
    session_id: UUID
    message: Text

    @field_validator("message")
    @classmethod
    def exclude_credentials(cls, text):
        configured = (
            os.getenv(name)
            for name in ("ANTHROPIC_API_KEY", "GMS_API_KEY", "LEGAL_API_KEY")
        )
        if any(secret and secret in text for secret in configured) or re.search(
            r"(?:sk-ant-|sk-proj-|Bearer\s+)[A-Za-z0-9_-]{12,}", text, re.I
        ):
            raise ValueError("인증값은 입력에서 제외하세요.")
        return text


class Reply(BaseModel):
    session_id: str
    run_id: str
    status: str
    intent: str
    answer: str
    disclaimer: str
    data_mode: str
    attempts: int
    jobs: list[str]
    events: list[dict]
    conditions: dict
    sources: list[dict]
    errors: list[dict]
    draft: dict
    rejected: list[dict]


class Plan(BaseModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
    ]
    day: date
    start: Time
    end: Time
    notes: str = Field(default="", max_length=2400)

    @model_validator(mode="after")
    def check_interval(self):
        if self.start >= self.end:
            raise ValueError("종료는 시작 이후여야 합니다.")
        return self


class SavedPlan(Plan):
    id: int
    session_id: UUID
    created_at: str


class SaveResult(BaseModel):
    plan: SavedPlan
    conflicts: list[dict]


class LogGroup(BaseModel):
    session_id: UUID
    events: int


def create_app(runtime=None):
    runtime = Path(runtime or os.getenv("PLANNER_RUNTIME", ROOT / ".runtime"))
    store = Store(runtime / "plans.sqlite3")
    provider = get_provider(store)
    tools = Tools(
        os.getenv("DATA_MODE", "snapshot"),
        runtime,
        store if provider.name == "gms" else None,
    )
    agent = Agent(store, tools, provider, int(os.getenv("MAX_REPLANS", "3")))
    # Separate database lets a logging device/write failure leave memory and plans usable.
    try:
        log_store = Store(runtime / "logs.sqlite3")
        agent.bus.subscribers.append(LoggingAgent(log_store, agent.limit).receive)
    except (OSError, sqlite3.Error):
        log_store = None
    app = FastAPI(title="안심계약 AI Agent", version="1.0")
    app.state.agent = agent
    app.state.store = store

    def check_report_text(text):
        try:
            Chat.exclude_credentials(text)
        except ValueError:
            raise HTTPException(422, "인증값을 제외하고 입력하세요.") from None

    app.include_router(ocr_router)
    app.include_router(report_router(store, agent.provider, check_report_text))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc):
        if request.url.path.startswith("/api/reports"):
            return JSONResponse(
                {
                    "detail": "계약 문구, 제목 또는 수정 요청의 입력 길이를 확인해 주세요."
                },
                status_code=422,
            )
        return JSONResponse(
            {
                "detail": "입력값을 확인하세요. 날짜와 시작·종료 시간 형식을 확인해 주세요."
            },
            status_code=422,
        )

    def existing(value):
        if not value:
            raise HTTPException(404, "요청한 데이터를 찾을 수 없습니다.")
        return value

    def logging_db():
        if log_store is None:
            raise HTTPException(503, "실행 기록 저장소를 사용할 수 없습니다.")
        return log_store

    @app.post("/api/chat", response_model=Reply)
    def chat(body: Chat):
        return agent.run(str(body.session_id), body.message)

    @app.get("/api/tools")
    def registry():
        return tools.registry()

    @app.get("/api/usage")
    def usage():
        return store.usage()

    @app.get("/api/status")
    def status():
        return {
            "data_mode": tools.mode,
            "provider": agent.provider.name,
            "model": getattr(agent.provider, "model", "mock"),
            "max_replans": agent.limit,
        }

    @app.get("/api/plans", response_model=list[SavedPlan])
    def plans(session_id: UUID):
        return store.plans(str(session_id))

    @app.post("/api/plans", response_model=SaveResult, status_code=201)
    def save(session_id: UUID, body: Plan):
        return store.save(str(session_id), body)

    @app.get("/api/plans/{pid}", response_model=SavedPlan)
    def read(session_id: UUID, pid: int):
        return existing(store.plan(str(session_id), pid))

    @app.put("/api/plans/{pid}", response_model=SaveResult)
    def update(session_id: UUID, pid: int, body: Plan):
        return existing(store.save(str(session_id), body, pid))

    @app.delete("/api/plans/{pid}", status_code=204)
    def remove(session_id: UUID, pid: int):
        existing(store.delete_plan(str(session_id), pid))

    @app.get("/api/logs", response_model=list[LogGroup])
    def logs():
        return logging_db().logs()

    @app.get("/api/logs/{session_id}/{run_id}/assessment")
    def assessment(session_id: UUID, run_id: UUID):
        return existing(logging_db().assessment(str(session_id), run_id.hex))

    @app.post("/api/logs/{session_id}/{run_id}/diagnosis")
    def diagnosis(session_id: UUID, run_id: UUID):
        sid, run = str(session_id), run_id.hex
        db = logging_db()
        value = existing(db.assessment(sid, run))
        if value["diagnosis"] is None or "error" in value["diagnosis"]:
            note = diagnose(value["assessment"], agent.provider)
            if note:
                if "text" in note:
                    db.save_diagnosis(sid, run, note)
                value["diagnosis"] = note
        return value

    @app.get("/api/logs/{session_id}", response_model=list[dict])
    def detail(session_id: UUID):
        return existing(logging_db().logs(str(session_id)))

    @app.delete("/api/logs/{session_id}", status_code=204)
    def delete_logs(session_id: UUID):
        existing(logging_db().delete_logs(str(session_id)))

    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app
