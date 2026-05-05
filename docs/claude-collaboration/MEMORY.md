# Memory Index

- [Build tooling — no buildx](feedback_no_buildx.md) — User does not use docker buildx; do not mention or recommend it.
- [Naver auth = stepping stone](project_naver_auth_stepping_stone.md) — auth-profile 설계는 네이버 직접 테스트가 아니라 네이버로 로그인되는 외부 서비스 테스트가 목적.
- [Commit message style — plain language + structured](feedback_commit_message_style.md) — 비개발자도 이해 가능한 쉬운 한국어 + 본문은 항목별로 일목요연하게.
- [Document all decisions](feedback_document_decisions.md) — 비-사소한 변경 시 docs/PLAN_*.md 작성. 의사결정 근거(대안/사유/트레이드오프) + 구현범위 + 검증 명시.
- [No speculation — verify first](feedback_no_speculation.md) — 진단/분석은 직접 실행해 검증된 사실만. 추측을 사실처럼 단정 금지.
- [Fix lint warnings](feedback_fix_lint_warnings.md) — IDE 진단을 "기존 거라 무관" 으로 dismiss 금지. 편집한 파일의 모든 경고는 수정 대상.
- [e2e headless default](feedback_e2e_headless_default.md) — e2e 테스트는 headless 가 기본. 임의로 headed 로 바꾸지 말 것.
