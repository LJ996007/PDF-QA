"""Document upload and processing routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import fitz
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.models.schemas import DocumentUploadResponse, OCRChunk, ProgressEvent
from app.services.baidu_ocr import baidu_ocr_gateway
from app.services.document_store import document_store
from app.services.local_ocr import local_ocr_gateway
from app.services.parser import generate_thumbnail, get_ocr_required_pages, process_document, render_page_to_image
from app.services.rag_engine import rag_engine

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory runtime state.
document_progress: Dict[str, ProgressEvent] = {}
documents: Dict[str, dict] = {}
document_locks: Dict[str, asyncio.Lock] = {}
ocr_queue: "asyncio.Queue[OCRQueueJob]" = asyncio.Queue()
ocr_worker_task: Optional[asyncio.Task] = None
ocr_jobs_by_doc: Dict[str, Set[int]] = {}
ocr_cancel_flags: Set[str] = set()
ocr_queue_lock: Optional[asyncio.Lock] = None

KEEP_PDF = os.getenv("KEEP_PDF", "1").strip().lower() in {"1", "true", "yes", "y"}
VALID_OCR_STATUS = {"unrecognized", "processing", "recognized", "failed"}


@dataclass
class OCRQueueJob:
    doc_id: str
    pages: List[int]
    api_key: Optional[str] = None
    source: str = "manual"


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_abspath(path: str) -> str:
    return os.path.abspath(path)


def _is_allowed_pdf_path(path: str) -> bool:
    if not path:
        return False
    abs_path = _safe_abspath(path)
    uploads_dir = _safe_abspath("uploads")
    doc_store_dir = _safe_abspath("doc_store")
    for base in (uploads_dir, doc_store_dir):
        if abs_path == base:
            return True
        if abs_path.startswith(base + os.sep):
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


def _render_thumbnails_from_pdf(file_path: str) -> List[str]:
    if not file_path or not os.path.exists(file_path):
        return []
    thumbnails: List[str] = []
    with fitz.open(file_path) as pdf_doc:
        for page in pdf_doc:
            thumbnail = generate_thumbnail(page)
            thumbnails.append(f"data:image/webp;base64,{thumbnail}")
    return thumbnails


def _sorted_unique_pages(raw_pages: Any) -> List[int]:
    pages: Set[int] = set()
    if not isinstance(raw_pages, list):
        return []
    for page in raw_pages:
        try:
            page_num = int(page)
        except (TypeError, ValueError):
            continue
        pages.add(page_num)
    return sorted(pages)


def _coerce_page_status_map(raw_map: Any) -> Dict[int, str]:
    if not isinstance(raw_map, dict):
        return {}
    output: Dict[int, str] = {}
    for page_raw, status_raw in raw_map.items():
        try:
            page_num = int(page_raw)
        except (TypeError, ValueError):
            continue
        status = str(status_raw or "").strip()
        if status in VALID_OCR_STATUS:
            output[page_num] = status
    return output


def _get_or_create_doc_lock(doc_id: str) -> asyncio.Lock:
    lock = document_locks.get(doc_id)
    if lock is None:
        lock = asyncio.Lock()
        document_locks[doc_id] = lock
    return lock


def _get_ocr_queue_lock() -> asyncio.Lock:
    global ocr_queue_lock
    if ocr_queue_lock is None:
        ocr_queue_lock = asyncio.Lock()
    return ocr_queue_lock


def _ensure_status_map(doc: dict) -> Dict[int, str]:
    total_pages = int(doc.get("total_pages") or 0)
    status_map = _coerce_page_status_map(doc.get("page_ocr_status"))
    recognized = set(_sorted_unique_pages(doc.get("recognized_pages") or []))
    required = set(_sorted_unique_pages(doc.get("ocr_required_pages") or []))

    for page_num in range(1, total_pages + 1):
        if page_num in status_map:
            continue
        if page_num in recognized:
            status_map[page_num] = "recognized"
            continue
        if required:
            status_map[page_num] = "unrecognized" if page_num in required else "recognized"
            continue
        status_map[page_num] = "unrecognized"

    doc["page_ocr_status"] = status_map
    return status_map


def _compute_recognized_pages(doc: dict) -> List[int]:
    total_pages = int(doc.get("total_pages") or 0)
    status_map = _ensure_status_map(doc)
    pages = set(_sorted_unique_pages(doc.get("recognized_pages") or []))
    for page_num, status in status_map.items():
        if status == "recognized":
            pages.add(page_num)
    return sorted(page for page in pages if 1 <= page <= total_pages)


def _compute_unrecognized_pages(doc: dict) -> List[int]:
    total_pages = int(doc.get("total_pages") or 0)
    status_map = _ensure_status_map(doc)
    return [page for page in range(1, total_pages + 1) if status_map.get(page) != "recognized"]


def _sync_ocr_sets(doc: dict) -> None:
    _ensure_status_map(doc)
    doc["recognized_pages"] = _compute_recognized_pages(doc)
    doc["ocr_required_pages"] = _compute_unrecognized_pages(doc)


def get_consistent_recognized_pages(doc: dict) -> List[int]:
    _sync_ocr_sets(doc)
    return list(doc.get("recognized_pages") or [])


def _persist_doc_meta(doc_id: str, status: Optional[str] = None) -> None:
    doc = documents.get(doc_id)
    if doc is None:
        return

    _sync_ocr_sets(doc)
    existing = document_store.get_by_doc_id(doc_id) or {}

    keep_pdf = bool(doc.get("keep_pdf"))
    file_path = doc.get("file_path")
    if not keep_pdf:
        file_path = None

    initial_ocr_required_pages = doc.get("initial_ocr_required_pages")
    if initial_ocr_required_pages is None:
        initial_ocr_required_pages = existing.get("initial_ocr_required_pages") or []

    payload = {
        "doc_id": doc_id,
        "sha256": doc.get("sha256") or existing.get("sha256") or "",
        "filename": doc.get("name") or existing.get("filename") or doc_id,
        "created_at": doc.get("created_at") or existing.get("created_at") or _now_iso_utc(),
        "status": status or existing.get("status") or "completed",
        "total_pages": int(doc.get("total_pages") or existing.get("total_pages") or 0),
        "initial_ocr_required_pages": _sorted_unique_pages(initial_ocr_required_pages),
        "ocr_required_pages": list(doc.get("ocr_required_pages") or []),
        "recognized_pages": list(doc.get("recognized_pages") or []),
        "page_ocr_status": doc.get("page_ocr_status") or {},
        "ocr_mode": doc.get("ocr_mode") or existing.get("ocr_mode") or "manual",
        "thumbnails": list(doc.get("thumbnails") or existing.get("thumbnails") or []),
        "chunk_count": int(doc.get("chunk_count") or existing.get("chunk_count") or 0),
        "keep_pdf": keep_pdf,
        "pdf_path": file_path or existing.get("pdf_path"),
    }
    document_store.upsert_doc(payload)


def _ensure_doc_thumbnails(doc_id: str, doc: dict) -> None:
    total_pages = int(doc.get("total_pages") or 0)
    thumbnails = list(doc.get("thumbnails") or [])
    if total_pages <= 0:
        return
    if len(thumbnails) >= total_pages:
        return

    file_path = doc.get("file_path") or ""
    if not file_path or not os.path.exists(file_path):
        logger.info("Skip thumbnail backfill for %s: no readable PDF file", doc_id)
        return

    try:
        regenerated = _render_thumbnails_from_pdf(file_path)
        if len(regenerated) >= total_pages:
            doc["thumbnails"] = regenerated
            _persist_doc_meta(doc_id)
        else:
            logger.warning(
                "Thumbnail backfill incomplete for %s: expected %s pages, got %s",
                doc_id,
                total_pages,
                len(regenerated),
            )
    except Exception:
        logger.exception("Failed to backfill thumbnails for %s", doc_id)


def _extract_recognized_pages_from_ocr_payload(doc_id: str) -> Set[int]:
    payload = document_store.load_ocr_result(doc_id)
    if not isinstance(payload, dict):
        return set()
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return set()

    recognized: Set[int] = set()
    for item in pages:
        if not isinstance(item, dict):
            continue
        try:
            page_num = int(item.get("page_number"))
        except (TypeError, ValueError):
            continue
        if page_num <= 0:
            continue
        chunks = item.get("chunks")
        if isinstance(chunks, list) and chunks:
            recognized.add(page_num)
    return recognized


def _load_doc_meta_into_memory(meta: dict) -> None:
    doc_id = str(meta.get("doc_id") or "").strip()
    if not doc_id:
        return

    total_pages = int(meta.get("total_pages") or 0)
    recognized_from_meta = set(_sorted_unique_pages(meta.get("recognized_pages") or []))
    required_from_meta = set(_sorted_unique_pages(meta.get("ocr_required_pages") or []))
    recognized_from_payload = _extract_recognized_pages_from_ocr_payload(doc_id)
    status_map = _coerce_page_status_map(meta.get("page_ocr_status"))
    initial_required_from_meta = meta.get("initial_ocr_required_pages")
    if isinstance(initial_required_from_meta, list):
        initial_required_pages = set(_sorted_unique_pages(initial_required_from_meta))
    else:
        # Backfill baseline for legacy records that do not have initial_ocr_required_pages.
        initial_required_pages = set(required_from_meta)
        initial_required_pages.update(recognized_from_payload)
        initial_required_pages.update(
            page_num
            for page_num, page_status in status_map.items()
            if page_status in {"unrecognized", "processing", "failed"}
        )

    recognized = recognized_from_meta | recognized_from_payload

    for page_num in range(1, total_pages + 1):
        if page_num in status_map:
            continue
        if page_num in recognized:
            status_map[page_num] = "recognized"
            continue
        if required_from_meta:
            status_map[page_num] = "unrecognized" if page_num in required_from_meta else "recognized"
            continue
        status_map[page_num] = "unrecognized"

    doc = {
        "id": doc_id,
        "name": meta.get("filename") or meta.get("name") or doc_id,
        "sha256": meta.get("sha256") or "",
        "created_at": meta.get("created_at") or _now_iso_utc(),
        "total_pages": total_pages,
        "initial_ocr_required_pages": sorted(initial_required_pages),
        "recognized_pages": sorted(recognized),
        "ocr_required_pages": sorted(required_from_meta),
        "page_ocr_status": status_map,
        "ocr_mode": meta.get("ocr_mode") or "manual",
        "thumbnails": list(meta.get("thumbnails") or []),
        "file_path": meta.get("pdf_path"),
        "keep_pdf": bool(meta.get("keep_pdf")),
        "chunk_count": int(meta.get("chunk_count") or 0),
        "pages": [],
        "baidu_ocr_url": None,
        "baidu_ocr_token": None,
    }
    _sync_ocr_sets(doc)
    documents[doc_id] = doc
    _get_or_create_doc_lock(doc_id)


def load_persisted_documents() -> None:
    for meta in document_store.list_docs():
        if meta.get("status") == "completed":
            _load_doc_meta_into_memory(meta)


def ensure_document_loaded(doc_id: str) -> bool:
    if doc_id in documents:
        return True
    meta = document_store.get_by_doc_id(doc_id)
    if meta and meta.get("status") in {"completed", "processing"}:
        _load_doc_meta_into_memory(meta)
        return True
    return False


def _get_target_page(doc: dict, page_num: int):
    for page in doc.get("pages") or []:
        if getattr(page, "page_number", None) == page_num:
            return page
    return None


def _load_or_init_ocr_payload(doc_id: str, sha256: str) -> dict:
    payload = document_store.load_ocr_result(doc_id)
    if not isinstance(payload, dict):
        payload = {"doc_id": doc_id, "sha256": sha256, "pages": []}
    payload.setdefault("doc_id", doc_id)
    payload.setdefault("sha256", sha256)
    pages = payload.get("pages")
    if not isinstance(pages, list):
        payload["pages"] = []
    return payload


def _save_ocr_page_result(
    doc_id: str,
    sha256: str,
    page_num: int,
    provider: str,
    chunks: List[OCRChunk],
) -> None:
    payload = _load_or_init_ocr_payload(doc_id, sha256)
    pages = payload.get("pages") or []

    page_payload = {
        "page_number": page_num,
        "provider": provider,
        "chunks": [{"text": c.text, "bbox": c.bbox.model_dump() if c.bbox else None} for c in chunks],
        "merged_text": "\n".join(c.text for c in chunks).strip(),
    }

    replaced = False
    for idx, item in enumerate(pages):
        if not isinstance(item, dict):
            continue
        try:
            item_page = int(item.get("page_number"))
        except (TypeError, ValueError):
            continue
        if item_page == page_num:
            pages[idx] = page_payload
            replaced = True
            break

    if not replaced:
        pages.append(page_payload)

    payload["pages"] = sorted(
        [p for p in pages if isinstance(p, dict)],
        key=lambda p: int(p.get("page_number") or 0),
    )
    document_store.save_ocr_result(doc_id, payload)


def _clear_page_ocr_chunks(doc_id: str, page_num: int) -> None:
    try:
        rag_engine.collection.delete(where={"doc_id": doc_id, "page": page_num, "source": "ocr"})
    except Exception:
        # Best effort only.
        pass


def _normalize_ocr_mode(mode: str) -> str:
    return "full" if mode == "full" else "manual"


def _set_doc_progress(
    doc_id: str,
    stage: str,
    current: int,
    message: str,
    total: int = 100,
) -> None:
    document_progress[doc_id] = ProgressEvent(
        stage=stage,
        current=max(0, min(current, total)),
        total=total,
        message=message,
        document_id=doc_id,
    )


async def _release_queued_pages(doc_id: str, pages: List[int]) -> None:
    if not pages:
        return
    lock = _get_ocr_queue_lock()
    async with lock:
        pending = ocr_jobs_by_doc.get(doc_id)
        if not pending:
            return
        for page in pages:
            pending.discard(page)
        if not pending:
            ocr_jobs_by_doc.pop(doc_id, None)


async def _has_pending_queued_pages(doc_id: str) -> bool:
    lock = _get_ocr_queue_lock()
    async with lock:
        return bool(ocr_jobs_by_doc.get(doc_id))


def _cleanup_temp_pdf_if_needed(doc_id: str) -> None:
    doc = documents.get(doc_id)
    if not doc:
        return
    if bool(doc.get("keep_pdf")):
        return

    file_path = doc.get("file_path")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as exc:
            logger.warning("Failed to remove temporary PDF for %s: %s", doc_id, str(exc))
            return

    doc["file_path"] = None
    document_store.upsert_doc(
        {
            "doc_id": doc_id,
            "keep_pdf": False,
            "pdf_path": None,
        }
    )
    _persist_doc_meta(
        doc_id,
        status=(document_store.get_by_doc_id(doc_id) or {}).get("status", "completed"),
    )


async def _finalize_doc_after_ocr_queue(doc_id: str) -> None:
    lock = _get_ocr_queue_lock()
    async with lock:
        pending = bool(ocr_jobs_by_doc.get(doc_id))
    if pending:
        return
    _cleanup_temp_pdf_if_needed(doc_id)


async def enqueue_ocr_job(
    doc_id: str,
    pages: List[int],
    api_key: Optional[str] = None,
    source: str = "manual",
) -> List[int]:
    await start_ocr_worker()

    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    doc = documents.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="该文档未保存 PDF，无法进行 OCR")

    total_pages = int(doc.get("total_pages") or 0)
    requested = _sorted_unique_pages(pages)
    valid_pages = [page for page in requested if 1 <= page <= total_pages]
    if not valid_pages:
        return []

    queued_pages: List[int] = []
    queue_lock = _get_ocr_queue_lock()
    async with queue_lock:
        ocr_cancel_flags.discard(doc_id)
        pending = ocr_jobs_by_doc.setdefault(doc_id, set())
        status_map = _ensure_status_map(doc)

        for page in valid_pages:
            status = status_map.get(page, "unrecognized")
            if status in {"recognized", "processing"}:
                continue
            if page in pending:
                continue
            pending.add(page)
            queued_pages.append(page)

    if queued_pages:
        await ocr_queue.put(OCRQueueJob(doc_id=doc_id, pages=queued_pages, api_key=api_key, source=source))

    if queued_pages:
        _set_doc_progress(
            doc_id,
            stage="ocr",
            current=0,
            message=f"已加入后台 OCR 队列（{len(queued_pages)} 页）",
        )
    return queued_pages


async def run_ocr_worker() -> None:
    while True:
        job = await ocr_queue.get()
        try:
            doc_id = job.doc_id
            pages = list(job.pages or [])
            if not pages:
                continue

            if not ensure_document_loaded(doc_id):
                await _release_queued_pages(doc_id, pages)
                continue

            if doc_id in ocr_cancel_flags:
                await _release_queued_pages(doc_id, pages)
                if not await _has_pending_queued_pages(doc_id):
                    _set_doc_progress(doc_id, stage="completed", current=100, message="OCR 任务已取消")
                    await _finalize_doc_after_ocr_queue(doc_id)
                continue

            total = len(pages)
            failures = 0
            processed_pages: List[int] = []
            canceled = False

            for idx, page_num in enumerate(pages, start=1):
                if doc_id in ocr_cancel_flags:
                    canceled = True
                    break

                _set_doc_progress(
                    doc_id,
                    stage="ocr",
                    current=int((idx - 1) / max(total, 1) * 100),
                    message=f"后台识别中（{idx}/{total}）...",
                )
                try:
                    await recognize_document_page(doc_id, page_num, api_key=job.api_key)
                except Exception as exc:
                    failures += 1
                    logger.warning("Failed to recognize page %s of %s: %s", page_num, doc_id, str(exc))
                finally:
                    processed_pages.append(page_num)
                    await _release_queued_pages(doc_id, [page_num])

            if canceled:
                remaining = [page for page in pages if page not in set(processed_pages)]
                await _release_queued_pages(doc_id, remaining)
                if not await _has_pending_queued_pages(doc_id):
                    _set_doc_progress(doc_id, stage="completed", current=100, message="OCR 任务已取消")
                    await _finalize_doc_after_ocr_queue(doc_id)
                continue

            doc_local = documents.get(doc_id)
            if doc_local:
                _sync_ocr_sets(doc_local)
                _persist_doc_meta(doc_id, status="completed")

            if await _has_pending_queued_pages(doc_id):
                _set_doc_progress(
                    doc_id,
                    stage="ocr",
                    current=0,
                    message="OCR 队列中仍有待处理页面",
                )
            else:
                done_message = f"后台 OCR 完成：{total - failures}/{total} 页"
                if failures:
                    done_message += f"，失败 {failures} 页"
                _set_doc_progress(doc_id, stage="completed", current=100, message=done_message)
                await _finalize_doc_after_ocr_queue(doc_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error in OCR worker")
        finally:
            ocr_queue.task_done()


async def start_ocr_worker() -> None:
    global ocr_worker_task
    if ocr_worker_task is None or ocr_worker_task.done():
        ocr_worker_task = asyncio.create_task(run_ocr_worker(), name="ocr-worker")


async def stop_ocr_worker() -> None:
    global ocr_worker_task
    if ocr_worker_task is None:
        return
    ocr_worker_task.cancel()
    try:
        await ocr_worker_task
    except asyncio.CancelledError:
        pass
    finally:
        ocr_worker_task = None


async def _run_ocr(
    image_base64: str,
    page_num: int,
    page_width: float,
    page_height: float,
    baidu_ocr_url: Optional[str],
    baidu_ocr_token: Optional[str],
) -> Tuple[List[OCRChunk], str]:
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
            if chunks:
                return chunks, "baidu"
        except PermissionError:
            pass
        except Exception:
            pass

    chunks = await local_ocr_gateway.process_image(
        image_base64,
        page_num,
        page_width,
        page_height,
    )
    return chunks, "local"


def _extract_page_image_and_size(
    file_path: str,
    page_num: int,
    cached_image_base64: Optional[str] = None,
) -> Tuple[str, float, float]:
    with fitz.open(file_path) as pdf_doc:
        total = len(pdf_doc)
        if page_num < 1 or page_num > total:
            raise HTTPException(status_code=400, detail="页码超出范围")
        page = pdf_doc[page_num - 1]
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        image_base64 = cached_image_base64 or render_page_to_image(page)
    if not image_base64:
        raise HTTPException(status_code=500, detail=f"无法为第 {page_num} 页生成OCR图像")
    return image_base64, page_width, page_height


async def recognize_document_page(doc_id: str, page_num: int, api_key: Optional[str] = None) -> dict:
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    lock = _get_or_create_doc_lock(doc_id)

    file_path = ""
    baidu_ocr_url: Optional[str] = None
    baidu_ocr_token: Optional[str] = None
    cached_image_base64: Optional[str] = None
    sha256 = ""

    async with lock:
        doc = documents.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="文档不存在")

        total_pages = int(doc.get("total_pages") or 0)
        if page_num < 1 or page_num > total_pages:
            raise HTTPException(status_code=400, detail="页码超出范围")

        status_map = _ensure_status_map(doc)
        current_status = status_map.get(page_num, "unrecognized")
        if current_status == "recognized":
            return {
                "page": page_num,
                "chunks": [],
                "status": "already_recognized",
                "already_recognized": True,
                "message": "页面已识别",
            }
        if current_status == "processing":
            return {
                "page": page_num,
                "chunks": [],
                "status": "processing",
                "already_recognized": False,
                "message": "页面 OCR 正在进行中",
            }

        file_path = doc.get("file_path") or ""
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=400, detail="该文档未保存 PDF，无法进行 OCR")

        target_page = _get_target_page(doc, page_num)
        cached_image_base64 = getattr(target_page, "image_base64", None) if target_page else None
        baidu_ocr_url = doc.get("baidu_ocr_url") or os.getenv("BAIDU_OCR_API_URL")
        baidu_ocr_token = doc.get("baidu_ocr_token") or os.getenv("BAIDU_OCR_TOKEN")
        sha256 = str(doc.get("sha256") or "")

        status_map[page_num] = "processing"
        doc["page_ocr_status"] = status_map
        _sync_ocr_sets(doc)
        _persist_doc_meta(doc_id, status="completed")

    try:
        image_base64, page_width, page_height = _extract_page_image_and_size(
            file_path=file_path,
            page_num=page_num,
            cached_image_base64=cached_image_base64,
        )

        chunks, provider = await _run_ocr(
            image_base64=image_base64,
            page_num=page_num,
            page_width=page_width,
            page_height=page_height,
            baidu_ocr_url=baidu_ocr_url,
            baidu_ocr_token=baidu_ocr_token,
        )
        if not chunks:
            raise HTTPException(status_code=422, detail=f"第 {page_num} 页OCR结果为空")

        _clear_page_ocr_chunks(doc_id, page_num)
        indexed_count = await rag_engine.index_ocr_result(
            doc_id,
            page_num,
            [{"text": c.text, "bbox": c.bbox.model_dump()} for c in chunks],
            api_key=api_key,
        )
        if indexed_count <= 0:
            raise HTTPException(status_code=422, detail=f"第 {page_num} 页未生成可索引文本")

        async with lock:
            doc = documents.get(doc_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="文档不存在")

            status_map = _ensure_status_map(doc)
            status_map[page_num] = "recognized"
            doc["page_ocr_status"] = status_map
            doc["chunk_count"] = int(doc.get("chunk_count") or 0) + int(indexed_count)

            target_page = _get_target_page(doc, page_num)
            if target_page:
                target_page.image_base64 = image_base64
                target_page.type = "ocr"
                target_page.text = "\n".join(c.text for c in chunks).strip()
                target_page.coordinates = [c.bbox for c in chunks if c.bbox]
                target_page.confidence = 0.9

            _sync_ocr_sets(doc)
            _save_ocr_page_result(
                doc_id=doc_id,
                sha256=sha256,
                page_num=page_num,
                provider=provider,
                chunks=chunks,
            )
            _persist_doc_meta(doc_id, status="completed")

        return {
            "page": page_num,
            "chunks": chunks,
            "status": "recognized",
            "already_recognized": False,
            "indexed_count": int(indexed_count),
            "message": "页面 OCR 完成",
        }
    except HTTPException as exc:
        async with lock:
            doc = documents.get(doc_id)
            if doc is not None:
                status_map = _ensure_status_map(doc)
                if status_map.get(page_num) != "recognized":
                    status_map[page_num] = "failed"
                doc["page_ocr_status"] = status_map
                _sync_ocr_sets(doc)
                _persist_doc_meta(doc_id, status="completed")
        raise exc
    except Exception as exc:
        logger.exception("Failed to recognize page %s of %s", page_num, doc_id)
        async with lock:
            doc = documents.get(doc_id)
            if doc is not None:
                status_map = _ensure_status_map(doc)
                if status_map.get(page_num) != "recognized":
                    status_map[page_num] = "failed"
                doc["page_ocr_status"] = status_map
                _sync_ocr_sets(doc)
                _persist_doc_meta(doc_id, status="completed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def process_document_async(
    doc_id: str,
    file_path: str,
    filename: str,
    sha256: str,
    created_at: str,
    api_key: Optional[str] = None,
    ocr_model: str = "glm-4v-flash",
    ocr_provider: str = "baidu",
    baidu_ocr_url: Optional[str] = None,
    baidu_ocr_token: Optional[str] = None,
    ocr_mode: str = "manual",
    keep_pdf: bool = KEEP_PDF,
):
    del ocr_model, ocr_provider

    ocr_mode = _normalize_ocr_mode(ocr_mode)
    # Manual selective OCR requires PDF to remain available.
    effective_keep_pdf = bool(keep_pdf or ocr_mode == "manual")

    try:
        document_progress[doc_id] = ProgressEvent(
            stage="extracting",
            current=0,
            total=100,
            message="正在解析PDF...",
            document_id=doc_id,
        )

        pages, thumbnails = process_document(file_path)
        total_pages = len(pages)
        ocr_required = get_ocr_required_pages(pages)
        required_set = set(ocr_required)
        recognized_pages = [p.page_number for p in pages if p.page_number not in required_set]
        page_ocr_status = {
            p.page_number: ("unrecognized" if p.page_number in required_set else "recognized")
            for p in pages
        }

        doc = {
            "id": doc_id,
            "name": filename,
            "sha256": sha256,
            "created_at": created_at,
            "total_pages": total_pages,
            "initial_ocr_required_pages": list(ocr_required),
            "recognized_pages": recognized_pages,
            "ocr_required_pages": ocr_required,
            "page_ocr_status": page_ocr_status,
            "ocr_mode": ocr_mode,
            "thumbnails": thumbnails,
            "file_path": file_path,
            "keep_pdf": effective_keep_pdf,
            "chunk_count": 0,
            "pages": pages,
            "baidu_ocr_url": baidu_ocr_url,
            "baidu_ocr_token": baidu_ocr_token,
        }
        _sync_ocr_sets(doc)
        documents[doc_id] = doc
        _get_or_create_doc_lock(doc_id)

        document_store.upsert_doc(
            {
                "doc_id": doc_id,
                "sha256": sha256,
                "filename": filename,
                "created_at": created_at,
                "status": "processing",
                "total_pages": total_pages,
                "initial_ocr_required_pages": list(ocr_required),
                "ocr_required_pages": list(doc.get("ocr_required_pages") or []),
                "recognized_pages": list(doc.get("recognized_pages") or []),
                "page_ocr_status": doc.get("page_ocr_status") or {},
                "ocr_mode": ocr_mode,
                "thumbnails": list(thumbnails),
                "chunk_count": 0,
                "keep_pdf": effective_keep_pdf,
                "pdf_path": file_path if effective_keep_pdf else None,
            }
        )

        document_progress[doc_id] = ProgressEvent(
            stage="embedding",
            current=40,
            total=100,
            message="正在建立向量索引...",
            document_id=doc_id,
        )
        native_chunk_count = await rag_engine.index_document(doc_id, pages, api_key)
        doc["chunk_count"] = int(native_chunk_count)
        _persist_doc_meta(doc_id, status="processing")

        if ocr_mode == "full":
            pages_to_recognize = list(doc.get("ocr_required_pages") or [])
            if not pages_to_recognize:
                _persist_doc_meta(doc_id, status="completed")
                _set_doc_progress(
                    doc_id,
                    stage="completed",
                    current=100,
                    message=f"处理完成：已识别 {len(doc.get('recognized_pages') or [])}/{total_pages} 页",
                )
                if not effective_keep_pdf:
                    _cleanup_temp_pdf_if_needed(doc_id)
                return

            queued_pages = await enqueue_ocr_job(
                doc_id=doc_id,
                pages=pages_to_recognize,
                api_key=api_key,
                source="upload_full",
            )
            if not queued_pages:
                _persist_doc_meta(doc_id, status="completed")
                _set_doc_progress(
                    doc_id,
                    stage="completed",
                    current=100,
                    message=f"处理完成：已识别 {len(doc.get('recognized_pages') or [])}/{total_pages} 页",
                )
                if not effective_keep_pdf:
                    _cleanup_temp_pdf_if_needed(doc_id)
                return

            _persist_doc_meta(doc_id, status="processing")
            _set_doc_progress(
                doc_id,
                stage="ocr",
                current=45,
                message=f"已加入后台 OCR 队列（{len(queued_pages)} 页）",
            )
            return

        _persist_doc_meta(doc_id, status="completed")
        document_progress[doc_id] = ProgressEvent(
            stage="completed",
            current=100,
            total=100,
            message="文档已加载，可在网格视图选择页面进行 OCR。",
            document_id=doc_id,
        )

        if not effective_keep_pdf:
            _cleanup_temp_pdf_if_needed(doc_id)
    except Exception as exc:
        logger.exception("Document processing failed for %s", doc_id)
        try:
            document_store.upsert_doc(
                {
                    "doc_id": doc_id,
                    "sha256": sha256,
                    "filename": filename,
                    "created_at": created_at,
                    "status": "failed",
                    "keep_pdf": bool(keep_pdf),
                    "pdf_path": file_path if keep_pdf else None,
                }
            )
        except Exception:
            pass
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
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    ocr_mode = _normalize_ocr_mode(ocr_mode)
    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    existing = document_store.get_by_sha256(sha256)
    if existing and existing.get("status") == "completed":
        doc_id = existing.get("doc_id")
        if doc_id:
            ensure_document_loaded(doc_id)
            doc = documents.get(doc_id) or {}
            _sync_ocr_sets(doc)
            document_progress[doc_id] = ProgressEvent(
                stage="completed",
                current=100,
                total=100,
                message="已完成（命中缓存）",
                document_id=doc_id,
            )
            return DocumentUploadResponse(
                document_id=doc_id,
                status="completed",
                total_pages=int(doc.get("total_pages") or existing.get("total_pages") or 0),
                ocr_required_pages=list(doc.get("ocr_required_pages") or existing.get("ocr_required_pages") or []),
                progress_url=f"/api/documents/{doc_id}/progress",
                ocr_mode=doc.get("ocr_mode") or existing.get("ocr_mode") or ocr_mode,
            )

    doc_id = (existing.get("doc_id") if existing else None) or f"doc_{uuid.uuid4().hex[:12]}"
    created_at = (existing.get("created_at") if existing else None) or _now_iso_utc()
    effective_keep_pdf = bool(KEEP_PDF or ocr_mode == "manual")

    upload_dir = Path("uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(upload_dir / f"{doc_id}.pdf")
    with open(file_path, "wb") as handle:
        handle.write(content)

    document_store.upsert_doc(
        {
            "doc_id": doc_id,
            "sha256": sha256,
            "filename": file.filename,
            "created_at": created_at,
            "status": "processing",
            "chunk_count": int(existing.get("chunk_count") or 0) if existing else 0,
            "ocr_mode": ocr_mode,
            "keep_pdf": effective_keep_pdf,
            "pdf_path": file_path if effective_keep_pdf else None,
        }
    )

    document_progress[doc_id] = ProgressEvent(
        stage="extracting",
        current=0,
        total=100,
        message="开始处理...",
        document_id=doc_id,
    )
    _get_or_create_doc_lock(doc_id)

    if not baidu_ocr_url:
        baidu_ocr_url = os.getenv("BAIDU_OCR_API_URL")
    if not baidu_ocr_token:
        baidu_ocr_token = os.getenv("BAIDU_OCR_TOKEN")

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
        ocr_mode,
        effective_keep_pdf,
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
    async def event_generator():
        while True:
            if doc_id not in document_progress:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "文档不存在"}),
                }
                break

            progress = document_progress[doc_id]
            yield {
                "event": "progress",
                "data": json.dumps(progress.model_dump()),
            }

            if progress.stage in {"completed", "failed"}:
                break

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/history")
async def get_history():
    items = []
    for meta in document_store.list_docs():
        pdf_path = meta.get("pdf_path")
        has_pdf = bool(pdf_path and os.path.exists(str(pdf_path)))
        items.append(
            {
                "doc_id": meta.get("doc_id"),
                "filename": meta.get("filename"),
                "created_at": meta.get("created_at"),
                "total_pages": int(meta.get("total_pages") or 0),
                "ocr_required_pages": list(meta.get("ocr_required_pages") or []),
                "sha256": meta.get("sha256"),
                "status": meta.get("status"),
                "keep_pdf": bool(meta.get("keep_pdf")),
                "has_pdf": has_pdf,
                "ocr_mode": meta.get("ocr_mode") or "manual",
            }
        )
    return items


class LookupRequest(BaseModel):
    sha256: str


@router.post("/lookup")
async def lookup_document(request: LookupRequest):
    sha256 = (request.sha256 or "").strip().lower()
    meta = document_store.get_by_sha256(sha256)
    if meta and meta.get("doc_id"):
        return {"exists": True, "doc_id": meta.get("doc_id"), "status": meta.get("status")}
    return {"exists": False}


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    lock = _get_or_create_doc_lock(doc_id)
    async with lock:
        doc = documents.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        _ensure_doc_thumbnails(doc_id, doc)
        _sync_ocr_sets(doc)
        _persist_doc_meta(doc_id, status=(document_store.get_by_doc_id(doc_id) or {}).get("status", "completed"))

        return {
            "id": doc["id"],
            "name": doc["name"],
            "total_pages": int(doc.get("total_pages") or 0),
            "initial_ocr_required_pages": list(doc.get("initial_ocr_required_pages") or []),
            "ocr_required_pages": list(doc.get("ocr_required_pages") or []),
            "recognized_pages": list(doc.get("recognized_pages") or []),
            "page_ocr_status": doc.get("page_ocr_status") or {},
            "ocr_mode": doc.get("ocr_mode") or "manual",
            "thumbnails": list(doc.get("thumbnails") or []),
        }


@router.get("/{doc_id}/pdf")
async def get_document_pdf(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    path = _resolve_pdf_path(doc_id)
    if not path:
        raise HTTPException(status_code=404, detail="该文档未保存 PDF")
    return FileResponse(path, media_type="application/pdf", filename=f"{doc_id}.pdf")


@router.head("/{doc_id}/pdf")
async def head_document_pdf(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    path = _resolve_pdf_path(doc_id)
    if not path:
        raise HTTPException(status_code=404, detail="该文档未保存 PDF")
    return Response(status_code=200)


@router.post("/{doc_id}/attach_pdf")
async def attach_document_pdf(doc_id: str, file: UploadFile = File(...)):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    meta = document_store.get_by_doc_id(doc_id) or {}
    expected_sha = str(meta.get("sha256") or "").strip().lower()
    if expected_sha and expected_sha != sha256:
        raise HTTPException(status_code=400, detail="所选 PDF 与记录不匹配（SHA256 不同）")

    upload_dir = Path("uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(upload_dir / f"{doc_id}.pdf")
    with open(file_path, "wb") as handle:
        handle.write(content)

    document_store.upsert_doc(
        {
            "doc_id": doc_id,
            "sha256": sha256,
            "keep_pdf": True,
            "pdf_path": file_path,
        }
    )

    if doc_id in documents:
        documents[doc_id]["file_path"] = file_path
        documents[doc_id]["keep_pdf"] = True
        if not documents[doc_id].get("sha256"):
            documents[doc_id]["sha256"] = sha256
        _persist_doc_meta(doc_id, status=(meta.get("status") or "completed"))

    return {"status": "ok"}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    ensure_document_loaded(doc_id)

    queue_lock = _get_ocr_queue_lock()
    async with queue_lock:
        ocr_cancel_flags.add(doc_id)
        ocr_jobs_by_doc.pop(doc_id, None)

    doc = documents.get(doc_id)
    if doc:
        file_path = doc.get("file_path")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    rag_engine.delete_document(doc_id)
    document_store.delete_doc(doc_id)
    document_store.delete_chat(doc_id)
    document_store.delete_compliance(doc_id)

    documents.pop(doc_id, None)
    document_progress.pop(doc_id, None)
    document_locks.pop(doc_id, None)

    return {"status": "deleted"}


class RecognizeRequest(BaseModel):
    pages: List[int] = Field(default_factory=list)
    api_key: Optional[str] = None


@router.post("/{doc_id}/recognize")
async def recognize_pages(doc_id: str, request: RecognizeRequest):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    doc = documents.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="该文档未保存 PDF，无法进行 OCR")

    total_pages = int(doc.get("total_pages") or 0)
    requested = _sorted_unique_pages(request.pages)
    valid_pages = [page for page in requested if 1 <= page <= total_pages]
    if not valid_pages:
        raise HTTPException(status_code=400, detail="没有可识别的有效页码")

    queued_pages = await enqueue_ocr_job(
        doc_id=doc_id,
        pages=valid_pages,
        api_key=request.api_key,
        source="manual_select",
    )
    if not queued_pages:
        return {
            "status": "noop",
            "document_id": doc_id,
            "pages": [],
            "message": "没有新增待识别页面",
        }

    return {
        "status": "queued",
        "queued": True,
        "document_id": doc_id,
        "pages": queued_pages,
        "message": f"已加入后台 OCR 队列：{len(queued_pages)} 页",
    }


@router.post("/{doc_id}/ocr/cancel")
async def cancel_doc_ocr(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    queue_lock = _get_ocr_queue_lock()
    async with queue_lock:
        ocr_cancel_flags.add(doc_id)
        pending = len(ocr_jobs_by_doc.get(doc_id) or set())
        ocr_jobs_by_doc.pop(doc_id, None)

    _set_doc_progress(
        doc_id,
        stage="completed",
        current=100,
        message="OCR 任务取消中...",
    )

    return {
        "status": "cancel_requested",
        "document_id": doc_id,
        "pending_pages": pending,
    }


class ComplianceRequest(BaseModel):
    requirements: List[str]
    api_key: Optional[str] = None
    allowed_pages: Optional[List[int]] = None


@router.post("/{doc_id}/compliance")
async def check_compliance(doc_id: str, request: ComplianceRequest):
    from app.services.compliance_service import compliance_service

    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")

    if request.allowed_pages is None:
        doc = documents.get(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="文档不存在")
        allowed_pages = get_consistent_recognized_pages(doc)
    else:
        allowed_pages = _sorted_unique_pages(request.allowed_pages)

    try:
        results = await compliance_service.verify_requirements(
            doc_id,
            request.requirements,
            api_key=request.api_key,
            allowed_pages=allowed_pages,
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
    except Exception as exc:
        logger.exception("Compliance check failed for %s", doc_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{doc_id}/compliance_history")
async def get_compliance_history(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    data = document_store.load_compliance(doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="暂无合规检查历史")
    return data

