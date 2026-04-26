"""retrieve-svc — Dify External Knowledge API adapter (POST /retrieval).

Hybrid (dense + sparse + graph) retrieve → RRF fusion → CrossEncoder rerank → top-K records.
Dify 1.13.3 External KB API spec 호환 — metadata 필드는 항상 dict (null 금지).

기동: supervisord 의 [program:retrieve-svc] 가 uvicorn 으로 실행.
헬스: GET /health (Dify 연결 점검 + 운영 모니터링).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.fusion import HybridRetriever
from app.schema import HealthResponse, RetrievalRequest, RetrievalResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    retriever = HybridRetriever(settings)
    await retriever.startup()
    app.state.retriever = retriever
    try:
        yield
    finally:
        await retriever.shutdown()


app = FastAPI(title="retrieve-svc", version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    retriever: HybridRetriever = app.state.retriever
    rerank_loaded = bool(retriever.reranker and retriever.reranker.loaded)
    return HealthResponse(status="ok", rerank_loaded=rerank_loaded)


@app.post("/retrieval", response_model=RetrievalResponse)
async def retrieval(req: RetrievalRequest) -> RetrievalResponse:
    retriever: HybridRetriever = app.state.retriever
    return await retriever.retrieve(req)
