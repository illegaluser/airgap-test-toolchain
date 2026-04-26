"""Sprint 3 통합 테스트용 DSL 시나리오 빌더.

각 테스트가 동일한 navigate-step boilerplate 를 반복 작성하지 않도록 가장
자주 쓰는 step 형태를 한 줄로 만들 수 있게 한다.
"""

from __future__ import annotations


def navigate(url: str, *, step: int = 1, description: str = "대상 페이지 로드") -> dict:
    return {
        "step": step,
        "action": "navigate",
        "target": "",
        "value": url,
        "description": description,
    }


def click(target: str, *, step: int, description: str = "", **extra) -> dict:
    return {"step": step, "action": "click", "target": target, "value": "",
            "description": description, **extra}


def fill(target: str, value: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "fill", "target": target, "value": value,
            "description": description}


def press(target: str, value: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "press", "target": target, "value": value,
            "description": description}


def select(target: str, value: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "select", "target": target, "value": value,
            "description": description}


def check(target: str, value: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "check", "target": target, "value": value,
            "description": description}


def hover(target: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "hover", "target": target, "value": "",
            "description": description}


def verify(target: str, *, step: int, condition: str = "visible", value: str = "",
           description: str = "") -> dict:
    return {"step": step, "action": "verify", "target": target, "value": value,
            "condition": condition, "description": description}


def upload(target: str, value: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "upload", "target": target, "value": value,
            "description": description}


def drag(source: str, dest: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "drag", "target": source, "value": dest,
            "description": description}


def scroll(target: str, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "scroll", "target": target, "value": "into_view",
            "description": description}


def mock_status(pattern: str, status: int, *, step: int, times: int = 1,
                description: str = "") -> dict:
    return {"step": step, "action": "mock_status", "target": pattern,
            "value": str(status), "times": times, "description": description}


def mock_data(pattern: str, body: str | dict | list, *, step: int, times: int = 1,
              description: str = "") -> dict:
    return {"step": step, "action": "mock_data", "target": pattern, "value": body,
            "times": times, "description": description}


def wait(ms: int, *, step: int, description: str = "") -> dict:
    return {"step": step, "action": "wait", "target": "", "value": str(ms),
            "description": description}
