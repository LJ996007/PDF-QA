"""RAG 对话路由。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, TextChunk
from app.routers.documents import _build_page_image_inputs, documents, ensure_document_loaded
from app.services.document_store import document_store
from app.services.mm_provider import PageImageInput, get_multimodal_provider
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


def _serialize_chunk_reference(chunk: TextChunk, fallback_index: Optional[int] = None) -> dict:
    ref_id = chunk.ref_id or (f"ref-{fallback_index}" if fallback_index is not None else chunk.id)
    return {
        "ref_id": ref_id,
        "chunk_id": chunk.id,
        "page": chunk.page_number,
        "bbox": chunk.bbox.model_dump(),
        "content": chunk.content[:100] + "..." if len(chunk.content) > 100 else chunk.content,
    }


def _build_page_level_references(page_inputs: list[PageImageInput]) -> list[dict]:
    refs: list[dict] = []
    for index, page_input in enumerate(page_inputs, start=1):
        refs.append(
            {
                "ref_id": f"ref-{index}",
                "chunk_id": f"page-{page_input.page}",
                "page": page_input.page,
                "bbox": {
                    "page": page_input.page,
                    "x": 36.0,
                    "y": 36.0,
                    "w": max(100.0, page_input.width * 0.85),
                    "h": max(30.0, min(96.0, page_input.height * 0.15)),
                },
                "content": f"第{page_input.page}页页面图像",
            }
        )
    return refs


def _collect_effective_allowed_pages(request: ChatRequest) -> list[int]:
    if request.page_reference_groups:
        grouped_pages = sorted(
            {
                int(page)
                for group in request.page_reference_groups
                for page in group.pages
                if int(page) > 0
            }
        )
        if grouped_pages:
            return grouped_pages

    return sorted({int(page) for page in request.allowed_pages if int(page) > 0})


@router.post("/chat")
async def chat(request: ChatRequest):
    """流式返回带引用的 RAG 答案。"""
    doc_id = request.document_id

    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    effective_allowed_pages = _collect_effective_allowed_pages(request)
    chunks = await rag_engine.retrieve(
        query=request.question,
        doc_id=doc_id,
        top_k=10,
        api_key=request.zhipu_api_key,
        allowed_pages=effective_allowed_pages if effective_allowed_pages else None,
    )

    refs_data = [_serialize_chunk_reference(chunk, index + 1) for index, chunk in enumerate(chunks)]
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

        if request.use_vision:
            doc = documents.get(doc_id) or {}
            total_pages = int(doc.get("total_pages") or 0)
            if effective_allowed_pages:
                pages_to_view = effective_allowed_pages
            elif chunks:
                pages_to_view = sorted({c.page_number for c in chunks}) or [1]
            elif total_pages > 0:
                pages_to_view = list(range(1, min(total_pages, 10) + 1))
            else:
                pages_to_view = [1]

            pages_to_view = pages_to_view[:10]

            file_path = str(doc.get("file_path") or os.path.join("uploads", f"{doc_id}.pdf"))
            try:
                page_inputs = _build_page_image_inputs(file_path, pages_to_view)
            except Exception as exc:
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "error", "content": f"加载页面图像失败: {exc}"}),
                }
                return

            page_lookup = {image.page for image in page_inputs}
            relevant_chunks = [chunk for chunk in chunks if chunk.page_number in page_lookup]
            visual_refs = (
                [_serialize_chunk_reference(chunk, index + 1) for index, chunk in enumerate(relevant_chunks)]
                if relevant_chunks
                else _build_page_level_references(page_inputs)
            )

            yield {
                "event": "message",
                "data": json.dumps({"type": "references", "refs": visual_refs}),
            }

            vision_prompt = llm_router.build_multimodal_prompt(
                question=request.question,
                references=visual_refs,
                chunks=relevant_chunks or None,
                page_reference_groups=request.page_reference_groups,
            )
            try:
                provider = get_multimodal_provider(request.multimodal_provider)
                result = await provider.analyze_pages(
                    images=page_inputs,
                    prompt=vision_prompt,
                    json_schema=None,
                    api_key=request.multimodal_api_key,
                    model=request.multimodal_model or None,
                    base_url=request.multimodal_base_url,
                    provider_name=request.multimodal_provider,
                )
                answer_text = result.get("answer") or result.get("text") or str(result)
            except Exception as exc:
                error_message = str(exc).strip() or "未知错误"
                if not error_message.startswith("多模态模型调用失败"):
                    error_message = f"多模态模型调用失败: {error_message}"
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "error", "content": error_message}),
                }
                return

            try:
                ts = datetime.now(timezone.utc).isoformat()
                user_msg = {
                    "id": f"user_{uuid.uuid4().hex[:12]}",
                    "role": "user",
                    "content": request.question,
                    "page_reference_groups": [group.model_dump() for group in request.page_reference_groups],
                    "timestamp": ts,
                }
                assistant_msg = {
                    "id": f"assistant_{uuid.uuid4().hex[:12]}",
                    "role": "assistant",
                    "content": answer_text,
                    "timestamp": ts,
                    "references": visual_refs,
                }
                document_store.append_chat(doc_id, user_msg, assistant_msg)
            except Exception as exc:
                print(f"[CHAT_STORE] Failed to persist vision chat: {exc}")

            final_refs = list(dict.fromkeys(llm_router.extract_ref_ids(answer_text)))
            yield {
                "event": "message",
                "data": json.dumps({"type": "content", "text": answer_text, "active_refs": final_refs}),
            }
            yield {
                "event": "message",
                "data": json.dumps({"type": "done", "final_refs": final_refs}),
            }
            return

        yield {
            "event": "message",
            "data": json.dumps({"type": "references", "refs": refs_data}),
        }

        # 检索不到内容时，不调用 LLM，避免空上下文幻觉。
        if not chunks:
            doc = documents.get(doc_id, {})
            ocr_pages = doc.get("ocr_required_pages") or []
            indexed_chunks = int(doc.get("indexed_chunks") or doc.get("chunk_count") or 0)
            recognized_pages = len(doc.get("recognized_pages") or [])
            has_partial_index = indexed_chunks > 0 or recognized_pages > 0

            if has_partial_index:
                hint = "根据现有片段无法确认。当前问题在已建立的文档索引中未检索到匹配内容。"
                if request.page_reference_groups:
                    hint += " 可尝试检查页面组是否选得过窄，或换一个更接近原文的关键词再试。"
                elif effective_allowed_pages:
                    hint += " 可尝试放宽页码范围，或换一个更接近原文的关键词再试。"
                else:
                    hint += " 可尝试换一个更接近原文的关键词，或限定到更相关的页码范围。"
                if ocr_pages:
                    hint += f" 当前仍有 {len(ocr_pages)} 页待 OCR，补齐后可进一步提升召回率。"
            else:
                hint = "根据现有片段无法确认。未从该文档索引中检索到可用内容。"
                if ocr_pages:
                    hint += (
                        f" 该文件可能是扫描件，仍有 {len(ocr_pages)} 页需要 OCR。"
                        " 请先配置 OCR 服务并重新识别。"
                    )
                else:
                    hint += " 请确认文档解析和索引流程已成功完成。"

            if not has_partial_index and ocr_pages:
                hint += (
                    ""
                )

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
                    "page_reference_groups": [group.model_dump() for group in request.page_reference_groups],
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
            page_reference_groups=request.page_reference_groups,
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
                        "page_reference_groups": [group.model_dump() for group in request.page_reference_groups],
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
