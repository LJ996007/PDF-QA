"""
RAG对话路由
"""
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, TextChunk
from app.services.rag_engine import rag_engine
from app.services.llm_router import llm_router
from app.routers.documents import documents, ensure_document_loaded
from app.services.document_store import document_store


router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    RAG对话接口（SSE流式返回）
    """
    doc_id = request.document_id
    
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    
    # 1. 检索相关片段（增加到10个以覆盖更多相关内容）
    chunks = await rag_engine.retrieve(
        query=request.question,
        doc_id=doc_id,
        top_k=10,
        api_key=request.zhipu_api_key
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
    
    async def event_generator():
        # 发送thinking状态
        yield {
            "event": "message",
            "data": json.dumps({
                "type": "thinking",
                "content": f"正在检索相关内容...找到 {len(chunks)} 个相关片段"
            })
        }
        
        # 发送检索到的引用信息
        yield {
            "event": "message",
            "data": json.dumps({
                "type": "references",
                "refs": refs_data
            })
        }

        # If we cannot retrieve any chunk, do not call the LLM with an empty context.
        # This avoids hallucinated citations and gives the user actionable next steps.
        if not chunks:
            doc = documents.get(doc_id, {})
            ocr_pages = doc.get("ocr_required_pages") or []

            hint = "未从文档索引中检索到任何内容。"
            if ocr_pages:
                hint += f" 该文档可能是扫描件（{len(ocr_pages)} 页需要 OCR）。请在设置中配置可用的 OCR 服务（百度 PP-OCR 的 API 地址/Token），或检查 OCR 是否调用成功。"
            else:
                hint += " 请确认文档是否已成功解析并建立索引。"

            yield {
                "event": "message",
                "data": json.dumps({
                    "type": "content",
                    "text": hint,
                    "active_refs": []
                })
            }
            yield {
                "event": "message",
                "data": json.dumps({
                    "type": "done",
                    "final_refs": []
                })
            }

            # Persist this Q/A hint so it appears in chat history.
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
            except Exception as e:
                print(f"[CHAT_STORE] Failed to persist hint: {e}")

            return
        
        # 2. 流式生成回答
        all_refs = set()
        assistant_text = ""
        
        async for chunk in llm_router.chat_stream(
            question=request.question,
            chunks=chunks,
            history=request.history,
            zhipu_api_key=request.zhipu_api_key,
            deepseek_api_key=request.deepseek_api_key
        ):
            if chunk["type"] == "content":
                assistant_text += chunk["content"]
                # 收集所有引用
                for ref in chunk.get("active_refs", []):
                    all_refs.add(ref)
                
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "type": "content",
                        "text": chunk["content"],
                        "active_refs": chunk.get("active_refs", [])
                    })
                }
            
            elif chunk["type"] == "done":
                # Persist chat history on completion.
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
                except Exception as e:
                    print(f"[CHAT_STORE] Failed to persist chat: {e}")

                yield {
                    "event": "message",
                    "data": json.dumps({
                        "type": "done",
                        "final_refs": list(all_refs)
                    })
                }
            
            elif chunk["type"] == "error":
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "type": "error",
                        "content": chunk["content"]
                    })
                }
    
    return EventSourceResponse(event_generator())


@router.get("/documents/{doc_id}/chat_history")
async def get_chat_history(doc_id: str):
    """Return persisted chat history for a document."""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return document_store.load_chat(doc_id)


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, page: int = None):
    """获取文档的所有文本块（用于调试）"""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    
    # 使用空查询检索所有
    chunks = await rag_engine.retrieve(
        query="",
        doc_id=doc_id,
        top_k=100
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
                "source": c.source_type
            }
            for c in chunks
        ]
    }
