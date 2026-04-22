"""
eval_runner.reporting — AI 평가 리포트 생성 패키지

test_runner 의 HTML/번역 로직을 책임 분리한 서브 패키지.

- `translate`: 평가 결과 문자열의 한국어 번역 + HTML-safe 줄바꿈 헬퍼
- `html`: summary.json 상태 사전을 받아 단일 HTML 문자열을 반환

Phase 2 에서 R1~R6 리포트 개편을 이 패키지 안에서 진행한다. test_runner.py 는
SUMMARY_STATE 관리·pytest 파라메트라이즈 실행만 담당하고, 리포트 표현은
전부 본 패키지로 위임.
"""

__all__ = []
