"""sentence-transformers CrossEncoder in-process rerank.

bge-reranker-v2-m3 (568M) — 폐쇄망 build 시 이미지 안 /opt/rerank-models/bge-reranker-v2-m3 에
사전 적재. HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 (supervisord env) 로 metadata 조회 차단.
"""
import asyncio
from typing import List, Optional


class CrossEncoderReranker:
    def __init__(self, model_path: str, max_length: int = 512):
        self.model_path = model_path
        self.max_length = max_length
        self._model = None  # lazy load via async startup

    async def load(self) -> None:
        """sentence-transformers 모델 로드는 동기·blocking. thread pool 으로 옮긴다."""
        loop = asyncio.get_running_loop()
        self._model = await loop.run_in_executor(None, self._load_sync)

    def _load_sync(self):
        # local import — startup 외 경로에서 sentence-transformers 누락 시 전체 import 차단 방지.
        from sentence_transformers import CrossEncoder

        return CrossEncoder(self.model_path, max_length=self.max_length)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def score(self, query: str, candidates: List[str]) -> List[float]:
        if not candidates:
            return []
        if self._model is None:
            return [0.0] * len(candidates)
        loop = asyncio.get_running_loop()
        pairs = [(query, c) for c in candidates]
        scores = await loop.run_in_executor(None, lambda: self._model.predict(pairs))
        return [float(s) for s in scores]
