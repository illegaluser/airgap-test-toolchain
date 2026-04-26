"""Dify External Knowledge API schema (POST /retrieval).

Dify 1.13.3 spec — 응답 records[].metadata 필드는 dict 필수 (null 금지).
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RetrievalSetting(BaseModel):
    top_k: int = 5
    score_threshold: float = 0.0


class MetadataConditionItem(BaseModel):
    name: List[str] = Field(default_factory=list)
    comparison_operator: str = "contains"
    value: Optional[str] = None


class MetadataCondition(BaseModel):
    logical_operator: str = "and"
    conditions: List[MetadataConditionItem] = Field(default_factory=list)


class RetrievalRequest(BaseModel):
    knowledge_id: str
    query: str
    retrieval_setting: RetrievalSetting = Field(default_factory=RetrievalSetting)
    metadata_condition: Optional[MetadataCondition] = None
    # 04 가 자체 메타 (issue_path / issue_symbol / severity / rule_id) 를 추가 주입하는 슬롯.
    # Dify 표준 외 확장 — Dify Workflow 의 metadata 변수 매핑으로 전달.
    metadata: Optional[Dict[str, Any]] = None


class Record(BaseModel):
    content: str
    score: float
    title: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    records: List[Record] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    rerank_loaded: bool = False
