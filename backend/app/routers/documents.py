"""Document upload and processing routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, List, Optional, Set

import fitz
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import DocumentUploadResponse, OCRChunk, ProgressEvent
from app.services.baidu_ocr import baidu_ocr_gateway
from app.services.local_ocr import local_ocr_gateway
from app.services.parser import process_document, render_page_to_image
from app.services.rag_engine import rag_engine

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory runtime state.
# NOTE: existing modules import these globals, so keep names stable.
document_progress: Dict[str, ProgressEvent] = {}
documents: Dict[str, dict] = {}
document_locks: Dict[str, asyncio.Lock] = {}


def _sorted_unique_pages(pages: List[int]) -> List[int]:
    normalized: Set[int] = set()
    for page in pages:
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            continue
        normalized.add(page_num)
    return sorted(normalized)


def _get_or_create_doc_lock(doc_id: str) -> asyncio.Lock:
    lock = document_locks.get(doc_id)
    if lock is None:
        lock = asyncio.Lock()
        document_locks[doc_id] = lock
    return lock


def _compute_recognized_pages(doc: dict) -> List[int]:
    total_pages = int(doc.get("total_pages") or 0)
    existing = set(_sorted_unique_pages(doc.get("recognized_pages") or []))
    from_status: Set[int] = set()

    page_ocr_status = doc.get("page_ocr_status") or {}
    for page_raw, status in page_ocr_status.items():
        if status != "recognized":
            continue
        try:
            page_num = int(page_raw)
        except (TypeError, ValueError):
            continue
        if 1 <= page_num <= total_pages:
            from_status.add(page_num)

    merged = existing | from_status
    return sorted(page for page in merged if 1 <= page <= total_pages)


def _compute_unrecognized_pages(doc: dict) -> List[int]:
    total_pages = int(doc.get("total_pages") or 0)
    recognized = set(_compute_recognized_pages(doc))
    return [page for page in range(1, total_pages + 1) if page not in recognized]


def _sync_ocr_sets(doc: dict) -> None:
    doc["recognized_pages"] = _compute_recognized_pages(doc)
    doc["ocr_required_pages"] = _compute_unrecognized_pages(doc)


def get_consistent_recognized_pages(doc: dict) -> List[int]:
    _sync_ocr_sets(doc)
    return list(doc.get("recognized_pages") or [])


def _get_target_page(doc: dict, page_num: int):
    for page in doc.get("pages", []):
        if page.page_number == page_num:
            return page
    return None


def _log_ocr_state(doc_id: str, page_num: int, status: str, detail: Optional[str] = None) -> None:
    doc = documents.get(doc_id)
    if not doc:
        return
    recognized_count = len(doc.get("recognized_pages") or [])
    required_count = len(doc.get("ocr_required_pages") or [])
    if detail:
        message = (
            "ocr_state "
            f"doc_id={doc_id} page={page_num} status={status} "
            f"recognized_count={recognized_count} required_count={required_count} detail={detail}"
        )
    else:
        message = (
            "ocr_state "
            f"doc_id={doc_id} page={page_num} status={status} "
            f"recognized_count={recognized_count} required_count={required_count}"
        )
    print(message, flush=True)
    logger.info(message)


async def _run_ocr(
    image_base64: str,
    page_num: int,
    page_width: float,
    page_height: float,
    baidu_ocr_url: Optional[str],
    baidu_ocr_token: Optional[str],
) -> List[OCRChunk]:
    chunks: List[OCRChunk]

    if baidu_ocr_url and baidu_ocr_token:
        try:
            chunks = await baidu_ocr_gateway.process_image(
                image_base64,
                page_num,
                page_width,
                page_height,
                api_url=baidu_ocr_url,
                token=baidu_ocr_token,
            )
        except PermissionError:
            chunks = await local_ocr_gateway.process_image(
                image_base64,
                page_num,
                page_width,
                page_height,
            )
        except Exception:
            chunks = await local_ocr_gateway.process_image(
                image_base64,
                page_num,
                page_width,
                page_height,
            )
    else:
        chunks = await local_ocr_gateway.process_image(
            image_base64,
            page_num,
            page_width,
            page_height,
        )

    return chunks


async def recognize_document_page(doc_id: str, page_num: int, api_key: Optional[str] = None) -> dict:
    """Recognize a single page and index it."""
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found")

    lock = _get_or_create_doc_lock(doc_id)

    file_path = ""
    baidu_ocr_url: Optional[str] = None
    baidu_ocr_token: Optional[str] = None
    cached_image_base64: Optional[str] = None

    # Stage A: pre-check and mark page processing.
    async with lock:
        doc = documents.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")

        total_pages = int(doc.get("total_pages") or 0)
        if page_num < 1 or page_num > total_pages:
            raise HTTPException(status_code=400, detail="Page number out of range")

        _sync_ocr_sets(doc)
        page_ocr_status = doc.setdefault("page_ocr_status", {})
        current_status = page_ocr_status.get(page_num, "unrecognized")
        if current_status == "recognized" or page_num in set(doc.get("recognized_pages") or []):
            page_ocr_status[page_num] = "recognized"
            _sync_ocr_sets(doc)
            _log_ocr_state(doc_id, page_num, "already_recognized")
            return {
                "page": page_num,
                "chunks": [],
                "status": "already_recognized",
                "already_recognized": True,
                "message": "Page already recognized",
            }
        if current_status == "processing":
            _log_ocr_state(doc_id, page_num, "processing", "already in progress")
            return {
                "page": page_num,
                "chunks": [],
                "status": "processing",
                "already_recognized": False,
                "message": "Page OCR is already in progress",
            }

        target_page = _get_target_page(doc, page_num)
        if not target_page:
            page_ocr_status[page_num] = "failed"
            _sync_ocr_sets(doc)
            _log_ocr_state(doc_id, page_num, "failed", "page metadata not found")
            raise HTTPException(status_code=404, detail="Page metadata not found")

        page_ocr_status[page_num] = "processing"
        _sync_ocr_sets(doc)

        file_path = doc["file_path"]
        baidu_ocr_url = doc.get("baidu_ocr_url")
        baidu_ocr_token = doc.get("baidu_ocr_token")
        cached_image_base64 = target_page.image_base64

    try:
        # Stage B: heavy OCR + indexing (without lock).
        with fitz.open(file_path) as pdf_doc:
            page = pdf_doc[page_num - 1]
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            image_base64 = cached_image_base64 or render_page_to_image(page)

        if not image_base64:
            raise HTTPException(status_code=500, detail="Unable to render page image for OCR")

        chunks = await _run_ocr(
            image_base64=image_base64,
            page_num=page_num,
            page_width=page_width,
            page_height=page_height,
            baidu_ocr_url=baidu_ocr_url,
            baidu_ocr_token=baidu_ocr_token,
        )
        if not chunks:
            raise HTTPException(status_code=422, detail=f"OCR returned empty result for page {page_num}")

        indexed_count = await rag_engine.index_ocr_result(
            doc_id,
            page_num,
            [{"text": c.text, "bbox": c.bbox.model_dump()} for c in chunks],
            api_key=api_key,
        )
        if indexed_count == 0:
            raise HTTPException(status_code=422, detail=f"No indexable OCR chunks for page {page_num}")

        # Stage C: commit runtime state under lock.
        async with lock:
            doc = documents.get(doc_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")

            page_ocr_status = doc.setdefault("page_ocr_status", {})
            target_page = _get_target_page(doc, page_num)
            if not target_page:
                page_ocr_status[page_num] = "failed"
                _sync_ocr_sets(doc)
                _log_ocr_state(doc_id, page_num, "failed", "page metadata missing on commit")
                raise HTTPException(status_code=404, detail="Page metadata not found")

            target_page.image_base64 = image_base64
            target_page.type = "ocr"
            target_page.text = "\n".join([c.text for c in chunks])
            target_page.coordinates = [c.bbox for c in chunks if c.bbox]
            target_page.confidence = 0.9

            page_ocr_status[page_num] = "recognized"
            _sync_ocr_sets(doc)
            _log_ocr_state(doc_id, page_num, "recognized")

        return {
            "page": page_num,
            "chunks": chunks,
            "status": "recognized",
            "already_recognized": False,
            "message": "Page OCR completed",
        }
    except HTTPException as exc:
        async with lock:
            doc = documents.get(doc_id)
            if doc is not None:
                page_ocr_status = doc.setdefault("page_ocr_status", {})
                if page_ocr_status.get(page_num) != "recognized":
                    page_ocr_status[page_num] = "failed"
                _sync_ocr_sets(doc)
                _log_ocr_state(doc_id, page_num, "failed", str(exc.detail))
        raise
    except Exception as exc:
        logger.exception("Failed to recognize page %s of %s", page_num, doc_id)
        async with lock:
            doc = documents.get(doc_id)
            if doc is not None:
                page_ocr_status = doc.setdefault("page_ocr_status", {})
                if page_ocr_status.get(page_num) != "recognized":
                    page_ocr_status[page_num] = "failed"
                _sync_ocr_sets(doc)
                _log_ocr_state(doc_id, page_num, "failed", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def process_document_async(
    doc_id: str,
    file_path: str,
    filename: str,
    api_key: Optional[str] = None,
    ocr_model: str = "glm-4v-flash",
    ocr_provider: str = "baidu",
    baidu_ocr_url: Optional[str] = None,
    baidu_ocr_token: Optional[str] = None,
    ocr_mode: str = "manual",
):
    """Background document processor."""
    del ocr_model, ocr_provider  # Backward compatibility with API params.

    try:
        document_progress[doc_id] = ProgressEvent(
            stage="extracting",
            current=0,
            total=100,
            message="Parsing PDF...",
            document_id=doc_id,
        )

        pages, thumbnails = process_document(file_path)
        total_pages = len(pages)
        page_ocr_status = {page_num: "unrecognized" for page_num in range(1, total_pages + 1)}

        documents[doc_id] = {
            "id": doc_id,
            "name": filename,
            "total_pages": total_pages,
            "thumbnails": thumbnails,
            "file_path": file_path,
            "pages": pages,
            "recognized_pages": [],
            "ocr_required_pages": list(range(1, total_pages + 1)),
            "page_ocr_status": page_ocr_status,
            "ocr_mode": ocr_mode,
            "baidu_ocr_url": baidu_ocr_url,
            "baidu_ocr_token": baidu_ocr_token,
        }
        _get_or_create_doc_lock(doc_id)

        if ocr_mode == "full":
            for index in range(total_pages):
                page_num = index + 1
                current = 10 + int((index / max(total_pages, 1)) * 80)
                document_progress[doc_id] = ProgressEvent(
                    stage="ocr",
                    current=current,
                    total=100,
                    message=f"Recognizing pages ({page_num}/{total_pages})...",
                    document_id=doc_id,
                )
                try:
                    await recognize_document_page(doc_id, page_num, api_key=api_key)
                except HTTPException as exc:
                    logger.warning("OCR failed for %s page %s: %s", doc_id, page_num, exc.detail)

            doc = documents.get(doc_id) or {}
            _sync_ocr_sets(doc)
            recognized_count = len(doc.get("recognized_pages") or [])
            if recognized_count == 0 and total_pages > 0:
                document_progress[doc_id] = ProgressEvent(
                    stage="failed",
                    current=0,
                    total=100,
                    message="No pages were recognized. Check OCR configuration.",
                    document_id=doc_id,
                )
                return

            document_progress[doc_id] = ProgressEvent(
                stage="completed",
                current=100,
                total=100,
                message=f"Completed: recognized {recognized_count}/{total_pages} pages",
                document_id=doc_id,
            )
            return

        document_progress[doc_id] = ProgressEvent(
            stage="completed",
            current=100,
            total=100,
            message="Document loaded. Select pages to recognize.",
            document_id=doc_id,
        )
    except Exception as exc:
        logger.exception("Document processing failed for %s", doc_id)
        document_progress[doc_id] = ProgressEvent(
            stage="failed",
            current=0,
            total=100,
            message=str(exc),
            document_id=doc_id,
        )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    zhipu_api_key: Optional[str] = Form(None),
    ocr_model: str = Form("glm-4v-flash"),
    ocr_provider: str = Form("baidu"),
    baidu_ocr_url: Optional[str] = Form(None),
    baidu_ocr_token: Optional[str] = Form(None),
    ocr_mode: str = Form("manual"),
):
    """Upload PDF and start background processing."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    if ocr_mode not in {"manual", "full"}:
        raise HTTPException(status_code=400, detail="ocr_mode must be manual or full")

    doc_id = f"doc_{uuid.uuid4().hex[:12]}"
    document_locks[doc_id] = asyncio.Lock()

    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{doc_id}.pdf")

    content = await file.read()
    with open(file_path, "wb") as handle:
        handle.write(content)

    document_progress[doc_id] = ProgressEvent(
        stage="extracting",
        current=0,
        total=100,
        message="Starting processing...",
        document_id=doc_id,
    )

    if not baidu_ocr_url:
        baidu_ocr_url = os.getenv("BAIDU_OCR_API_URL")
    if not baidu_ocr_token:
        baidu_ocr_token = os.getenv("BAIDU_OCR_TOKEN")

    background_tasks.add_task(
        process_document_async,
        doc_id,
        file_path,
        file.filename,
        zhipu_api_key,
        ocr_model,
        ocr_provider,
        baidu_ocr_url,
        baidu_ocr_token,
        ocr_mode,
    )

    return DocumentUploadResponse(
        document_id=doc_id,
        status="processing",
        total_pages=0,
        ocr_required_pages=[],
        progress_url=f"/api/documents/{doc_id}/progress",
        ocr_mode=ocr_mode,
    )


@router.get("/{doc_id}/progress")
async def get_progress(doc_id: str):
    """SSE stream for document processing progress."""

    async def event_generator():
        while True:
            if doc_id not in document_progress:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "Document not found"}),
                }
                break

            progress = document_progress[doc_id]
            yield {
                "event": "progress",
                "data": json.dumps(progress.model_dump()),
            }

            if progress.stage in ["completed", "failed"]:
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """Get document metadata."""
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found")

    lock = _get_or_create_doc_lock(doc_id)
    async with lock:
        doc = documents.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        _sync_ocr_sets(doc)

        return {
            "id": doc["id"],
            "name": doc["name"],
            "total_pages": doc["total_pages"],
            "ocr_required_pages": doc["ocr_required_pages"],
            "recognized_pages": list(doc.get("recognized_pages") or []),
            "page_ocr_status": doc.get("page_ocr_status") or {},
            "ocr_mode": doc.get("ocr_mode", "manual"),
            "thumbnails": doc["thumbnails"],
        }


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """Delete a document and related runtime state."""
    if doc_id in documents:
        doc = documents[doc_id]
        if os.path.exists(doc["file_path"]):
            os.remove(doc["file_path"])
        rag_engine.delete_document(doc_id)
        del documents[doc_id]

    if doc_id in document_progress:
        del document_progress[doc_id]
    if doc_id in document_locks:
        del document_locks[doc_id]

    return {"status": "deleted"}


class ComplianceRequest(BaseModel):
    requirements: List[str]
    api_key: Optional[str] = None
    allowed_pages: Optional[List[int]] = None


@router.post("/{doc_id}/compliance")
async def check_compliance(doc_id: str, request: ComplianceRequest):
    """Compliance check endpoint."""
    from app.services.compliance_service import compliance_service

    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="Document not found")

    if request.allowed_pages is None:
        lock = _get_or_create_doc_lock(doc_id)
        async with lock:
            doc = documents.get(doc_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")
            allowed_pages = get_consistent_recognized_pages(doc)
    else:
        allowed_pages = _sorted_unique_pages(request.allowed_pages)

    try:
        return await compliance_service.verify_requirements(
            doc_id,
            request.requirements,
            api_key=request.api_key,
            allowed_pages=allowed_pages,
        )
    except Exception as exc:
        logger.exception("Compliance check failed for %s", doc_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
