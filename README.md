# 안심계약 AI Agent

부동산 계약 사진과 문구를 점검하고, 법률 근거를 찾아 검토 보고서와 후속 일정을 만드는 SSAFY AI 코어 프로젝트입니다.

- **계약 점검**은 LangGraph로 요청을 해석하고 법령·공휴일·날씨를 대조해 점검 일정을 제안합니다.
- **리포트 작성**은 계약서 사진 판독, 원문 대조, 항목별 점검과 수정 요청 작성, 저장·인쇄를 지원합니다.
- **실행 기록**에서 판단 근거와 도구 실행 경로를 확인할 수 있습니다.

설치, 실행, GMS 설정은 [프로젝트 README](contract_planner/README.md)를 참고하세요. [검증 결과](contract_planner/docs/verification.md)와 [GMS 모델 및 사용량](contract_planner/docs/providers.md)도 확인할 수 있습니다.
