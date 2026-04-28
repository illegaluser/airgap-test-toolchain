"""Recording 서비스 (호스트 에이전트, Phase R-MVP).

호스트(mac/wsl) 에서 동작하는 FastAPI 데몬. playwright codegen 을 subprocess
로 실행해 사용자 행동을 녹화하고, 컨테이너 CLI 위임으로 14-DSL JSON 으로
변환한다.

진입점:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

설계: PLAN_GROUNDING_RECORDING_AGENT.md §"Phase R" / §"T0.3"
"""

__version__ = "0.1.0-tr4"
