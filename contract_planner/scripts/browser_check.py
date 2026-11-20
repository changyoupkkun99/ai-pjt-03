"""Real Chromium and loopback FastAPI, isolated state, no paid provider calls."""

import os
import socket
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from threading import Thread
import uvicorn
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import create_app
from src.data.legal_api import KST

out = ROOT / "docs/screens"
out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    os.environ.update(DATA_MODE="snapshot", LLM_PROVIDER="mock", RAG_DATA_DIR=tmp)
    app = create_app(tmp)
    app.state.agent.clock = lambda: datetime(2026, 11, 20, 8, tzinfo=KST)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    channel=os.getenv("BROWSER_CHANNEL", "chrome"), headless=True
                )
                page = browser.new_page(viewport={"width": 1180, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/")
                page.locator("#message").fill(
                    "11월 21일부터 평일 오후 6시 이후 12월 31일 계약 만료 준비"
                )
                page.locator("#send").click()
                expect(page.get_by_text("점검 일정 제안", exact=True)).to_be_visible()
                page.get_by_text("실행 과정 · 재계획 2회").click()
                expect(
                    page.locator(".trace").filter(has_text="재계획 2회").first
                ).to_be_visible()
                page.screenshot(path=str(out / "chat.png"), full_page=True)
                page.get_by_text("이 일정 저장", exact=True).click()
                page.get_by_role("button", name="저장한 일정", exact=True).click()
                expect(page.locator("#planList article")).to_have_count(1)
                page.get_by_role("button", name="수정", exact=True).click()
                page.locator("#edit [name=title]").fill("반환 준비 자료 정리")
                page.get_by_role("button", name="수정 저장").click()
                expect(page.locator("#planList h2")).to_have_text("반환 준비 자료 정리")
                page.screenshot(path=str(out / "plans.png"))
                page.on("dialog", lambda dialog: dialog.accept())
                page.get_by_role("button", name="삭제", exact=True).click()
                expect(page.locator("#planList article")).to_have_count(0)
                page.get_by_role("button", name="실행 기록", exact=True).click()
                page.get_by_role("button", name="기록 열기").click()
                expect(page.locator("#logList .trace").first).to_be_visible()
                page.get_by_role("button", name="계약 점검", exact=True).click()
                page.get_by_role("button", name="특약 검토", exact=True).click()
                page.locator("#send").click()
                expect(page.get_by_text("NO_INDEX", exact=False).first).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                page.get_by_role("button", name="리포트 작성", exact=True).click()
                # Optional local sale contract fixture, never copied into screenshots or the project.
                if photo := os.getenv("PLANNER_TEST_SALE_PHOTO"):
                    page.locator("#photo-engine").select_option("local")
                    page.locator("#report-photo").set_input_files(photo)
                    expect(page.locator("#photo-status")).to_contain_text(
                        "문구를 읽었습니다", timeout=45000
                    )
                    assert "매매" in page.locator(
                        "#report-clauses"
                    ).input_value().replace(" ", "")
                    page.locator("#report-analyze").click()
                    expect(page.locator("#report-title")).to_have_value(
                        "부동산 매매 계약 검토 보고서"
                    )
                    expect(page.locator("#report-findings")).to_contain_text(
                        "매매 하자·수리 책임"
                    )
                    expect(page.locator("#report-findings")).to_contain_text(
                        "권리 말소 약정"
                    )
                    page.locator("#report-picker").select_option("")
                    expect(page.locator("#photo-preview")).to_be_hidden()
                page.locator("#report-example").click()
                page.locator("#report-ai").check()
                page.locator("#report-analyze").click()
                expect(page.locator("#report-findings")).to_contain_text("2일 늦습니다")
                expect(page.locator("#report-unverified")).to_contain_text("검증하지 못했습니다")
                expect(page.locator(".request-item")).to_have_count(2)
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                page.locator("#report-title").fill("수정 요청 검토본")
                page.locator("#request-0").fill(
                    "수선비 부담 범위와 예외를 구분해 주세요."
                )
                page.get_by_role("button", name="보고서 저장").click()
                expect(page.locator("#report-status")).to_contain_text(
                    "보고서를 저장했습니다"
                )
                rid = page.locator("#report-picker").input_value()
                page.goto(f"http://127.0.0.1:{port}/#reports")
                page.locator("#report-picker").select_option(rid)
                expect(page.locator("#request-0")).to_have_value(
                    "수선비 부담 범위와 예외를 구분해 주세요."
                )
                page.set_viewport_size({"width": 1280, "height": 900})
                page.screenshot(path=str(out / "report.png"), full_page=True)
                page.emulate_media(media="print")
                page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
                expect(page.locator("#print-report")).to_contain_text(
                    "수정 요청 검토본"
                )
                expect(page.locator("#print-report")).to_contain_text(
                    "수선비 부담 범위와 예외를 구분해 주세요."
                )
                expect(page.locator(".report-grid")).to_be_hidden()
                page.emulate_media(media="screen")
                page.locator("#report-follow").click()
                for field, value in {
                    "day": "2026-11-26",
                    "start": "18:00",
                    "end": "18:30",
                }.items():
                    page.locator(f"#edit [name={field}]").fill(value)
                page.get_by_role("button", name="일정 저장", exact=True).click()
                expect(page.locator("#planList h2")).to_have_text("계약 검토 후속 점검")
                page.goto(
                    f"http://127.0.0.1:{port}/docs", wait_until="domcontentloaded"
                )
                try:
                    expect(page.locator(".swagger-ui .info")).to_be_visible(
                        timeout=12000
                    )
                    page.screenshot(path=str(out / "api.png"))
                except AssertionError:
                    page.goto(f"http://127.0.0.1:{port}/openapi.json")
                    page.screenshot(path=str(out / "api-schema.png"))
                assert not errors, errors
                browser.close()
                print(
                    "PASS Chromium: planner, logs, NO_INDEX, report analysis, editing, "
                    "persistence, print layout, follow-up scheduling, mobile overflow"
                )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
