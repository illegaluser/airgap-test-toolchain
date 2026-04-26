"""Unit tests for Dify External KB schema — metadata 필드 dict 강제 + 기본값."""
import json

from app.schema import HealthResponse, Record, RetrievalRequest, RetrievalResponse


def test_record_metadata_default_empty_dict():
    """Dify 1.13.3 spec: metadata 는 dict 필수 (null 금지)."""
    rec = Record(content="x", score=0.5)
    payload = rec.model_dump()
    assert payload["metadata"] == {}
    # JSON 직렬화 후에도 dict
    assert json.loads(rec.model_dump_json())["metadata"] == {}


def test_record_metadata_preserved():
    rec = Record(content="x", score=0.7, metadata={"path": "a.py", "symbol": "f"})
    assert rec.metadata == {"path": "a.py", "symbol": "f"}


def test_response_empty_records_default():
    """빈 결과는 records=[] 로 응답 (오류 아님 — Dify 가 정상 처리)."""
    resp = RetrievalResponse()
    assert resp.model_dump() == {"records": []}


def test_request_minimal_defaults():
    req = RetrievalRequest(knowledge_id="kb1", query="q")
    assert req.retrieval_setting.top_k == 5
    assert req.retrieval_setting.score_threshold == 0.0
    assert req.metadata_condition is None
    assert req.metadata is None


def test_request_with_issue_metadata():
    """04 가 retrieval_setting 외에 issue_path/issue_symbol 을 metadata 로 추가 주입."""
    req = RetrievalRequest(
        knowledge_id="code-kb-realworld",
        query="user authentication flow",
        retrieval_setting={"top_k": 10, "score_threshold": 0.25},
        metadata={"issue_path": "src/auth.py", "issue_symbol": "login", "rule_id": "S1234"},
    )
    assert req.retrieval_setting.top_k == 10
    assert req.metadata["issue_path"] == "src/auth.py"
    assert req.metadata["issue_symbol"] == "login"


def test_health_default():
    h = HealthResponse()
    assert h.status == "ok"
    assert h.rerank_loaded is False
