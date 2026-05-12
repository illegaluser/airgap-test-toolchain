"""recording_shared — Recording UI 가 만든 도구지만 Replay UI 도 함께 사용하는 모듈.

`trace_parser` (trace.zip → run_log 변환), `codegen_trace_wrapper`
(`.py` 시나리오를 trace 켜고 실행), `report_export` (self-contained HTML 보고서) 셋이
양쪽 UI 의 공용 의존이라 한 곳으로 모은 패키지.
"""
