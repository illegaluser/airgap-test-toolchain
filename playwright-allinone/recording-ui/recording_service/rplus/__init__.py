"""Phase R-Plus 실험 모듈 — replay / enrich (역추정) / compare (Doc↔Recording).

R-MVP 가 충족된 상태에서, 평가 증거(2-person rubric, 5-sample 점수, 90% replay
DoD) 가 모이기 전까지 메인 UI 와 분리해 운용한다. 활성화 조건은 환경변수
``RPLUS_ENABLED=1``. 이 값이 없으면 `recording_service.server` 가 router 자체를
include 하지 않으므로 모든 ``/experimental/*`` 엔드포인트가 404 가 된다.
"""
