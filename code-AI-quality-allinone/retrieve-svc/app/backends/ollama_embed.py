"""쿼리 임베딩 — host Ollama 의 qwen3-embedding:0.6b 호출.

컨테이너 → host: host.docker.internal:11434 (양 머신 동일 — WSL2 compose 가
extra_hosts:host-gateway 매핑).
"""
from typing import List

import httpx


class OllamaEmbedBackend:
    def __init__(self, url: str, model: str):
        self.url = url.rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(base_url=self.url, timeout=30.0)

    async def embed(self, text: str) -> List[float]:
        if not text.strip():
            return []
        try:
            r = await self.client.post(
                "/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            r.raise_for_status()
            data = r.json()
            return list(data.get("embedding", []))
        except httpx.HTTPError:
            return []

    async def close(self):
        await self.client.aclose()
