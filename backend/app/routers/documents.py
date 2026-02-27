"""Document upload and processing routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
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

from app.models.schemas import (
    DocumentUploadResponse,
    MultimodalAuditJobRequest,
    MultimodalAuditJobResponse,
    OCRChunk,
    PageContent,
    ProgressEvent,
)
from app.services.baidu_ocr import baidu_ocr_gateway
from app.services.document_store import document_store
from app.services.local_ocr import local_ocr_gateway
from app.services.mm_provider import PageImageInput
from app.services.multimodal_audit_service import multimodal_audit_service
from app.services.parser import generate_thumbnail, get_ocr_required_pages, process_document, render_page_to_image
from app.services.rag_engine import rag_engine
from app.services.word_converter import (
    WordConversionError,
    convert_to_pdf,
    extract_markdown_with_markitdown,
)

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
audit_queue: "asyncio.Queue[AuditQueueJob]" = asyncio.Queue()
audit_worker_task: Optional[asyncio.Task] = None
audit_queue_lock: Optional[asyncio.Lock] = None
audit_jobs: Dict[str, Dict[str, Any]] = {}
audit_progress: Dict[str, Dict[str, Any]] = {}

KEEP_PDF = os.getenv("KEEP_PDF", "1").strip().lower() in {"1", "true", "yes", "y"}
VALID_OCR_STATUS = {"unrecognized", "processing", "recognized", "failed"}
ENABLE_MULTIMODAL_AUDIT = os.getenv("ENABLE_MULTIMODAL_AUDIT", "1").strip().lower() in {"1", "true", "yes", "y"}
ALLOWED_UPLOAD_FORMATS = {"pdf", "doc", "docx"}
WORD_UPLOAD_FORMATS = {"doc", "docx"}


@dataclass
class OCRQueueJob:
    doc_id: str
    pages: List[int]
    api_key: Optional[str] = None
    source: str = "manual"


@dataclass
class AuditQueueJob:
    job_id: str
    doc_id: str
    request: MultimodalAuditJobRequest
    allowed_pages: List[int]


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


def _detect_source_format(filename: str) -> str:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    return ext if ext in ALLOWED_UPLOAD_FORMATS else ""


def _is_word_source(source_format: str) -> bool:
    return source_format in WORD_UPLOAD_FORMATS


def _compute_text_quality(pages: List[PageContent]) -> Dict[str, float]:
    total_pages = len(pages)
    if total_pages <= 0:
        return {"readable_ratio": 0.0, "empty_ratio": 1.0, "char_count": 0.0, "low_quality": 1.0}

    empty_pages = 0
    readable_chars = 0
    total_chars = 0

    for page in pages:
        text = (page.text or "").strip()
        if not text:
            empty_pages += 1
            continue

        compact = re.sub(r"\s+", "", text)
        if not compact:
            empty_pages += 1
            continue

        total_chars += len(compact)
        readable_chars += sum(
            1 for ch in compact if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff")
        )

    readable_ratio = (readable_chars / total_chars) if total_chars else 0.0
    empty_ratio = empty_pages / float(total_pages)
    low_quality = total_chars < 300 or empty_ratio >= 0.5 or readable_ratio < 0.6
    return {
        "readable_ratio": float(readable_ratio),
        "empty_ratio": float(empty_ratio),
        "char_count": float(total_chars),
        "low_quality": 1.0 if low_quality else 0.0,
    }


def _split_markdown_to_pages(markdown_text: str, total_pages: int) -> List[str]:
    cleaned = (markdown_text or "").strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if not paragraphs:
        return []

    target_pages = max(int(total_pages), 1)
    total_chars = sum(len(p) for p in paragraphs)
    target_chars_per_page = max(total_chars // target_pages, 200)

    output: List[str] = []
    idx = 0
    for page_idx in range(target_pages):
        if idx >= len(paragraphs):
            output.append("")
            continue

        chunks: List[str] = []
        current_chars = 0
        remaining_pages = target_pages - page_idx
        remaining_paragraphs = len(paragraphs) - idx
        must_take_one = remaining_paragraphs <= remaining_pages

        while idx < len(paragraphs):
            para = paragraphs[idx]
            if chunks and current_chars >= target_chars_per_page and not must_take_one:
                break
            chunks.append(para)
            current_chars += len(para)
            idx += 1
            must_take_one = False

        output.append("\n\n".join(chunks).strip())

    if idx < len(paragraphs):
        tail = "\n\n".join(paragraphs[idx:]).strip()
        if output:
            output[-1] = (output[-1] + "\n\n" + tail).strip() if output[-1] else tail
        else:
            output.append(tail)
    return output


def _apply_docx_markdown_fallback(pages: List[PageContent], markdown_text: str) -> bool:
    if not pages:
        return False

    fallback_page_texts = _split_markdown_to_pages(markdown_text, len(pages))
    if not fallback_page_texts:
        return False

    changed = False
    for idx, page in enumerate(pages):
        fallback_text = fallback_page_texts[idx].strip() if idx < len(fallback_page_texts) else ""
        if not fallback_text:
            continue

        base_text = (page.text or "").strip()
        if page.type == "ocr" or not base_text:
            page.text = fallback_text
            page.type = "native"
            page.coordinates = None
            page.confidence = max(float(page.confidence or 0.0), 0.65)
            changed = True
            continue

        if len(fallback_text) > max(len(base_text) * 2, 1200):
            page.text = f"{base_text}\n{fallback_text}"
            page.coordinates = None
            changed = True

    return changed


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


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _update_doc_metrics(doc: dict) -> None:
    """Keep metrics fields internally consistent before persistence."""
    chunk_count = _to_int(doc.get("chunk_count"), 0)
    doc["chunk_count"] = chunk_count
    indexed_chunks = _to_int(doc.get("indexed_chunks"), chunk_count)
    doc["indexed_chunks"] = max(indexed_chunks, chunk_count)

    triggered_pages = _sorted_unique_pages(doc.get("ocr_triggered_page_numbers") or [])
    doc["ocr_triggered_page_numbers"] = triggered_pages
    doc["ocr_triggered_pages"] = len(triggered_pages)

    conversion_failed = str(doc.get("conversion_status") or "").strip().lower() == "failed"
    conversion_fail_count = _to_int(doc.get("conversion_fail_count"), 0)
    if conversion_failed and conversion_fail_count < 1:
        conversion_fail_count = 1
    doc["conversion_fail_count"] = conversion_fail_count


def _mark_ocr_triggered(doc: dict, page_num: int) -> None:
    pages = set(_sorted_unique_pages(doc.get("ocr_triggered_page_numbers") or []))
    if page_num > 0:
        pages.add(int(page_num))
    doc["ocr_triggered_page_numbers"] = sorted(pages)
    doc["ocr_triggered_pages"] = len(pages)


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


def _get_audit_queue_lock() -> asyncio.Lock:
    global audit_queue_lock
    if audit_queue_lock is None:
        audit_queue_lock = asyncio.Lock()
    return audit_queue_lock


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
    _update_doc_metrics(doc)
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
        "source_format": doc.get("source_format") or existing.get("source_format") or "pdf",
        "converted_from": doc.get("converted_from") or existing.get("converted_from"),
        "conversion_status": doc.get("conversion_status") or existing.get("conversion_status") or "ok",
        "conversion_ms": (
            int(doc.get("conversion_ms"))
            if doc.get("conversion_ms") is not None
            else (
                int(existing.get("conversion_ms"))
                if existing.get("conversion_ms") is not None
                else None
            )
        ),
        "conversion_fail_count": _to_int(
            doc.get("conversion_fail_count"),
            _to_int(existing.get("conversion_fail_count"), 0),
        ),
        "ocr_triggered_pages": _to_int(
            doc.get("ocr_triggered_pages"),
            _to_int(existing.get("ocr_triggered_pages"), 0),
        ),
        "ocr_triggered_page_numbers": _sorted_unique_pages(
            doc.get("ocr_triggered_page_numbers")
            or existing.get("ocr_triggered_page_numbers")
            or []
        ),
        "indexed_chunks": _to_int(
            doc.get("indexed_chunks"),
            _to_int(existing.get("indexed_chunks"), _to_int(doc.get("chunk_count"), 0)),
        ),
        "avg_context_tokens": (
            _to_float(doc.get("avg_context_tokens"))
            if doc.get("avg_context_tokens") is not None
            else (
                _to_float(existing.get("avg_context_tokens"))
                if existing.get("avg_context_tokens") is not None
                else None
            )
        ),
        "context_query_count": _to_int(
            doc.get("context_query_count"),
            _to_int(existing.get("context_query_count"), 0),
        ),
        "text_fallback_used": bool(doc.get("text_fallback_used") or existing.get("text_fallback_used")),
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
    ocr_triggered_page_numbers = _sorted_unique_pages(meta.get("ocr_triggered_page_numbers") or [])
    if not ocr_triggered_page_numbers and recognized_from_payload:
        ocr_triggered_page_numbers = sorted(recognized_from_payload)

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
        "source_format": meta.get("source_format") or "pdf",
        "converted_from": meta.get("converted_from"),
        "conversion_status": meta.get("conversion_status") or "ok",
        "conversion_ms": (
            int(meta.get("conversion_ms")) if meta.get("conversion_ms") is not None else None
        ),
        "conversion_fail_count": _to_int(
            meta.get("conversion_fail_count"),
            1 if (meta.get("conversion_status") == "failed") else 0,
        ),
        "ocr_triggered_pages": _to_int(
            meta.get("ocr_triggered_pages"),
            len(ocr_triggered_page_numbers),
        ),
        "ocr_triggered_page_numbers": ocr_triggered_page_numbers,
        "indexed_chunks": _to_int(meta.get("indexed_chunks"), _to_int(meta.get("chunk_count"), 0)),
        "avg_context_tokens": (
            _to_float(meta.get("avg_context_tokens"))
            if meta.get("avg_context_tokens") is not None
            else None
        ),
        "context_query_count": _to_int(meta.get("context_query_count"), 0),
        "text_fallback_used": bool(meta.get("text_fallback_used")),
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
        raise HTTPException(status_code=404, detail="Document not found")

    doc = documents.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="该文档未保存 PDF，无法执行 OCR")

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
            message=f"已加入 OCR 队列：{len(queued_pages)} 页",
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
                    message=f"Background OCR in progress ({idx}/{total})...",
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


def _set_audit_progress(
    *,
    job_id: str,
    doc_id: str,
    stage: str,
    current: int,
    total: int,
    message: str,
    status: str = "running",
) -> None:
    total = max(1, int(total))
    current = max(0, min(int(current), total))
    audit_progress[job_id] = {
        "job_id": job_id,
        "doc_id": doc_id,
        "stage": stage,
        "current": current,
        "total": total,
        "status": status,
        "message": message,
        "updated_at": _now_iso_utc(),
    }


def _build_page_image_inputs(file_path: str, pages: List[int]) -> List[PageImageInput]:
    inputs: List[PageImageInput] = []
    with fitz.open(file_path) as pdf_doc:
        total = len(pdf_doc)
        for page_num in pages:
            if page_num < 1 or page_num > total:
                continue
            page = pdf_doc[page_num - 1]
            image_base64 = render_page_to_image(page)
            inputs.append(
                PageImageInput(
                    page=page_num,
                    image_base64=image_base64,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                )
            )
    return inputs


async def enqueue_audit_job(doc_id: str, request: MultimodalAuditJobRequest, allowed_pages: List[int]) -> Dict[str, Any]:
    if not ENABLE_MULTIMODAL_AUDIT:
        raise HTTPException(status_code=503, detail="Multimodal audit is disabled by configuration.")

    await start_audit_worker()

    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    doc = documents.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="PDF is unavailable for multimodal audit.")

    total_pages = int(doc.get("total_pages") or 0)
    if total_pages <= 0:
        raise HTTPException(status_code=400, detail="Document has no pages.")

    requested_pages = _sorted_unique_pages(allowed_pages or [])
    target_pages = requested_pages or list(range(1, total_pages + 1))
    target_pages = [p for p in target_pages if 1 <= p <= total_pages]
    if not target_pages:
        raise HTTPException(status_code=400, detail="No valid pages selected for multimodal audit.")

    max_pages = int(os.getenv("MULTIMODAL_AUDIT_MAX_PAGES", "120") or "120")
    if len(target_pages) > max_pages:
        raise HTTPException(
            status_code=400,
            detail=f"Selected pages exceed limit ({len(target_pages)} > {max_pages}). Please narrow allowed_pages.",
        )

    job_id = f"audit_{uuid.uuid4().hex[:12]}"
    created_at = _now_iso_utc()
    sanitized_request = request.model_copy(update={"api_key": None})

    audit_jobs[job_id] = {
        "job_id": job_id,
        "doc_id": doc_id,
        "status": "queued",
        "created_at": created_at,
        "finished_at": None,
        "request": sanitized_request.model_dump(),
        "allowed_pages": target_pages,
        "result": None,
        "error": None,
    }
    _set_audit_progress(
        job_id=job_id,
        doc_id=doc_id,
        stage="queued",
        current=0,
        total=100,
        message="Audit job queued.",
        status="queued",
    )

    queue_lock = _get_audit_queue_lock()
    async with queue_lock:
        await audit_queue.put(
            AuditQueueJob(
                job_id=job_id,
                doc_id=doc_id,
                request=request,
                allowed_pages=target_pages,
            )
        )

    return {
        "job_id": job_id,
        "status": "queued",
        "progress_url": f"/api/documents/{doc_id}/multimodal_audit/jobs/{job_id}/progress",
        "result_url": f"/api/documents/{doc_id}/multimodal_audit/jobs/{job_id}",
    }


async def run_audit_worker() -> None:
    while True:
        job = await audit_queue.get()
        try:
            record = audit_jobs.get(job.job_id)
            if not record:
                continue

            record["status"] = "running"
            _set_audit_progress(
                job_id=job.job_id,
                doc_id=job.doc_id,
                stage="preparing",
                current=5,
                total=100,
                message="Preparing audit job...",
                status="running",
            )

            if not ensure_document_loaded(job.doc_id):
                raise RuntimeError("Document not found.")

            doc = documents.get(job.doc_id)
            if doc is None:
                raise RuntimeError("Document not found.")
            file_path = doc.get("file_path")
            if not file_path or not os.path.exists(file_path):
                raise RuntimeError("PDF is unavailable for multimodal audit.")

            _set_audit_progress(
                job_id=job.job_id,
                doc_id=job.doc_id,
                stage="rendering",
                current=10,
                total=100,
                message="Rendering audit page images...",
                status="running",
            )
            page_inputs = _build_page_image_inputs(file_path=file_path, pages=job.allowed_pages)
            if not page_inputs:
                raise RuntimeError("No page images were rendered for audit.")

            async def on_service_progress(stage: str, current: int, total: int, message: str) -> None:
                if stage == "vision_analyzing":
                    base = 20
                    span = 45
                elif stage == "rag_calibrating":
                    base = 70
                    span = 25
                else:
                    base = 20
                    span = 60
                pct = base + int((max(0, current) / max(1, total)) * span)
                _set_audit_progress(
                    job_id=job.job_id,
                    doc_id=job.doc_id,
                    stage=stage,
                    current=pct,
                    total=100,
                    message=message,
                    status="running",
                )

            result = await multimodal_audit_service.run_audit(
                doc_id=job.doc_id,
                audit_type=job.request.audit_type,
                page_images=page_inputs,
                bidder_name=job.request.bidder_name,
                custom_checks=job.request.custom_checks,
                api_key=job.request.api_key,
                model=job.request.model,
                progress_callback=on_service_progress,
            )

            final_payload = {
                "job_id": job.job_id,
                "doc_id": job.doc_id,
                "status": "completed",
                "created_at": record.get("created_at") or _now_iso_utc(),
                "finished_at": _now_iso_utc(),
                "request": (record.get("request") or {}),
                "allowed_pages": list(job.allowed_pages),
                **(result or {}),
            }
            record.update(
                {
                    "status": "completed",
                    "finished_at": final_payload["finished_at"],
                    "result": final_payload,
                    "error": None,
                }
            )
            document_store.append_multimodal_audit(job.doc_id, jsonable_encoder(final_payload), max_items=20)

            _set_audit_progress(
                job_id=job.job_id,
                doc_id=job.doc_id,
                stage="completed",
                current=100,
                total=100,
                message="Audit completed.",
                status="completed",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Unexpected error in multimodal audit worker")
            record = audit_jobs.get(job.job_id)
            if record is not None:
                record.update(
                    {
                        "status": "failed",
                        "finished_at": _now_iso_utc(),
                        "result": None,
                        "error": str(exc),
                    }
                )
            _set_audit_progress(
                job_id=job.job_id,
                doc_id=job.doc_id,
                stage="failed",
                current=100,
                total=100,
                message=f"Multimodal audit failed: {str(exc)}",
                status="failed",
            )
        finally:
            audit_queue.task_done()


async def start_audit_worker() -> None:
    global audit_worker_task
    if audit_worker_task is None or audit_worker_task.done():
        audit_worker_task = asyncio.create_task(run_audit_worker(), name="multimodal-audit-worker")


async def stop_audit_worker() -> None:
    global audit_worker_task
    if audit_worker_task is None:
        return
    audit_worker_task.cancel()
    try:
        await audit_worker_task
    except asyncio.CancelledError:
        pass
    finally:
        audit_worker_task = None


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
        raise HTTPException(status_code=500, detail=f"无法为第 {page_num} 页生成 OCR 图像")
    return image_base64, page_width, page_height


async def recognize_document_page(doc_id: str, page_num: int, api_key: Optional[str] = None) -> dict:
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    lock = _get_or_create_doc_lock(doc_id)

    file_path = ""
    baidu_ocr_url: Optional[str] = None
    baidu_ocr_token: Optional[str] = None
    cached_image_base64: Optional[str] = None
    sha256 = ""

    async with lock:
        doc = documents.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")

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
                "message": "该页已识别",
            }
        if current_status == "processing":
            return {
                "page": page_num,
                "chunks": [],
                "status": "processing",
                "already_recognized": False,
                "message": "该页 OCR 正在进行中",
            }

        file_path = doc.get("file_path") or ""
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=400, detail="该文档未保存 PDF，无法执行 OCR")

        target_page = _get_target_page(doc, page_num)
        cached_image_base64 = getattr(target_page, "image_base64", None) if target_page else None
        baidu_ocr_url = doc.get("baidu_ocr_url") or os.getenv("BAIDU_OCR_API_URL")
        baidu_ocr_token = doc.get("baidu_ocr_token") or os.getenv("BAIDU_OCR_TOKEN")
        sha256 = str(doc.get("sha256") or "")

        status_map[page_num] = "processing"
        doc["page_ocr_status"] = status_map
        _mark_ocr_triggered(doc, page_num)
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
            raise HTTPException(status_code=422, detail=f"第 {page_num} 页 OCR 结果为空")

        _clear_page_ocr_chunks(doc_id, page_num)
        indexed_count = await rag_engine.index_ocr_result(
            doc_id,
            page_num,
            [{"text": c.text, "bbox": c.bbox.model_dump()} for c in chunks],
            api_key=api_key,
        )
        if indexed_count <= 0:
            raise HTTPException(status_code=422, detail=f"Page {page_num} has no indexable text")

        async with lock:
            doc = documents.get(doc_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="Document not found")

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
    source_format: str = "pdf",
    converted_from: Optional[str] = None,
    conversion_status: str = "ok",
    conversion_ms: Optional[int] = None,
    source_file_path: Optional[str] = None,
):
    del ocr_model, ocr_provider

    source_format = source_format if source_format in ALLOWED_UPLOAD_FORMATS else "pdf"
    ocr_mode = _normalize_ocr_mode(ocr_mode)
    if _is_word_source(source_format) and ocr_mode == "full":
        logger.info("Force OCR mode to manual for Word upload doc_id=%s", doc_id)
        ocr_mode = "manual"

    effective_keep_pdf = bool(keep_pdf or ocr_mode == "manual")
    text_fallback_used = False

    try:
        document_progress[doc_id] = ProgressEvent(
            stage="extracting",
            current=0,
            total=100,
            message="Extracting document...",
            document_id=doc_id,
        )

        pages, thumbnails = process_document(file_path)
        quality = _compute_text_quality(pages)
        if (
            source_format == "docx"
            and bool(quality.get("low_quality"))
            and source_file_path
            and os.path.exists(source_file_path)
        ):
            markdown_text, fallback_error = extract_markdown_with_markitdown(source_file_path)
            if markdown_text:
                text_fallback_used = _apply_docx_markdown_fallback(pages, markdown_text)
                if text_fallback_used:
                    quality = _compute_text_quality(pages)
                    logger.info(
                        "Applied markitdown fallback doc_id=%s readable_ratio=%.3f empty_ratio=%.3f chars=%s",
                        doc_id,
                        quality.get("readable_ratio", 0.0),
                        quality.get("empty_ratio", 1.0),
                        int(quality.get("char_count", 0.0)),
                    )
            elif fallback_error:
                logger.warning("MarkItDown fallback unavailable for %s: %s", doc_id, fallback_error)

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
            "indexed_chunks": 0,
            "ocr_triggered_pages": 0,
            "ocr_triggered_page_numbers": [],
            "conversion_fail_count": 1 if conversion_status == "failed" else 0,
            "avg_context_tokens": None,
            "context_query_count": 0,
            "pages": pages,
            "baidu_ocr_url": baidu_ocr_url,
            "baidu_ocr_token": baidu_ocr_token,
            "source_format": source_format,
            "converted_from": converted_from,
            "conversion_status": conversion_status,
            "conversion_ms": conversion_ms,
            "text_fallback_used": text_fallback_used,
        }
        _update_doc_metrics(doc)
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
                "indexed_chunks": 0,
                "keep_pdf": effective_keep_pdf,
                "pdf_path": file_path if effective_keep_pdf else None,
                "source_format": source_format,
                "converted_from": converted_from,
                "conversion_status": conversion_status,
                "conversion_ms": conversion_ms,
                "conversion_fail_count": 1 if conversion_status == "failed" else 0,
                "ocr_triggered_pages": 0,
                "ocr_triggered_page_numbers": [],
                "avg_context_tokens": None,
                "context_query_count": 0,
                "text_fallback_used": text_fallback_used,
            }
        )

        document_progress[doc_id] = ProgressEvent(
            stage="embedding",
            current=40,
            total=100,
            message="Building vector index...",
            document_id=doc_id,
        )
        native_chunk_count = await rag_engine.index_document(doc_id, pages, api_key)
        doc["chunk_count"] = int(native_chunk_count)
        doc["indexed_chunks"] = int(native_chunk_count)
        _persist_doc_meta(doc_id, status="processing")

        if ocr_mode == "full":
            pages_to_recognize = list(doc.get("ocr_required_pages") or [])
            if not pages_to_recognize:
                _persist_doc_meta(doc_id, status="completed")
                _set_doc_progress(
                    doc_id,
                    stage="completed",
                    current=100,
                    message=f"Completed with OCR recognized pages: {len(doc.get('recognized_pages') or [])}/{total_pages}",
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
                    message=f"Completed with OCR recognized pages: {len(doc.get('recognized_pages') or [])}/{total_pages}",
                )
                if not effective_keep_pdf:
                    _cleanup_temp_pdf_if_needed(doc_id)
                return

            _persist_doc_meta(doc_id, status="processing")
            _set_doc_progress(
                doc_id,
                stage="ocr",
                current=45,
                message=f"Queued OCR pages: {len(queued_pages)}",
            )
            return

        _persist_doc_meta(doc_id, status="completed")
        document_progress[doc_id] = ProgressEvent(
            stage="completed",
            current=100,
            total=100,
            message="文档已就绪。",
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
                    "keep_pdf": bool(effective_keep_pdf),
                    "pdf_path": file_path if effective_keep_pdf else None,
                    "source_format": source_format,
                    "converted_from": converted_from,
                    "conversion_status": "failed",
                    "conversion_ms": conversion_ms,
                    "conversion_fail_count": 1,
                    "ocr_triggered_pages": 0,
                    "ocr_triggered_page_numbers": [],
                    "indexed_chunks": 0,
                    "avg_context_tokens": None,
                    "context_query_count": 0,
                    "text_fallback_used": text_fallback_used,
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
    finally:
        if source_file_path and os.path.exists(source_file_path):
            try:
                os.remove(source_file_path)
            except Exception as exc:
                logger.warning("Failed to remove temporary source file for %s: %s", doc_id, str(exc))

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
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    source_format = _detect_source_format(file.filename)
    if not source_format:
        raise HTTPException(status_code=400, detail="仅支持 .pdf、.doc、.docx 文件")

    ocr_mode = _normalize_ocr_mode(ocr_mode)
    if _is_word_source(source_format) and ocr_mode == "full":
        # Default to manual OCR for Word uploads to reduce unnecessary OCR usage.
        ocr_mode = "manual"

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
                message="Completed (cache hit)",
                document_id=doc_id,
            )
            return DocumentUploadResponse(
                document_id=doc_id,
                status="completed",
                total_pages=int(doc.get("total_pages") or existing.get("total_pages") or 0),
                ocr_required_pages=list(doc.get("ocr_required_pages") or existing.get("ocr_required_pages") or []),
                progress_url=f"/api/documents/{doc_id}/progress",
                ocr_mode=doc.get("ocr_mode") or existing.get("ocr_mode") or ocr_mode,
                source_format=(doc.get("source_format") or existing.get("source_format") or source_format),
            )

    doc_id = (existing.get("doc_id") if existing else None) or f"doc_{uuid.uuid4().hex[:12]}"
    created_at = (existing.get("created_at") if existing else None) or _now_iso_utc()
    effective_keep_pdf = bool(KEEP_PDF or ocr_mode == "manual")
    converted_from: Optional[str] = source_format if _is_word_source(source_format) else None
    conversion_status = "ok"
    conversion_ms: Optional[int] = None
    source_file_path: Optional[str] = None

    upload_dir = Path("uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = str(upload_dir / f"{doc_id}.pdf")
    if source_format == "pdf":
        with open(file_path, "wb") as handle:
            handle.write(content)
    else:
        source_dir = upload_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_file_path = str(source_dir / f"{doc_id}.{source_format}")
        with open(source_file_path, "wb") as handle:
            handle.write(content)
        try:
            converted = convert_to_pdf(source_file_path, file_path)
            file_path = converted.output_pdf_path
            conversion_status = "ok"
            conversion_ms = int(converted.elapsed_ms)
        except WordConversionError as exc:
            try:
                document_store.upsert_doc(
                    {
                        "doc_id": doc_id,
                        "sha256": sha256,
                        "filename": file.filename,
                        "created_at": created_at,
                        "status": "failed",
                        "keep_pdf": False,
                        "pdf_path": None,
                        "source_format": source_format,
                        "converted_from": converted_from,
                        "conversion_status": "failed",
                        "conversion_ms": conversion_ms,
                        "conversion_fail_count": 1,
                        "ocr_triggered_pages": 0,
                        "ocr_triggered_page_numbers": [],
                        "indexed_chunks": 0,
                        "avg_context_tokens": None,
                        "context_query_count": 0,
                        "text_fallback_used": False,
                    }
                )
            except Exception:
                pass
            if source_file_path and os.path.exists(source_file_path):
                try:
                    os.remove(source_file_path)
                except Exception:
                    pass
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            logger.warning("Word conversion failed for %s: %s", file.filename, str(exc))
            raise HTTPException(status_code=400, detail=f"Word 转 PDF 失败: {exc}") from exc

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
            "source_format": source_format,
            "converted_from": converted_from,
            "conversion_status": conversion_status,
            "conversion_ms": conversion_ms,
            "conversion_fail_count": 1 if conversion_status == "failed" else 0,
            "ocr_triggered_pages": 0,
            "ocr_triggered_page_numbers": [],
            "indexed_chunks": int(existing.get("indexed_chunks") or existing.get("chunk_count") or 0)
            if existing
            else 0,
            "avg_context_tokens": existing.get("avg_context_tokens") if existing else None,
            "context_query_count": int(existing.get("context_query_count") or 0) if existing else 0,
            "text_fallback_used": False,
        }
    )

    document_progress[doc_id] = ProgressEvent(
        stage="extracting",
        current=0,
        total=100,
        message="已接收上传，正在处理...",
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
        source_format,
        converted_from,
        conversion_status,
        conversion_ms,
        source_file_path,
    )

    return DocumentUploadResponse(
        document_id=doc_id,
        status="processing",
        total_pages=0,
        ocr_required_pages=[],
        progress_url=f"/api/documents/{doc_id}/progress",
        ocr_mode=ocr_mode,
        source_format=source_format,
    )


@router.get("/{doc_id}/progress")
async def get_progress(doc_id: str):
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
                "source_format": meta.get("source_format") or "pdf",
                "converted_from": meta.get("converted_from"),
                "conversion_status": meta.get("conversion_status") or "ok",
                "conversion_ms": (
                    int(meta.get("conversion_ms")) if meta.get("conversion_ms") is not None else None
                ),
                "conversion_fail_count": _to_int(meta.get("conversion_fail_count"), 0),
                "ocr_triggered_pages": _to_int(meta.get("ocr_triggered_pages"), 0),
                "indexed_chunks": _to_int(meta.get("indexed_chunks"), _to_int(meta.get("chunk_count"), 0)),
                "avg_context_tokens": (
                    _to_float(meta.get("avg_context_tokens"))
                    if meta.get("avg_context_tokens") is not None
                    else None
                ),
                "context_query_count": _to_int(meta.get("context_query_count"), 0),
                "text_fallback_used": bool(meta.get("text_fallback_used")),
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
        raise HTTPException(status_code=404, detail="Document not found")

    lock = _get_or_create_doc_lock(doc_id)
    async with lock:
        doc = documents.get(doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
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
            "source_format": doc.get("source_format") or "pdf",
            "converted_from": doc.get("converted_from"),
            "conversion_status": doc.get("conversion_status") or "ok",
            "conversion_ms": (
                int(doc.get("conversion_ms")) if doc.get("conversion_ms") is not None else None
            ),
            "conversion_fail_count": _to_int(doc.get("conversion_fail_count"), 0),
            "ocr_triggered_pages": _to_int(doc.get("ocr_triggered_pages"), 0),
            "indexed_chunks": _to_int(doc.get("indexed_chunks"), _to_int(doc.get("chunk_count"), 0)),
            "avg_context_tokens": (
                _to_float(doc.get("avg_context_tokens"))
                if doc.get("avg_context_tokens") is not None
                else None
            ),
            "context_query_count": _to_int(doc.get("context_query_count"), 0),
            "text_fallback_used": bool(doc.get("text_fallback_used")),
        }


@router.get("/{doc_id}/pdf")
async def get_document_pdf(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    path = _resolve_pdf_path(doc_id)
    if not path:
        raise HTTPException(status_code=404, detail="PDF is not stored for this document")
    return FileResponse(path, media_type="application/pdf", filename=f"{doc_id}.pdf")


@router.head("/{doc_id}/pdf")
async def head_document_pdf(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    path = _resolve_pdf_path(doc_id)
    if not path:
        raise HTTPException(status_code=404, detail="PDF is not stored for this document")
    return Response(status_code=200)


@router.post("/{doc_id}/attach_pdf")
async def attach_document_pdf(doc_id: str, file: UploadFile = File(...)):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()

    meta = document_store.get_by_doc_id(doc_id) or {}
    expected_sha = str(meta.get("sha256") or "").strip().lower()
    if expected_sha and expected_sha != sha256:
        raise HTTPException(status_code=400, detail="Selected PDF does not match recorded hash")

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
    document_store.delete_multimodal_audit(doc_id)

    documents.pop(doc_id, None)
    document_progress.pop(doc_id, None)
    document_locks.pop(doc_id, None)
    for job_id, record in list(audit_jobs.items()):
        if record.get("doc_id") == doc_id:
            audit_jobs.pop(job_id, None)
            audit_progress.pop(job_id, None)

    return {"status": "deleted"}


class RecognizeRequest(BaseModel):
    pages: List[int] = Field(default_factory=list)
    api_key: Optional[str] = None


@router.post("/{doc_id}/recognize")
async def recognize_pages(doc_id: str, request: RecognizeRequest):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    doc = documents.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="该文档未保存 PDF，无法执行 OCR")

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
            "message": "No new pages to OCR",
        }

    return {
        "status": "queued",
        "queued": True,
        "document_id": doc_id,
        "pages": queued_pages,
        "message": f"已加入 OCR 队列：{len(queued_pages)} 页",
    }


@router.post("/{doc_id}/ocr/cancel")
async def cancel_doc_ocr(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    queue_lock = _get_ocr_queue_lock()
    async with queue_lock:
        ocr_cancel_flags.add(doc_id)
        pending = len(ocr_jobs_by_doc.get(doc_id) or set())
        ocr_jobs_by_doc.pop(doc_id, None)

    _set_doc_progress(
        doc_id,
        stage="completed",
        current=100,
        message="已请求取消 OCR...",
    )

    return {
        "status": "cancel_requested",
        "document_id": doc_id,
        "pending_pages": pending,
    }


@router.post("/{doc_id}/multimodal_audit/jobs", response_model=MultimodalAuditJobResponse)
async def create_multimodal_audit_job(doc_id: str, request: MultimodalAuditJobRequest):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    if request.audit_type in {"certificate", "personnel"} and not request.bidder_name.strip():
        raise HTTPException(status_code=400, detail="bidder_name is required for certificate/personnel audit.")

    doc = documents.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    total_pages = int(doc.get("total_pages") or 0)
    if total_pages <= 0:
        raise HTTPException(status_code=400, detail="Document has no pages.")

    allowed_pages = _sorted_unique_pages(request.allowed_pages)
    if not allowed_pages:
        allowed_pages = list(range(1, total_pages + 1))

    payload = await enqueue_audit_job(doc_id=doc_id, request=request, allowed_pages=allowed_pages)
    return MultimodalAuditJobResponse(**payload)


@router.get("/{doc_id}/multimodal_audit/jobs/{job_id}/progress")
async def get_multimodal_audit_progress(doc_id: str, job_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    async def event_generator():
        while True:
            progress = audit_progress.get(job_id)
            if progress is None:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "Multimodal audit job not found."}),
                }
                break
            if progress.get("doc_id") != doc_id:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "Job does not belong to this document."}),
                }
                break

            yield {"event": "progress", "data": json.dumps(progress)}
            if progress.get("stage") in {"completed", "failed"}:
                break
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())


@router.get("/{doc_id}/multimodal_audit/jobs/{job_id}")
async def get_multimodal_audit_job_result(doc_id: str, job_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    record = audit_jobs.get(job_id)
    if record and record.get("doc_id") == doc_id:
        result = record.get("result")
        if result:
            return result
        return {
            "job_id": job_id,
            "doc_id": doc_id,
            "status": record.get("status"),
            "error": record.get("error"),
            "created_at": record.get("created_at"),
            "finished_at": record.get("finished_at"),
        }

    history = document_store.load_multimodal_audit(doc_id) or {}
    jobs = history.get("jobs") if isinstance(history, dict) else []
    if isinstance(jobs, list):
        for item in jobs:
            if isinstance(item, dict) and item.get("job_id") == job_id:
                return item
    raise HTTPException(status_code=404, detail="Multimodal audit job not found")


@router.get("/{doc_id}/multimodal_audit/history")
async def get_multimodal_audit_history(doc_id: str):
    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    data = document_store.load_multimodal_audit(doc_id) or {"version": 1, "doc_id": doc_id, "jobs": []}
    return data


class ComplianceRequest(BaseModel):
    requirements: List[str]
    api_key: Optional[str] = None
    allowed_pages: Optional[List[int]] = None


@router.post("/{doc_id}/compliance")
async def check_compliance(doc_id: str, request: ComplianceRequest):
    from app.services.compliance_service import compliance_service

    if not ensure_document_loaded(doc_id):
        raise HTTPException(status_code=404, detail="Document not found")

    if request.allowed_pages is None:
        doc = documents.get(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
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
        raise HTTPException(status_code=404, detail="Document not found")
    data = document_store.load_compliance(doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="No compliance history")
    return data


