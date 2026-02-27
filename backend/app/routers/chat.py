"""RAG 对话路由。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, TextChunk
from app.routers.documents import documents, ensure_document_loaded
from app.services.document_store import document_store
from app.services.llm_router import llm_router
from app.services.rag_engine import rag_engine

router = APIRouter()


def _estimate_context_tokens(chunks: list[TextChunk]) -> int:
    # Lightweight estimate to keep metrics cheap: ~4 chars per token.
    total_chars = sum(len((chunk.content or "").strip()) for chunk in chunks)
    if total_chars <= 0:
        return 0
    return max(1, total_chars // 4)


def _record_context_metrics(doc_id: str, context_tokens: int) -> None:
    if context_tokens <= 0:
        return

    meta = document_store.get_by_doc_id(doc_id) or {}
    prev_count = 0
    try:
        prev_count = int(meta.get("context_query_count") or 0)
    except (TypeError, ValueError):
        prev_count = 0

    prev_avg_raw = meta.get("avg_context_tokens")
    try:
        prev_avg = float(prev_avg_raw) if prev_avg_raw is not None else 0.0
    except (TypeError, ValueError):
        prev_avg = 0.0

    new_count = prev_count + 1
    new_avg = ((prev_avg * prev_count) + float(context_tokens)) / float(new_count)

    try:
        document_store.upsert_doc(
            {
                "doc_id": doc_id,
                "avg_context_tokens": round(new_avg, 2),
                "context_query_count": new_count,
            }
        )
    except Exception as exc:
        print(f"[METRICS] Failed to persist context metrics for {doc_id}: {exc}")

    doc = documents.get(doc_id)
    if isinstance(doc, dict):
        doc["avg_context_tokens"] = round(new_avg, 2)
        doc["context_query_count"] = new_count


@router.post("/chat")
async def chat(request: ChatRequest):
    """流式返回带引用的 RAG 答案。"""
    doc_id = request.document_id

    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    chunks = await rag_engine.retrieve(
        query=request.question,
        doc_id=doc_id,
        top_k=10,
        api_key=request.zhipu_api_key,
    )

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
    _record_context_metrics(doc_id, _estimate_context_tokens(chunks))

    async def event_generator():
        yield {
            "event": "message",
            "data": json.dumps(
                {
                    "type": "thinking",
                    "content": f"正在检索相关内容... 已找到 {len(chunks)} 个片段。",
                }
            ),
        }

        yield {
            "event": "message",
            "data": json.dumps({"type": "references", "refs": refs_data}),
        }

        # 检索不到内容时，不调用 LLM，避免空上下文幻觉。
        if not chunks:
            doc = documents.get(doc_id, {})
            ocr_pages = doc.get("ocr_required_pages") or []

            hint = "未从该文档索引中检索到可用内容。"
            if ocr_pages:
                hint += (
                    f" 该文件可能是扫描件，仍有 {len(ocr_pages)} 页需要 OCR。"
                    " 请先配置 OCR 服务并重新识别。"
                )
            else:
                hint += " 请确认文档解析和索引流程已成功完成。"

            yield {
                "event": "message",
                "data": json.dumps({"type": "content", "text": hint, "active_refs": []}),
            }
            yield {
                "event": "message",
                "data": json.dumps({"type": "done", "final_refs": []}),
            }

            try:
                ts = datetime.now(timezone.utc).isoformat()
                user_msg = {
                    "id": f"user_{uuid.uuid4().hex[:12]}",
                    "role": "user",
                    "content": request.question,
                    "timestamp": ts,
                }
                assistant_msg = {
                    "id": f"assistant_{uuid.uuid4().hex[:12]}",
                    "role": "assistant",
                    "content": hint,
                    "timestamp": ts,
                    "references": [],
                }
                document_store.append_chat(doc_id, user_msg, assistant_msg)
            except Exception as exc:
                print(f"[CHAT_STORE] Failed to persist hint: {exc}")

            return

        all_refs = set()
        assistant_text = ""

        async for chunk in llm_router.chat_stream(
            question=request.question,
            chunks=chunks,
            history=request.history,
            zhipu_api_key=request.zhipu_api_key,
            deepseek_api_key=request.deepseek_api_key,
        ):
            if chunk["type"] == "content":
                assistant_text += chunk["content"]
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
                try:
                    ts = datetime.now(timezone.utc).isoformat()
                    user_msg = {
                        "id": f"user_{uuid.uuid4().hex[:12]}",
                        "role": "user",
                        "content": request.question,
                        "timestamp": ts,
                    }
                    assistant_msg = {
                        "id": f"assistant_{uuid.uuid4().hex[:12]}",
                        "role": "assistant",
                        "content": assistant_text,
                        "timestamp": ts,
                        "references": refs_data,
                    }
                    document_store.append_chat(doc_id, user_msg, assistant_msg)
                except Exception as exc:
                    print(f"[CHAT_STORE] Failed to persist chat: {exc}")

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


@router.get("/documents/{doc_id}/chat_history")
async def get_chat_history(doc_id: str):
    """获取文档已持久化的聊天历史。"""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return document_store.load_chat(doc_id)


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, page: Optional[int] = None):
    """获取索引片段（调试用）。"""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    chunks = await rag_engine.retrieve(
        query="",
        doc_id=doc_id,
        top_k=100,
    )

    if page:
        chunks = [c for c in chunks if c.page_number == page]

    return {
        "chunks": [
            {
                "id": c.id,
                "page": c.page_number,
                "content": c.content,
                "bbox": c.bbox.model_dump(),
                "source": c.source_type,
            }
            for c in chunks
        ]
    }
