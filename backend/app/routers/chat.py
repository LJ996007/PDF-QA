"""RAG chat routes."""

import json
import logging
from typing import List

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest
from app.routers.documents import _get_or_create_doc_lock, documents, get_consistent_recognized_pages
from app.services.llm_router import llm_router
from app.services.rag_engine import rag_engine

logger = logging.getLogger(__name__)
router = APIRouter()


def _sorted_unique_pages(pages: List[int]) -> List[int]:
    return sorted(set(int(p) for p in pages if isinstance(p, int)))


@router.post("/chat")
async def chat(request: ChatRequest):
    """RAG chat endpoint (SSE)."""
    doc_id = request.document_id
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found")

    if request.allowed_pages:
        allowed_pages = _sorted_unique_pages(request.allowed_pages)
    else:
        lock = _get_or_create_doc_lock(doc_id)
        async with lock:
            doc = documents.get(doc_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            allowed_pages = get_consistent_recognized_pages(doc)

    log_message = f"chat_allowed_pages doc_id={doc_id} count={len(allowed_pages)} sample={allowed_pages[:10]}"
    print(log_message, flush=True)
    logger.info(log_message)

    async def event_generator():
        if not allowed_pages:
            hint = "当前没有可检索页面。请先在左侧选择页面并执行识别，然后再提问。"
            yield {
                "event": "message",
                "data": json.dumps({"type": "thinking", "content": "No recognized pages"}),
            }
            yield {
                "event": "message",
                "data": json.dumps({"type": "references", "refs": []}),
            }
            yield {
                "event": "message",
                "data": json.dumps({"type": "content", "text": hint, "active_refs": []}),
            }
            yield {
                "event": "message",
                "data": json.dumps({"type": "done", "final_refs": []}),
            }
            return

        chunks = await rag_engine.retrieve(
            query=request.question,
            doc_id=doc_id,
            top_k=10,
            api_key=request.zhipu_api_key,
            allowed_pages=allowed_pages,
            ensure_page_coverage=True,
        )

        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "thinking",
                    "content": f"正在已识别页面中检索相关内容，找到 {len(chunks)} 个片段",
                }
            ),
        }

        refs_data = [
            {
                "ref_id": chunk.ref_id,
                "chunk_id": chunk.id,
                "page": chunk.page_number,
                "bbox": chunk.bbox.model_dump(),
                "content": chunk.content[:100] + "..." if len(chunk.content) > 100 else chunk.content,
            }
            for chunk in chunks
        ]

        yield {
            "event": "message",
            "data": json.dumps({"type": "references", "refs": refs_data}),
        }

        if not chunks:
            hint = "已识别页面中未检索到相关内容，请尝试先识别更多页面后再提问。"
            yield {
                "event": "message",
                "data": json.dumps({"type": "content", "text": hint, "active_refs": []}),
            }
            yield {
                "event": "message",
                "data": json.dumps({"type": "done", "final_refs": []}),
            }
            return

        all_refs = set()
        async for chunk in llm_router.chat_stream(
            question=request.question,
            chunks=chunks,
            history=request.history,
            zhipu_api_key=request.zhipu_api_key,
            deepseek_api_key=request.deepseek_api_key,
        ):
            if chunk["type"] == "content":
                for ref in chunk.get("active_refs", []):
                    all_refs.add(ref)
                yield {
                    "event": "message",
                    "data": json.dumps(
                        {
                            "type": "content",
                            "text": chunk["content"],
                            "active_refs": chunk.get("active_refs", []),
                        }
                    ),
                }
            elif chunk["type"] == "done":
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "done", "final_refs": list(all_refs)}),
                }
            elif chunk["type"] == "error":
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "error", "content": chunk["content"]}),
                }

    return EventSourceResponse(event_generator())


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, page: int | None = None):
    """Debug endpoint to inspect chunks."""
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found")

    allowed_pages = [page] if page else None
    chunks = await rag_engine.retrieve(
        query="",
        doc_id=doc_id,
        top_k=100,
        allowed_pages=allowed_pages,
    )

    return {
        "chunks": [
            {
                "id": chunk.id,
                "page": chunk.page_number,
                "content": chunk.content,
                "bbox": chunk.bbox.model_dump(),
                "source": chunk.source_type,
            }
            for chunk in chunks
        ]
    }
