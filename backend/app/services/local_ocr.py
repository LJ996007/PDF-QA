"""
Local OCR fallback (offline).

This project primarily uses Baidu PP-OCRv5 via HTTP. When credentials are missing/invalid,
we can still OCR scanned PDFs locally using RapidOCR (onnxruntime).

Coordinate system:
- Input image comes from backend/app/services/parser.py rendered at 150 DPI.
- We convert pixel coordinates to PDF points (72 DPI) with scale = 72/150.
- We keep the origin at top-left (same as the frontend highlight layer expects).
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import List, Optional

from rapidocr_onnxruntime import RapidOCR

from app.models.schemas import OCRChunk, BoundingBox


class LocalOCRGateway:
    def __init__(self):
        self._engine: Optional[RapidOCR] = None

    def _get_engine(self) -> RapidOCR:
        # Lazy init because model loading can take a while.
        if self._engine is None:
            self._engine = RapidOCR()
        return self._engine

    def _merge_ocr_chunks(self, chunks: List[OCRChunk]) -> List[OCRChunk]:
        """
        Merge nearby OCR chunks on the same line and drop low-value noise.

        RapidOCR can output many tiny boxes (single letters, bullets). Those hurt retrieval quality.
        """
        if not chunks:
            return []

        valid: List[OCRChunk] = []
        for c in chunks:
            t = (c.text or "").strip()
            if not t:
                continue

            # Drop 1-char chunks (common OCR noise like "O", "X", bullets).
            if len(t) == 1:
                continue

            # Drop pure punctuation.
            if re.fullmatch(r"[\W_]+", t, flags=re.UNICODE):
                continue

            valid.append(c)

        if not valid:
            return []

        valid.sort(key=lambda c: (c.bbox.y, c.bbox.x))

        merged: List[OCRChunk] = []
        current = valid[0]

        for nxt in valid[1:]:
            y_diff = abs(current.bbox.y - nxt.bbox.y)
            height_avg = (current.bbox.h + nxt.bbox.h) / 2.0
            x_gap = nxt.bbox.x - (current.bbox.x + current.bbox.w)

            is_same_line = y_diff < (height_avg * 0.5)
            is_adjacent = -20 < x_gap < 50

            if is_same_line and is_adjacent:
                new_x = min(current.bbox.x, nxt.bbox.x)
                new_y = min(current.bbox.y, nxt.bbox.y)
                new_max_x = max(current.bbox.x + current.bbox.w, nxt.bbox.x + nxt.bbox.w)
                new_max_y = max(current.bbox.y + current.bbox.h, nxt.bbox.y + nxt.bbox.h)

                current = OCRChunk(
                    text=f"{current.text} {nxt.text}".strip(),
                    bbox=BoundingBox(
                        page=current.bbox.page,
                        x=new_x,
                        y=new_y,
                        w=new_max_x - new_x,
                        h=new_max_y - new_y,
                    ),
                )
            else:
                merged.append(current)
                current = nxt

        merged.append(current)
        return merged

    async def process_image(
        self,
        image_base64: str,
        page_number: int,
        page_width: float,
        page_height: float,
    ) -> List[OCRChunk]:
        if not image_base64:
            return []

        img_bytes = base64.b64decode(image_base64)
        engine = self._get_engine()

        # Run OCR in a worker thread to avoid blocking the event loop.
        result, _elapse = await asyncio.to_thread(engine, img_bytes)
        if not result:
            return []

        scale = 72.0 / 150.0  # Must match parser.py render DPI.
        chunks: List[OCRChunk] = []

        for item in result:
            # item: [poly, text, score]
            if not item or len(item) < 2:
                continue
            poly = item[0]
            text = str(item[1]) if item[1] is not None else ""

            if not text.strip():
                continue
            if not poly or len(poly) < 4:
                continue

            try:
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                x0, y0 = min(xs), min(ys)
                x1, y1 = max(xs), max(ys)
            except Exception:
                continue

            x = float(x0 * scale)
            y = float(y0 * scale)
            w = float((x1 - x0) * scale)
            h = float((y1 - y0) * scale)

            if w <= 0 or h <= 0:
                continue

            chunks.append(
                OCRChunk(
                    text=text,
                    bbox=BoundingBox(
                        page=page_number,
                        x=x,
                        y=y,
                        w=w,
                        h=h,
                    ),
                )
            )

        return self._merge_ocr_chunks(chunks)


local_ocr_gateway = LocalOCRGateway()
