"""Hybrid retrieve coordinator — RRF (Reciprocal Rank Fusion) + layer weighting + rerank.

흐름 (PLAN §6.7):
  1. ollama embed (쿼리 → vector)
  2. dense (Qdrant) ∥ sparse (Meilisearch) ∥ graph (FalkorDB) 병렬 retrieve
  3. RRF fusion → 후보 ~100 개로 합침
  4. layer weighting (L2a 0.85 / L2c 0.15)
  5. CrossEncoder rerank (bge-reranker-v2-m3) → top-K 압축
  6. Dify External KB 응답 schema 로 직렬화
"""
import asyncio
from typing import Any, Dict, List, Optional, Tuple

from app.backends.falkor import FalkorBackend
from app.backends.meilisearch import MeilisearchBackend
from app.backends.ollama_embed import OllamaEmbedBackend
from app.backends.qdrant import QdrantBackend
from app.backends.rerank import CrossEncoderReranker
from app.config import Settings, parse_layer_weights
from app.schema import Record, RetrievalRequest, RetrievalResponse


# RRF fusion 시 source layer 우선순위 — graph (구조 신호) > sparse (정확 매칭) > dense (의미)
_LAYER_PRIORITY = ("graph", "sparse", "dense")


class HybridRetriever:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.layer_weights = parse_layer_weights(settings.layer_weights)
        self.qdrant = QdrantBackend(settings.qdrant_url)
        self.meili = MeilisearchBackend(settings.meili_url, settings.meili_key)
        self.falkor = FalkorBackend(settings.falkor_url)
        self.embedder = OllamaEmbedBackend(settings.ollama_url, settings.ollama_embed_model)
        self.reranker: Optional[CrossEncoderReranker] = (
            None if settings.disable_rerank else CrossEncoderReranker(settings.rerank_model_path)
        )

    async def startup(self) -> None:
        if self.reranker:
            await self.reranker.load()

    async def shutdown(self) -> None:
        await asyncio.gather(
            self.qdrant.close(),
            self.meili.close(),
            self.falkor.close(),
            self.embedder.close(),
            return_exceptions=True,
        )

    async def retrieve(self, req: RetrievalRequest) -> RetrievalResponse:
        meta = req.metadata or {}
        issue_path = meta.get("issue_path") or ""
        issue_symbol = meta.get("issue_symbol") or ""
        # knowledge_id ↔ collection/index/graph 매핑 — Phase 4 에서 metadata 또는 매핑 테이블로 정밀화.
        # 현재 단계에서는 knowledge_id 자체를 그대로 사용 (Dify Dataset 명 = Meili index 명 = Falkor graph 명 가정).
        backend_id = meta.get("backend_id") or req.knowledge_id

        qvec = await self.embedder.embed(req.query)

        tasks: Dict[str, "asyncio.Future[List[Dict[str, Any]]]"] = {
            "dense": asyncio.ensure_future(
                self.qdrant.search(qvec, top_n=self.settings.top_n_per_path, collection=backend_id)
            ),
        }
        if not self.settings.disable_sparse:
            tasks["sparse"] = asyncio.ensure_future(
                self.meili.search(req.query, top_n=self.settings.top_n_per_path, index=backend_id)
            )
        if not self.settings.disable_graph and issue_path and issue_symbol:
            tasks["graph"] = asyncio.ensure_future(
                self.falkor.blast_radius(issue_path, issue_symbol, n_hop=2, top_n=30, graph_name=backend_id)
            )

        results: Dict[str, List[Dict[str, Any]]] = {}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for path, hits in zip(tasks.keys(), gathered):
            results[path] = [] if isinstance(hits, BaseException) else hits

        fused = self._rrf_fuse(results, k=self.settings.rrf_k)
        fused = self._apply_layer_weights(fused)

        top_n = fused[: max(self.settings.top_k_final * 10, 100)]
        top_k = req.retrieval_setting.top_k or self.settings.top_k_final

        if self.reranker and self.reranker.loaded and len(top_n) > top_k:
            scores = await self.reranker.score(req.query, [h.get("content", "") for h in top_n])
            ranked = sorted(zip(top_n, scores), key=lambda x: -x[1])
            final_pairs = ranked[:top_k]
            final = [self._to_record(h, float(s)) for h, s in final_pairs]
        else:
            final = [self._to_record(h, float(h.get("_fused_score", 0.0))) for h in top_n[:top_k]]

        threshold = req.retrieval_setting.score_threshold
        if threshold and threshold > 0:
            final = [r for r in final if r.score >= threshold]
        return RetrievalResponse(records=final)

    def _rrf_fuse(
        self, results: Dict[str, List[Dict[str, Any]]], k: int = 60
    ) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion. score = sum over paths of 1 / (k + rank).

        동일 청크가 여러 path 에서 hit 시 점수 합산. source_layer 는 우선순위
        graph > sparse > dense (구조 신호 우선).
        """
        if not results:
            return []
        scores: Dict[str, float] = {}
        idx: Dict[str, Dict[str, Any]] = {}
        layers_seen: Dict[str, set] = {}
        for path, hits in results.items():
            for rank, h in enumerate(hits or []):
                hid = h.get("id") or self._compose_id(h)
                if not hid:
                    continue
                rrf = 1.0 / (k + rank + 1)
                scores[hid] = scores.get(hid, 0.0) + rrf
                # 이미 들어온 hit 의 content 가 비어있고 (graph) 새 hit content 가 있으면 (dense/sparse) 보강
                if hid not in idx or (not idx[hid].get("content") and h.get("content")):
                    idx[hid] = h
                layers_seen.setdefault(hid, set()).add(path)
        sorted_ids = sorted(scores, key=lambda i: scores[i], reverse=True)
        out: List[Dict[str, Any]] = []
        for hid in sorted_ids:
            h = dict(idx[hid])
            h["_fused_score"] = scores[hid]
            ls = layers_seen[hid]
            for preferred in _LAYER_PRIORITY:
                if preferred in ls:
                    h["source_layer"] = preferred
                    break
            out.append(h)
        return out

    @staticmethod
    def _compose_id(h: Dict[str, Any]) -> str:
        path = h.get("path", "")
        symbol = h.get("symbol", "")
        if path or symbol:
            return f"{path}::{symbol}::{h.get('start_line', 0)}"
        return ""

    def _apply_layer_weights(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """L2a / L2c layer 별 가중치. hit.kb_layer 미지정 시 'l2a' 기본값 (현행 단일 dataset 호환)."""
        if not self.layer_weights:
            return hits
        for h in hits:
            layer = h.get("kb_layer") or "l2a"
            mult = self.layer_weights.get(layer, 1.0)
            h["_fused_score"] = float(h.get("_fused_score", 0.0)) * mult
        hits.sort(key=lambda x: -float(x.get("_fused_score", 0.0)))
        return hits

    @staticmethod
    def _to_record(hit: Dict[str, Any], score: float) -> Record:
        path = hit.get("path", "")
        symbol = hit.get("symbol", "")
        title = f"{path}::{symbol}" if (path or symbol) else hit.get("title", "")
        meta: Dict[str, Any] = {
            "path": path,
            "symbol": symbol,
            "source_layer": hit.get("source_layer", ""),
            "kb_layer": hit.get("kb_layer", "l2a"),
        }
        for k in ("match_reason", "kind", "lang", "endpoint", "is_test"):
            if k in hit:
                meta[k] = hit[k]
        return Record(content=hit.get("content", ""), score=float(score), title=title, metadata=meta)
