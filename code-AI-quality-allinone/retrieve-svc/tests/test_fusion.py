"""Unit tests for fusion module — RRF behavior + layer weighting + source_layer priority."""
import pytest

from app.config import Settings, parse_layer_weights
from app.fusion import HybridRetriever


@pytest.fixture
def retriever() -> HybridRetriever:
    """Backend 미연결 stub. _rrf_fuse / _apply_layer_weights / _to_record 단위 테스트 전용."""
    s = Settings(
        layer_weights="l2a=0.85,l2c=0.15",
        rrf_k=60,
        top_k_final=5,
        disable_rerank=True,
    )
    r = HybridRetriever.__new__(HybridRetriever)
    r.settings = s
    r.layer_weights = parse_layer_weights(s.layer_weights)
    r.reranker = None
    r.qdrant = None
    r.meili = None
    r.falkor = None
    r.embedder = None
    return r


def test_rrf_basic_ordering(retriever: HybridRetriever):
    """다중 path 에서 등장하는 hit 가 더 높은 fused score 를 받는다."""
    results = {
        "dense": [
            {"id": "a", "content": "hit-a", "path": "p", "symbol": "a"},
            {"id": "b", "content": "hit-b", "path": "p", "symbol": "b"},
        ],
        "sparse": [
            {"id": "b", "content": "hit-b", "path": "p", "symbol": "b"},
            {"id": "c", "content": "hit-c", "path": "p", "symbol": "c"},
        ],
        "graph": [
            {"id": "a", "content": "hit-a", "path": "p", "symbol": "a"},
        ],
    }
    fused = retriever._rrf_fuse(results, k=60)
    ids = [h["id"] for h in fused]
    # 'a': dense rank0 + graph rank0 → 1/61 + 1/61 ≈ 0.0328
    # 'b': dense rank1 + sparse rank0 → 1/62 + 1/61 ≈ 0.0325
    # 'c': sparse rank1 → 1/62 ≈ 0.0161
    assert ids == ["a", "b", "c"]


def test_rrf_empty_input(retriever: HybridRetriever):
    assert retriever._rrf_fuse({}, k=60) == []
    assert retriever._rrf_fuse({"dense": [], "sparse": []}, k=60) == []


def test_source_layer_priority_graph_wins(retriever: HybridRetriever):
    """동일 hit 이 dense + graph 양쪽 hit 시 source_layer = 'graph' (구조 신호 우선)."""
    results = {
        "dense": [{"id": "x", "content": "c", "path": "p", "symbol": "x"}],
        "graph": [{"id": "x", "content": "c", "path": "p", "symbol": "x"}],
    }
    fused = retriever._rrf_fuse(results, k=60)
    assert fused[0]["source_layer"] == "graph"


def test_source_layer_priority_sparse_over_dense(retriever: HybridRetriever):
    results = {
        "dense": [{"id": "y", "content": "c", "path": "p", "symbol": "y"}],
        "sparse": [{"id": "y", "content": "c", "path": "p", "symbol": "y"}],
    }
    fused = retriever._rrf_fuse(results, k=60)
    assert fused[0]["source_layer"] == "sparse"


def test_graph_content_backfill_from_dense(retriever: HybridRetriever):
    """Graph hit 은 content='', dense hit 은 content 있음 → fused entry 의 content 가 dense 로 보강."""
    results = {
        "graph": [{"id": "z", "content": "", "path": "p", "symbol": "z"}],
        "dense": [{"id": "z", "content": "func body", "path": "p", "symbol": "z"}],
    }
    fused = retriever._rrf_fuse(results, k=60)
    assert fused[0]["content"] == "func body"
    # source_layer 우선순위는 여전히 graph
    assert fused[0]["source_layer"] == "graph"


def test_layer_weights_applied(retriever: HybridRetriever):
    """kb_layer 별 가중치 곱 + 정렬."""
    hits = [
        {"id": "a", "kb_layer": "l2a", "_fused_score": 1.0},
        {"id": "c", "kb_layer": "l2c", "_fused_score": 1.0},
    ]
    out = retriever._apply_layer_weights(hits)
    by_id = {h["id"]: h for h in out}
    assert by_id["a"]["_fused_score"] == pytest.approx(0.85)
    assert by_id["c"]["_fused_score"] == pytest.approx(0.15)
    assert out[0]["id"] == "a"  # 가중치 적용 후 정렬


def test_layer_weights_default_l2a(retriever: HybridRetriever):
    """kb_layer 누락 시 'l2a' 기본 가정 (현행 단일 dataset 호환)."""
    hits = [{"id": "x", "_fused_score": 2.0}]
    out = retriever._apply_layer_weights(hits)
    assert out[0]["_fused_score"] == pytest.approx(1.7)  # 2.0 * 0.85


def test_to_record_metadata_never_null(retriever: HybridRetriever):
    """Dify 응답 schema 제약: metadata 는 항상 dict."""
    hit = {"path": "x.py", "symbol": "f", "content": "def f(): pass", "kb_layer": "l2a"}
    rec = retriever._to_record(hit, score=0.5)
    assert isinstance(rec.metadata, dict)
    assert rec.metadata["path"] == "x.py"
    assert rec.metadata["symbol"] == "f"
    assert rec.title == "x.py::f"


def test_parse_layer_weights_robust():
    assert parse_layer_weights("l2a=0.85,l2c=0.15") == {"l2a": 0.85, "l2c": 0.15}
    assert parse_layer_weights("") == {}
    assert parse_layer_weights("l2a=1.0") == {"l2a": 1.0}
    # 비정상 입력은 skip
    assert parse_layer_weights("l2a=,l2c=0.15,malformed") == {"l2c": 0.15}
