"""
RAG对话路由
"""
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, TextChunk
from app.services.rag_engine import rag_engine
from app.services.llm_router import llm_router
from app.routers.documents import documents


router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    """
    RAG对话接口（SSE流式返回）
    """
    doc_id = request.document_id
    
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="文档不存在")
    
    # 1. 检索相关片段（增加到10个以覆盖更多相关内容）
    chunks = await rag_engine.retrieve(
        query=request.question,
        doc_id=doc_id,
        top_k=10,
        api_key=request.zhipu_api_key
    )
    
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
        refs_data = [
            {
                "ref_id": chunk.ref_id,
                "chunk_id": chunk.id,
                "page": chunk.page_number,
                "bbox": chunk.bbox.model_dump(),
                "content": chunk.content[:100] + "..." if len(chunk.content) > 100 else chunk.content
            }
            for chunk in chunks
        ]
        
        yield {
            "event": "message",
            "data": json.dumps({
                "type": "references",
                "refs": refs_data
            })
        }
        
        # 2. 流式生成回答
        all_refs = set()
        
        async for chunk in llm_router.chat_stream(
            question=request.question,
            chunks=chunks,
            history=request.history,
            zhipu_api_key=request.zhipu_api_key,
            deepseek_api_key=request.deepseek_api_key
        ):
            if chunk["type"] == "content":
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


@router.get("/documents/{doc_id}/chunks")
async def get_chunks(doc_id: str, page: int = None):
    """获取文档的所有文本块（用于调试）"""
    if doc_id not in documents:
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
