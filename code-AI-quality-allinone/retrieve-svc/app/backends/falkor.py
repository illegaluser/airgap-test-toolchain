"""Graph retrieve via FalkorDB (Redis module, Cypher subset).

Phase 0~5 단계에서는 02 의 falkor_sink.py 가 적재한 *호출그래프* 만 활용 (CALLS / HANDLES /
INHERITS_FROM / DEFINED_IN / TESTS). Phase 8 (Joern CPG 정식 도입) 에서 CFG / DDG /
REACHING_DEF / TAINTS 엣지 추가.
"""
from typing import Any, Dict, List, Optional

import redis.asyncio as redis


class FalkorBackend:
    def __init__(self, url: str):
        self.url = url
        self.client = redis.from_url(url, decode_responses=True)

    async def blast_radius(
        self,
        issue_path: str,
        issue_symbol: str,
        n_hop: int = 2,
        top_n: int = 30,
        graph_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """이슈 함수의 N-hop 호출자 + callees + 연관 테스트.

        graph_name 미지정 시 빈 결과 (fail-soft) — Phase 4 에서 knowledge_id ↔ graph 매핑 결정.
        """
        if not graph_name or not issue_path or not issue_symbol:
            return []
        # 작은따옴표 escape — Cypher 문자열 리터럴
        path_lit = issue_path.replace("'", "\\'")
        symbol_lit = issue_symbol.replace("'", "\\'")
        cypher = (
            f"MATCH (f {{path: '{path_lit}', symbol: '{symbol_lit}'}})-"
            f"[*1..{n_hop}]-(rel) "
            "RETURN rel.path AS path, rel.symbol AS symbol, "
            "labels(rel)[0] AS kind, rel.is_test AS is_test "
            f"LIMIT {top_n}"
        )
        try:
            res = await self.client.execute_command("GRAPH.QUERY", graph_name, cypher)
            return self._parse(res, n_hop)
        except Exception:
            return []

    @staticmethod
    def _parse(res, n_hop: int) -> List[Dict[str, Any]]:
        # FalkorDB GRAPH.QUERY 응답: [header, rows, statistics]
        if not isinstance(res, (list, tuple)) or len(res) < 2:
            return []
        rows = res[1] or []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            row = list(row) + [None] * max(0, 4 - len(row))
            path, symbol, kind, is_test = row[0], row[1], row[2], row[3]
            if not path and not symbol:
                continue
            out.append(
                {
                    "id": f"graph::{path}::{symbol}",
                    # graph hit 은 청크 본문이 비어 있음 — Phase 3 에서 dense 인덱스의 동일 청크와
                    # 조인해 본문 보강 필요. Phase 2 단계에서는 빈 content 로 둠.
                    "content": "",
                    "path": str(path or ""),
                    "symbol": str(symbol or ""),
                    "kind": str(kind or "Function"),
                    "is_test": bool(is_test) if is_test is not None else False,
                    "kb_layer": "l2a",
                    "match_reason": f"{n_hop}-hop neighbor of issue function",
                }
            )
        return out

    async def close(self):
        try:
            await self.client.aclose()
        except Exception:
            pass
