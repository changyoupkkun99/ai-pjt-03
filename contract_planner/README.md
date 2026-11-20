# 안심계약 AI Agent

임대차계약 체결 전 점검과 보증금 반환 준비를 위한 한국어 일정 플래너입니다. 사용자가 입력한 계약일, 가능 요일·시간과 조회한 조문 시행일·공휴일을 비교합니다. 현장 점검이면 도시 좌표로 날씨를 조회하고, 조건에서 벗어난 날짜를 제외해 다음 날짜를 검토합니다. 제안한 일정은 사용자가 저장한 뒤 수정하거나 삭제할 수 있습니다.

## 실행

저장소 **루트**에서 실행합니다. 이 작업 PC에는 프로젝트 가상환경이 준비되어 있습니다.

```powershell
.\.venv\Scripts\python -m uvicorn backend.main:create_app --factory --app-dir contract_planner --host 127.0.0.1 --port 8000
```

[서비스 화면](http://127.0.0.1:8000/) · [FastAPI 요청·응답 명세](http://127.0.0.1:8000/docs)

새 환경에서는 먼저 Python 3.12로 아래 명령을 실행합니다. 프로젝트 기본 기능은 API 키 없이 동작합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r contract_planner/requirements.txt
```

## 첫 요청

> 전세계약이 12월 31일에 끝나는데 보증금 반환을 준비해야 해. 평일에는 오후 6시 이후만 시간이 있어. 무엇을 확인하고 언제 준비해야 하는지 계획을 세워줘.

**계약 점검**에서 요청을 보내고 근거 자료와 실행 과정을 펼치세요. **이 일정 저장**을 누르면 **저장한 일정**에서 날짜·시간·점검 내용을 관리할 수 있습니다. “오후 7시 이후로 바꿔줘”라고 하면 같은 대화의 만료일과 요일 조건을 이어받습니다. **새 대화**는 별도 세션을 만듭니다.

예시로 “대전 현장 점검, 2026-11-26 계약 만료 준비. 강수확률 10% 이하”, “특약에 임차인이 모든 수선비를 부담한다고 적혀 있어. 위험과 근거를 확인해줘”, “보증금 반환 판례를 찾아줘”를 사용할 수 있습니다. 첫 예시의 날짜와 예보는 2026년 11월 검증 기준입니다. 현재 날짜가 달라졌을 때의 재현에는 고정 시계 검증 스크립트를 사용하세요.

## 데이터와 모델 설정

### 계약 검토 리포트

**계약서 사진 넣기**에서 JPG, PNG 또는 WebP 한 장(최대 12MB)을 선택하면 SSAFY GMS로 판독합니다. 기본 순서는 GPT-6 Astra → GPT-6 Sol → Claude Fable 5.1 → Claude Opus 5.5입니다. 세로형 저해상도 사진은 확대 영역을 함께 보내 지급표의 행별 금액과 날짜를 읽습니다. 사진을 로컬에서 읽으려면 **이 PC · Windows OCR**을 선택하세요. 분석 전 금액·날짜·특약을 원본과 대조할 수 있습니다.

상단 **리포트 작성**에서 계약 문구를 한 줄씩 붙여넣거나 **예시 넣기**를 선택하세요. **분석하고 초안 만들기**를 누르면 원문과 초안을 이 PC의 현재 세션에 저장합니다. 왼쪽에는 점검 단계·원문·이유·관련 조문이, 오른쪽에는 편집 가능한 항목별 확인·수정·자료 요청이 나타납니다. **보고서 저장** 후 **내 보고서**에서 다시 열 수 있습니다. **인쇄 / PDF**는 현재 편집 내용을 인쇄 미리보기로 보내고, **후속 점검 일정 계획하기**는 요청 사항을 일정 메모로 채웁니다. [리포트 화면](docs/screens/report.png)

보증금 표기와 대출 실행일을 대조하고, 매매에서는 읽힌 지급액의 합계와 지급일 순서, 문서에 적힌 말소·인수·정산 약정을 확인합니다. 문구가 흐리거나 외부 자료가 필요한 항목은 보고서에서 안내 한 줄로 표시합니다.

**AI 수정·확인 요청 추가**를 선택하면 GMS가 확인된 계약 문구를 바탕으로 요청을 구체화합니다. GPT-6가 작성한 요청은 원문과 대조해 보고서에 추가합니다. 키가 없거나 모델 호출이 실패하면 규칙 점검 보고서를 생성합니다.

### 환경변수

`contract_planner/.env.example`을 같은 폴더의 `.env`로 복사해 필요한 항목만 지정합니다. OS 환경변수가 우선합니다.

| 설정 | 동작 |
|---|---|
| `DATA_MODE=snapshot` (기본) | 2회차 법제처 원본 JSON, 2026년 한국 공휴일 및 대전 11월 20~26일 예보 응답을 재생합니다. 수집 범위 밖에서는 자료 부족을 표시합니다. |
| `DATA_MODE=mixed` | 2회차 법률 수집본과 실시간 공휴일·도시·날씨 API를 결합합니다. 법제처 서버 등록 전에도 현장 점검을 실행할 수 있습니다. |
| `DATA_MODE=live` | 법률 자료도 실시간 조회합니다. `LEGAL_API_KEY`에 서비스 서버 IP·도메인이 승인된 법제처 OC가 필요합니다. |
| `LLM_PROVIDER=auto` (기본) | `GMS_API_KEY`가 있으면 GMS를 사용하고, 없으면 Anthropic 직접 API 설정을 읽습니다. |
| `GMS_TEXT_MODEL`, `GMS_VISION_MODEL` | 텍스트와 사진 판독의 첫 모델을 설정합니다. 기본 모델은 `gpt-6-astra`입니다. |
| `LLM_PROVIDER=mock` | 같은 인터페이스로 모의 답변을 생성하고 모의 검토임을 표시합니다. |
| `CLAUDE_MODEL` | Anthropic 직접 API에서 쓸 모델입니다. 기본값은 `claude-fable-5-1`입니다. |
| `MAX_REPLANS` | 기본 3회, 지원 범위 0~7회. 대안 탐색을 끝내면 제외 사유와 조정할 조건을 안내합니다. |
| `PLANNER_RUNTIME` | 기본 `contract_planner/.runtime`. 일정·대화 메모리와 로그는 각각 별도 SQLite 파일에 저장합니다. |
| `RAG_DATA_DIR` | 하위에 `chroma/chroma.sqlite3`가 있는 2회차 호환 데이터 디렉터리. 기본은 `.runtime/rag`입니다. |

계약 기한, 가능 시간, 최대 강수확률은 사용자 조건입니다. 달력에서 연도를 생략하면 올해를 적용하고 이미 지난 월·일이면 연도를 되묻습니다. 점검 시간의 기본 제안은 09시부터 30분이며, 당일에는 현재 시각 이후만 제안합니다. 공휴일을 제외하고, 마감일과 동일한 날짜는 허용합니다. 약정상 정확한 종료 시각, 법정 통지 기한, 기관 운영시간, 계약의 효력이나 보증금 회수 가능성은 이 계산으로 확정할 수 없습니다.

## 벡터 검색

이 PC에는 주택임대차보호법 6개 조문과 8개 본문 청크를 대상으로 실제 BGE-M3 색인을 생성했습니다. ZIP에는 벡터 파일 대신 2회차 원문 수집본이 포함되어 있어 같은 검색 모듈로 색인을 재생성했습니다. 새 환경에서 샘플을 구축하려면 다음을 실행합니다. 모델을 처음 받을 때 인터넷 연결과 약 2.3GB의 모델 저장 공간이 필요합니다.

```powershell
.\.venv\Scripts\python -m pip install -r contract_planner/requirements-rag.txt
.\.venv\Scripts\python contract_planner/scripts/index_rag.py --sample
.\.venv\Scripts\python contract_planner/scripts/verify_integrations.py rag
```

전체 조문 697건을 색인하려면 `--sample`을 생략하세요. 원본 색인을 이미 가지고 있으면 `RAG_DATA_DIR`를 지정할 수 있습니다. 검색은 2회차 BGE-M3, Chroma 두 컬렉션, Retriever의 정렬·중복 제거, Generator의 인용 검증을 사용합니다. 색인이 없으면 `NO_INDEX`와 법령 API로 확보한 자료를 함께 표시합니다.

## 검증 재현

```powershell
.\.venv\Scripts\python -m pip install -r contract_planner/requirements-test.txt
Push-Location contract_planner
..\.venv\Scripts\python -m pytest -q tests
Pop-Location
.\.venv\Scripts\python contract_planner/scripts/report.py
.\.venv\Scripts\python contract_planner/scripts/browser_check.py
.\.venv\Scripts\python contract_planner/scripts/verify_integrations.py mixed
.\.venv\Scripts\python contract_planner/scripts/verify_integrations.py providers
```

브라우저 검증은 설치된 Chrome을 사용합니다. Edge의 경우 `BROWSER_CHANNEL=msedge`로 설정할 수 있습니다. 자동 테스트는 임시 DB를 사용합니다. 마지막 두 검증은 외부 서비스 상태에 따라 결과가 달라집니다. 저장 응답을 새로 수집하는 스크립트는 해당 fixture와 프로브 기록을 갱신합니다.

2회차 회귀 테스트에는 별도 의존성이 필요합니다. 아래 명령은 보존한 원본 테스트를 실행합니다.

```powershell
.\.venv\Scripts\python -m pip install -r contract_planner/legacy/requirements.txt
Push-Location contract_planner/legacy
..\..\.venv\Scripts\python -m pytest tests
Pop-Location
```

## 산출물

- [요구사항 충족 매트릭스와 검증 결과](docs/verification.md)
- [LangGraph 구조도, Tool 정의, API 경로 및 이용 조건](docs/design.md)
- [Claude 모델·GMS 호환성 및 토큰 보고서](docs/providers.md)
- [8개 대표 요청의 전체 응답·실행 로그·처리 시간](docs/scenarios.json)
- [실제 일정 API를 사용한 재계획](docs/integration-mixed.json) · [실제 BGE-M3 검색](docs/integration-rag.json)
- [대화와 실행 기록 화면](docs/screens/chat.png) · [계획 수정 결과](docs/screens/plans.png) · [FastAPI 명세 화면](docs/screens/api.png)

교육용 단일 사용자 로컬 서비스입니다. 같은 브라우저에서 세션을 나눌 수 있고 로그 화면은 이 서버에 저장된 세션을 조회합니다.
