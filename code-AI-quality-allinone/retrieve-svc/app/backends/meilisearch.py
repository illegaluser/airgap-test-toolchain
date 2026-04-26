"""Sparse (BM25 + lang-aware tokenizing) retrieve via Meilisearch v1.42.

02 (사전학습) 의 meili_sink.py 가 적재한 인덱스를 읽는다. index_name 은 dataset 명과 동일
(예: code-kb-realworld). knowledge_id ↔ index 매핑은 retrieve.py 에서 결정.
"""
from typing import Any, Dict, List, Optional

import httpx


class MeilisearchBackend:
    def __init__(self, url: str, master_key: str = ""):
        self.url = url.rstrip("/")
        headers = {"Authorization": f"Bearer {master_key}"} if master_key else {}
        self.client = httpx.AsyncClient(base_url=self.url, timeout=10.0, headers=headers)

    async def search(
        self,
        query: str,
        top_n: int = 50,
        index: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not index or not query.strip():
            return []
        try:
            r = await self.client.post(
                f"/indexes/{index}/search",
                json={"q": query, "limit": top_n},
            )
            r.raise_for_status()
            return [self._to_hit(h) for h in r.json().get("hits", [])]
        except httpx.HTTPError:
            return []

    @staticmethod
    def _to_hit(h: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(h.get("id", "")),
            "content": h.get("content", ""),
            "path": h.get("path", ""),
            "symbol": h.get("symbol", ""),
            "kb_layer": h.get("kb_layer", "l2a"),
            "kind": h.get("kind", ""),
            "lang": h.get("lang", ""),
            "endpoint": h.get("endpoint", ""),
        }

    async def close(self):
        await self.client.aclose()
