"""
文档上传与处理路由
"""
import os
import uuid
import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional, List
from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException, Response
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.encoders import jsonable_encoder
from sse_starlette.sse import EventSourceResponse
import json
from pydantic import BaseModel

from app.models.schemas import DocumentUploadResponse, ProgressEvent
from app.services.parser import process_document, get_ocr_required_pages
from app.services.rag_engine import rag_engine
from app.services.ocr_gateway import ocr_gateway
from app.services.baidu_ocr import baidu_ocr_gateway
from app.services.local_ocr import local_ocr_gateway
from app.services.document_store import document_store

# 配置日志
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

router = APIRouter()

# 存储文档处理进度
document_progress: Dict[str, ProgressEvent] = {}

# 存储文档信息
documents: Dict[str, dict] = {}

KEEP_PDF = os.getenv("KEEP_PDF", "1").strip().lower() in ("1", "true", "yes", "y")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _safe_abspath(path: str) -> str:
    # Resolve relative paths against current working directory.
    return os.path.abspath(path)


def _is_allowed_pdf_path(path: str) -> bool:
    if not path:
        return False
    ap = _safe_abspath(path)
    uploads_dir = _safe_abspath("uploads")
    doc_store_dir = _safe_abspath("doc_store")
    for base in (uploads_dir, doc_store_dir):
        if ap == base:
            return True
        if ap.startswith(base + os.sep):
            return True
    return False


def _resolve_pdf_path(doc_id: str) -> Optional[str]:
    path = (documents.get(doc_id) or {}).get("file_path")
    if not path:
        meta = document_store.get_by_doc_id(doc_id)
        path = meta.get("pdf_path") if meta else None
    if not path:
        return None
    if not _is_allowed_pdf_path(path):
        return None
    if not os.path.exists(path):
        return None
    return path


def _load_doc_meta_into_memory(meta: dict) -> None:
    doc_id = meta.get("doc_id")
    if not doc_id:
        return

    # Minimal in-memory record so /api/chat and /api/documents/{doc_id} work after restart.
    documents[doc_id] = {
        "id": doc_id,
        "name": meta.get("filename") or meta.get("name") or doc_id,
        "total_pages": int(meta.get("total_pages") or 0),
        "ocr_required_pages": list(meta.get("ocr_required_pages") or []),
        "thumbnails": [],
        "file_path": meta.get("pdf_path"),  # may be None when KEEP_PDF=0
        "pages": [],  # not persisted
    }


def load_persisted_documents() -> None:
    for meta in document_store.list_docs():
        if meta.get("status") == "completed":
            _load_doc_meta_into_memory(meta)


def ensure_document_loaded(doc_id: str) -> bool:
    if doc_id in documents:
        return True
    meta = document_store.get_by_doc_id(doc_id)
    if meta and meta.get("status") == "completed":
        _load_doc_meta_into_memory(meta)
        return True
    return False


async def process_document_async(
    doc_id: str, 
    file_path: str, 
    filename: str,
    sha256: str,
    created_at: str,
    api_key: Optional[str] = None,
    ocr_model: str = "glm-4v-flash",
    ocr_provider: str = "zhipu",
    baidu_ocr_url: Optional[str] = None,
    baidu_ocr_token: Optional[str] = None,
    keep_pdf: bool = KEEP_PDF,
):
    """后台处理文档，支持智谱和百度两种OCR提供商"""
    logger.info(f"[DOC] Starting processing for {doc_id}, file: {filename}, ocr_provider: {ocr_provider}")
    try:
        ocr_payload = {"doc_id": doc_id, "sha256": sha256, "pages": []}

        # 阶段1：提取文本
        document_progress[doc_id] = ProgressEvent(
            stage="extracting",
            current=0,
            total=100,
            message="正在解析PDF...",
            document_id=doc_id
        )
        
        logger.info(f"[DOC] Calling parser.process_document for {file_path}")
        pages, thumbnails = process_document(file_path)
        total_pages = len(pages)
        logger.info(f"[DOC] Parser returned {total_pages} pages")
        
        # 检查是否需要OCR
        ocr_pages = [p for p in pages if p.type == "ocr"]
        ocr_count = len(ocr_pages)
        logger.info(f"[DOC] Pages needing OCR: {ocr_count}")
        
        
        if ocr_count > 0:
            # 检查是否有可用的OCR凭证
            has_baidu_key = bool(baidu_ocr_url and baidu_ocr_token)
            
            logger.info(f"[DOC] OCR provider: {ocr_provider}, has_baidu_key: {has_baidu_key}")
            
            # 执行OCR
            # If Baidu credentials are missing or Baidu auth fails, use local OCR fallback.
            force_local_ocr = not has_baidu_key
            if not has_baidu_key:
                logger.warning("[DOC] Document needs OCR but Baidu credentials not provided. Using local OCR fallback.")

            for idx, page in enumerate(ocr_pages):
                print(f"[DEBUG] Processing OCR for page {idx+1}/{ocr_count}")
                progress_percent = int(10 + (idx / ocr_count) * 40)
                document_progress[doc_id] = ProgressEvent(
                    stage="extracting",
                    current=progress_percent,
                    total=100,
                    message=f"正在进行OCR识别 ({idx+1}/{ocr_count})...",
                    document_id=doc_id
                )
                
                if page.image_base64:
                    try:
                        import base64
                        from PIL import Image
                        import io
                        
                        img_data = base64.b64decode(page.image_base64)
                        with Image.open(io.BytesIO(img_data)) as img:
                            width, height = img.size
                            print(f"[DEBUG] Image size: {width}x{height}")
                            pdf_width = width * 72 / 150
                            pdf_height = height * 72 / 150
                            
                            print(f"[DEBUG] Starting OCR for page {page.page_number}")
                            
                            chunks = []
                            provider_used = None

                            # Prefer Baidu PP-OCR when possible; fallback to local OCR if auth fails or
                            # if the provider returns no result.
                            if not force_local_ocr:
                                try:
                                    chunks = await baidu_ocr_gateway.process_image(
                                        page.image_base64,
                                        page.page_number,
                                        pdf_width,
                                        pdf_height,
                                        api_url=baidu_ocr_url,
                                        token=baidu_ocr_token
                                    )
                                    if chunks:
                                        provider_used = "baidu"
                                except PermissionError as e:
                                    force_local_ocr = True
                                    logger.error(f"[DOC] Baidu OCR auth failed: {e}. Falling back to local OCR.")
                                except Exception as e:
                                    logger.warning(f"[DOC] Baidu OCR failed: {e}. Falling back to local OCR for this page.")

                            if force_local_ocr or not chunks:
                                chunks = await local_ocr_gateway.process_image(
                                    page.image_base64,
                                    page.page_number,
                                    pdf_width,
                                    pdf_height
                                )
                                if chunks:
                                    provider_used = "local"

                            print(f"[DEBUG] OCR returned {len(chunks)} chunks")

                            ocr_payload["pages"].append({
                                "page_number": page.page_number,
                                "provider": provider_used or ("local" if force_local_ocr else "baidu"),
                                "chunks": [
                                    {"text": c.text, "bbox": c.bbox.model_dump() if c.bbox else None}
                                    for c in (chunks or [])
                                ],
                                "merged_text": "\n".join([c.text for c in (chunks or [])]).strip(),
                            })
                            
                            # 更新页面内容
                            if chunks:
                                all_text = []
                                all_coords = []
                                for chunk in chunks:
                                    all_text.append(chunk.text)
                                    if chunk.bbox:
                                        all_coords.append(chunk.bbox)
                                
                                page.text = "\n".join(all_text)
                                page.coordinates = all_coords
                                page.confidence = 0.9
                                print(f"[DEBUG] Page {page.page_number} text length: {len(page.text)}")
                            else:
                                print(f"[WARNING] OCR returned 0 chunks for page {page.page_number}")
                    except Exception as e:
                        print(f"[ERROR] OCR processing failed: {e}")
                        import traceback
                        traceback.print_exc()
        
        document_progress[doc_id] = ProgressEvent(
            stage="extracting",
            current=50,
            total=100,
            message=f"已解析 {total_pages} 页",
            document_id=doc_id
        )
        
        # 阶段2：建立向量索引
        document_progress[doc_id] = ProgressEvent(
            stage="embedding",
            current=60,
            total=100,
            message="正在建立向量索引...",
            document_id=doc_id
        )
        
        # 调试：显示每页的文本状态
        for p in pages:
            print(f"[DEBUG Index] Page {p.page_number}: type={p.type}, text_len={len(p.text) if p.text else 0}")
        
        chunk_count = await rag_engine.index_document(doc_id, pages, api_key)
        print(f"[DEBUG] Indexed {chunk_count} chunks for document {doc_id}")

        if chunk_count == 0:
            # This usually happens when the PDF is image-only and OCR failed/misconfigured.
            try:
                document_store.upsert_doc({
                    "doc_id": doc_id,
                    "sha256": sha256,
                    "filename": filename,
                    "created_at": created_at,
                    "total_pages": total_pages,
                    "ocr_required_pages": get_ocr_required_pages(pages),
                    "status": "failed",
                    "chunk_count": 0,
                    "keep_pdf": bool(keep_pdf),
                    "pdf_path": file_path if keep_pdf else None,
                })
                if ocr_payload.get("pages"):
                    document_store.save_ocr_result(doc_id, ocr_payload)
            except Exception as e:
                logger.warning(f"[DOC_STORE] Failed to persist failed doc meta: {e}")

            document_progress[doc_id] = ProgressEvent(
                stage="failed",
                current=0,
                total=100,
                message="未能从文档中提取到可索引的文本内容：如果是扫描件，请配置可用的 OCR（或使用本地 OCR 回退）。",
                document_id=doc_id
            )
            return
        
        document_progress[doc_id] = ProgressEvent(
            stage="embedding",
            current=90,
            total=100,
            message=f"已索引 {chunk_count} 个文本块",
            document_id=doc_id
        )
        
        # 获取需要OCR的页面 (此时可能已处理，更新状态)
        # ocr_pages = get_ocr_required_pages(pages) # 如果OCR成功，text不为空，这里还要再判断吗？
        # parser.py中get_ocr_required_pages是判断type=="ocr"。
        # 我们已经处理了，但type没改。保留type="ocr"以便前端知道这是OCR过的页面？
        # 或者我们应该在这里更新type="native" (伪装)?
        # 暂时保持不变，前端可能用到。
        ocr_required_pages = get_ocr_required_pages(pages)

        # 保存文档信息
        documents[doc_id] = {
            "id": doc_id,
            "name": filename,
            "total_pages": total_pages,
            "ocr_required_pages": ocr_required_pages,
            "thumbnails": thumbnails,
            "file_path": file_path,
            "pages": pages
        }

        # Persist metadata + OCR results for history reuse.
        try:
            document_store.upsert_doc({
                "doc_id": doc_id,
                "sha256": sha256,
                "filename": filename,
                "created_at": created_at,
                "total_pages": total_pages,
                "ocr_required_pages": ocr_required_pages,
                "status": "completed",
                "chunk_count": int(chunk_count),
                "keep_pdf": bool(keep_pdf),
                "pdf_path": file_path if keep_pdf else None,
            })
            document_store.save_ocr_result(doc_id, ocr_payload)
        except Exception as e:
            logger.warning(f"[DOC_STORE] Failed to persist document: {e}")

        # Default behavior: do not keep the uploaded PDF after indexing.
        if not keep_pdf:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    documents[doc_id]["file_path"] = None
            except Exception as e:
                logger.warning(f"[DOC] Failed to remove temporary PDF: {e}")
        
        # 完成
        document_progress[doc_id] = ProgressEvent(
            stage="completed",
            current=100,
            total=100,
            message="处理完成",
            document_id=doc_id
        )
        
    except Exception as e:
        print(f"Error processing document: {e}")
        import traceback
        traceback.print_exc()
        try:
            document_store.upsert_doc({
                "doc_id": doc_id,
                "sha256": sha256,
                "filename": filename,
                "created_at": created_at,
                "status": "failed",
                "keep_pdf": bool(keep_pdf),
                "pdf_path": file_path if keep_pdf else None,
            })
        except Exception:
            pass
        document_progress[doc_id] = ProgressEvent(
            stage="failed",
            current=0,
            total=100,
            message=str(e),
            document_id=doc_id
        )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    zhipu_api_key: Optional[str] = Form(None),
    ocr_model: str = Form("glm-4v-flash"),
    ocr_provider: str = Form("zhipu"),
    baidu_ocr_url: Optional[str] = Form(None),
    baidu_ocr_token: Optional[str] = Form(None)
):
    """上传PDF文档"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持PDF文件")

    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    existing = document_store.get_by_sha256(sha256)
    if existing and existing.get("status") == "completed":
        doc_id = existing.get("doc_id")
        if doc_id:
            ensure_document_loaded(doc_id)
            # Make sure progress SSE can immediately complete.
            document_progress[doc_id] = ProgressEvent(
                stage="completed",
                current=100,
                total=100,
                message="Completed (cached)",
                document_id=doc_id,
            )
            return DocumentUploadResponse(
                document_id=doc_id,
                status="completed",
                total_pages=int(existing.get("total_pages") or 0),
                ocr_required_pages=list(existing.get("ocr_required_pages") or []),
                progress_url=f"/api/documents/{doc_id}/progress",
            )

    # Reuse doc_id if we have an incomplete record for the same sha256; otherwise create a new one.
    doc_id = (existing.get("doc_id") if existing else None) or f"doc_{uuid.uuid4().hex[:12]}"
    created_at = (existing.get("created_at") if existing else None) or _now_iso_utc()

    # Save file (temporary when KEEP_PDF=0).
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{doc_id}.pdf")
    with open(file_path, "wb") as f:
        f.write(content)

    # Upsert metadata for history/dedup.
    document_store.upsert_doc({
        "doc_id": doc_id,
        "sha256": sha256,
        "filename": file.filename,
        "created_at": created_at,
        "status": "processing",
        "chunk_count": int(existing.get("chunk_count") or 0) if existing else 0,
        "keep_pdf": bool(KEEP_PDF),
        "pdf_path": file_path if KEEP_PDF else None,
    })

    # 初始化进度
    document_progress[doc_id] = ProgressEvent(
        stage="extracting",
        current=0,
        total=100,
        message="开始处理...",
        document_id=doc_id
    )
    
    # 加载环境变量中的Baidu OCR配置
    if not baidu_ocr_url:
        baidu_ocr_url = os.getenv("BAIDU_OCR_API_URL")
    if not baidu_ocr_token:
        baidu_ocr_token = os.getenv("BAIDU_OCR_TOKEN")

    # 后台处理
    background_tasks.add_task(
        process_document_async, 
        doc_id, 
        file_path, 
        file.filename,
        sha256,
        created_at,
        zhipu_api_key,
        ocr_model,
        ocr_provider,
        baidu_ocr_url,
        baidu_ocr_token,
        KEEP_PDF,
    )
    
    return DocumentUploadResponse(
        document_id=doc_id,
        status="processing",
        total_pages=0,  # 处理完成后更新
        ocr_required_pages=[],
        progress_url=f"/api/documents/{doc_id}/progress"
    )


@router.get("/{doc_id}/progress")
async def get_progress(doc_id: str):
    """SSE流式返回处理进度"""
    async def event_generator():
        last_stage = None
        
        while True:
            if doc_id not in document_progress:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "文档不存在"})
                }
                break
            
            progress = document_progress[doc_id]
            
            yield {
                "event": "progress",
                "data": json.dumps(progress.model_dump())
            }
            
            if progress.stage in ["completed", "failed"]:
                break
            
            last_stage = progress.stage
            await asyncio.sleep(0.5)
    
    return EventSourceResponse(event_generator())


@router.get("/history")
async def get_history():
    """List persisted documents for history reuse."""
    items = []
    for meta in document_store.list_docs():
        pdf_path = meta.get("pdf_path")
        has_pdf = bool(pdf_path and os.path.exists(str(pdf_path)))
        items.append({
            "doc_id": meta.get("doc_id"),
            "filename": meta.get("filename"),
            "created_at": meta.get("created_at"),
            "total_pages": int(meta.get("total_pages") or 0),
            "ocr_required_pages": list(meta.get("ocr_required_pages") or []),
            "sha256": meta.get("sha256"),
            "status": meta.get("status"),
            "keep_pdf": bool(meta.get("keep_pdf")),
            "has_pdf": has_pdf,
        })
    return items


class LookupRequest(BaseModel):
    sha256: str


@router.post("/lookup")
async def lookup_document(request: LookupRequest):
    """Lookup an existing document by PDF sha256 (no file upload required)."""
    sha256 = (request.sha256 or "").strip().lower()
    meta = document_store.get_by_sha256(sha256)
    if meta and meta.get("doc_id"):
        return {"exists": True, "doc_id": meta.get("doc_id"), "status": meta.get("status")}
    return {"exists": False}


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """获取文档信息"""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    
    doc = documents[doc_id]
    return {
        "id": doc["id"],
        "name": doc["name"],
        "total_pages": doc["total_pages"],
        "ocr_required_pages": doc["ocr_required_pages"],
        "thumbnails": doc["thumbnails"]
    }


@router.get("/{doc_id}/pdf")
async def get_document_pdf(doc_id: str):
    """Get the persisted PDF for a document (when KEEP_PDF=1)."""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    path = _resolve_pdf_path(doc_id)
    if not path:
        raise HTTPException(status_code=404, detail="该文档未保存PDF")
    return FileResponse(path, media_type="application/pdf", filename=f"{doc_id}.pdf")


@router.head("/{doc_id}/pdf")
async def head_document_pdf(doc_id: str):
    """HEAD check for persisted PDF existence."""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    path = _resolve_pdf_path(doc_id)
    if not path:
        raise HTTPException(status_code=404, detail="该文档未保存PDF")
    # Starlette/FastAPI does not always add HEAD automatically for FileResponse; provide it explicitly.
    return Response(status_code=200)


@router.post("/{doc_id}/attach_pdf")
async def attach_document_pdf(doc_id: str, file: UploadFile = File(...)):
    """Attach/persist a PDF for an existing document (used to retrofit old KEEP_PDF=0 records)."""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="只支持PDF文件")

    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    meta = document_store.get_by_doc_id(doc_id) or {}
    expected = (meta.get("sha256") or "").strip().lower()
    if expected and expected != sha256:
        raise HTTPException(status_code=400, detail="PDF与该历史记录不匹配（SHA256不同）")

    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{doc_id}.pdf")
    with open(file_path, "wb") as f:
        f.write(content)

    document_store.upsert_doc({
        "doc_id": doc_id,
        "sha256": sha256,
        "keep_pdf": True,
        "pdf_path": file_path,
    })

    if doc_id in documents:
        documents[doc_id]["file_path"] = file_path

    return {"status": "ok"}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """删除文档"""
    if not ensure_document_loaded(doc_id):
        # Still attempt to delete from persistent store and vector index.
        document_store.delete_doc(doc_id)
        document_store.delete_chat(doc_id)
        document_store.delete_compliance(doc_id)
        rag_engine.delete_document(doc_id)
        if doc_id in document_progress:
            del document_progress[doc_id]
        return {"status": "deleted"}

    if doc_id in documents:
        doc = documents[doc_id]
        # 删除文件
        file_path = doc.get("file_path")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        # 删除向量索引
        rag_engine.delete_document(doc_id)
        # 删除持久化记录
        document_store.delete_doc(doc_id)
        document_store.delete_chat(doc_id)
        document_store.delete_compliance(doc_id)
        # 删除记录
        del documents[doc_id]
    
    if doc_id in document_progress:
        del document_progress[doc_id]
    
    return {"status": "deleted"}
class ComplianceRequest(BaseModel):
    requirements: List[str]
    api_key: Optional[str] = None

@router.post("/{doc_id}/compliance")
async def check_compliance(doc_id: str, request: ComplianceRequest):
    """
    技术合规性检查
    """
    from app.services.compliance_service import compliance_service
    
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
        
    try:
        results = await compliance_service.verify_requirements(
            doc_id, 
            request.requirements, 
            api_key=request.api_key
        )
        payload = {
            "version": 1,
            "doc_id": doc_id,
            "created_at": _now_iso_utc(),
            "requirements": list(request.requirements or []),
            **(results or {}),
        }
        document_store.save_compliance(doc_id, jsonable_encoder(payload))
        return results
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{doc_id}/compliance_history")
async def get_compliance_history(doc_id: str):
    """Get latest persisted compliance result for a document."""
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="鏂囨。涓嶅瓨鍦?")
    data = document_store.load_compliance(doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="No compliance history")
    return data
