"""Dense retrieve via Qdrant.

Dify Dataset 가 Qdrant collection 을 자동 생성·관리한다. knowledge_id ↔ collection 매핑은
Phase 4 (Dify Workflow datasource 교체) 에서 metadata.collection 또는 별도 매핑 테이블로 결정.
Phase 2 단위 테스트 단계에서는 collection 미지정 시 빈 결과 반환 (fail-soft).
"""
from typing import Any, Dict, List, Optional

import httpx


class QdrantBackend:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.url, timeout=10.0)

    async def search(
        self,
        qvec: List[float],
        top_n: int = 50,
        collection: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not collection or not qvec:
            return []
        try:
            r = await self.client.post(
                f"/collections/{collection}/points/search",
                json={"vector": qvec, "limit": top_n, "with_payload": True},
            )
            r.raise_for_status()
            data = r.json()
            return [self._to_hit(p) for p in data.get("result", [])]
        except httpx.HTTPError:
            return []

    @staticmethod
    def _to_hit(point: Dict[str, Any]) -> Dict[str, Any]:
        payload = point.get("payload") or {}
        return {
            "id": str(point.get("id", "")),
            "content": payload.get("content", "") or payload.get("page_content", ""),
            "path": payload.get("path", ""),
            "symbol": payload.get("symbol", ""),
            "score": float(point.get("score", 0.0)),
            "kb_layer": payload.get("kb_layer", "l2a"),
            "kind": payload.get("kind", ""),
            "lang": payload.get("lang", ""),
        }

    async def close(self):
        await self.client.aclose()
