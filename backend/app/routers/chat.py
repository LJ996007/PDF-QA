"""
RAG对话路由
"""
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import ChatRequest, TextChunk, BoundingBox
from app.services.rag_engine import rag_engine
from app.services.llm_router import llm_router
from app.routers.documents import documents
from app.services.pdf_render import render_pdf_page_to_png_base64
from app.services.vision_gateway import describe_page_image
from app.services.vision_utils import select_candidate_pages


router = APIRouter()

VISION_PROMPT_V1 = """你将看到一页 PDF 的截图（可能包含图表/流程图/示意图/图片/表格）。请只基于图片内容，输出简洁、可检索的中文要点摘要。
要求：
1. 只描述你能从图中确认的内容，不要编造；不确定就明确说不确定。
2. 如果是图表：说明图的主题、坐标轴/图例含义、趋势/对比结论；若能清晰读到关键数值可列出 3-8 个。
3. 如果是流程图/示意图：说明主要模块/步骤及它们的关系。
4. 如果这页没有有意义的非文字视觉信息（只有普通段落文字），只输出“无”。
输出格式：最多 10 条项目符号（- 开头）。"""


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

        # Optional: on-demand vision enrichment (adds extra citeable chunks)
        if getattr(request, "vision_enabled", False):
            doc = documents.get(doc_id, {})
            file_path = doc.get("file_path")
            total_pages = doc.get("total_pages")
            vision_cache = doc.setdefault("vision_cache", {})

            candidate_pages = select_candidate_pages(
                explicit_pages=request.vision_pages,
                chunk_pages=[getattr(c, "page_number", 0) for c in chunks],
                max_pages=getattr(request, "vision_max_pages", 2),
                total_pages=total_pages,
            )

            if file_path:
                next_ref_index = len(chunks) + 1
                for i, page_num in enumerate(candidate_pages):
                    page_num = int(page_num)
                    cache_key = f"v1|{request.vision_model}|p{page_num}"
                    vision_text = vision_cache.get(cache_key)
                    page_w_pt = 100.0
                    page_h_pt = 100.0

                    if not vision_text:
                        try:
                            img_b64, page_w_pt, page_h_pt = render_pdf_page_to_png_base64(
                                file_path, page_num, dpi=150
                            )
                            vision_text = await describe_page_image(
                                image_b64_png=img_b64,
                                prompt=VISION_PROMPT_V1,
                                api_key=request.vision_api_key,
                                base_url=request.vision_base_url,
                                model=request.vision_model,
                                timeout_s=90.0,
                            )
                            vision_cache[cache_key] = vision_text
                        except Exception:
                            continue
                    else:
                        # We still want a correct page bbox; best-effort.
                        try:
                            _img_b64, page_w_pt, page_h_pt = render_pdf_page_to_png_base64(
                                file_path, page_num, dpi=72
                            )
                        except Exception:
                            pass

                    if not vision_text:
                        continue

                    chunks.append(
                        TextChunk(
                            id=f"vision_{doc_id}_p{page_num}_{i}",
                            document_id=doc_id,
                            page_number=page_num,
                            content=f"[视觉摘要]\\n{vision_text}",
                            bbox=BoundingBox(page=page_num, x=0.0, y=0.0, w=float(page_w_pt), h=float(page_h_pt)),
                            source_type="vision",
                            distance=0.0,
                            ref_id=f"ref-{next_ref_index + i}",
                        )
                    )

        # 发送检索到的引用信息
        refs_data = [
            {
                "ref_id": chunk.ref_id,
                "chunk_id": chunk.id,
                "page": chunk.page_number,
                "bbox": chunk.bbox.model_dump(),
                "source": chunk.source_type,
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
            return
        
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
