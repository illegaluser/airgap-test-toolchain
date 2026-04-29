"""Recording service (host agent, Phase R-MVP).

A FastAPI daemon that runs on the host (mac/wsl). It runs playwright codegen as a
subprocess to record user actions and converts the recording into 14-DSL JSON by
delegating to the container CLI.

Entry point:
    uvicorn recording_service.server:app --host 0.0.0.0 --port 18092

Design: docs/PLAN_GROUNDING_RECORDING_AGENT.md §"Phase R" / §"T0.3"
"""

__version__ = "0.2.0-rplus"
