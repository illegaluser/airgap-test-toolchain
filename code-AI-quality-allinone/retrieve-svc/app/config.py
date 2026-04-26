"""환경변수 파싱. supervisord.conf 의 environment= 블록이 컨테이너 안에서 주입한다.

ENV 매핑은 PLAN §6.8.3 [program:retrieve-svc] 섹션과 1:1 일치해야 한다.
"""
from typing import Dict
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 백엔드 endpoint (양 머신 동일 — 모두 컨테이너 내부 127.0.0.1)
    qdrant_url: str = "http://127.0.0.1:6333"
    meili_url: str = "http://127.0.0.1:7700"
    meili_key: str = ""
    falkor_url: str = "redis://127.0.0.1:6380"
    ollama_url: str = "http://host.docker.internal:11434"

    # 모델 — 양 머신 동일 (PLAN §6.4)
    ollama_embed_model: str = "qwen3-embedding:0.6b"
    rerank_model_path: str = "/opt/rerank-models/bge-reranker-v2-m3"

    # Hybrid retrieve 파라미터
    layer_weights: str = "l2a=0.85,l2c=0.15"
    rrf_k: int = 60
    top_n_per_path: int = 50
    top_k_final: int = 5

    # 폴백 토글 (PLAN §6.10 폴백·축소 시나리오)
    disable_graph: bool = False
    disable_sparse: bool = False
    disable_rerank: bool = False

    # 폐쇄망 강제 (HF metadata 조회 차단 — sentence-transformers 첫 로드 시)
    hf_hub_offline: str = "1"
    transformers_offline: str = "1"

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")


def parse_layer_weights(s: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return out


settings = Settings()
