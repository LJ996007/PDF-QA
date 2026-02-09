"""
PDF page rendering utilities (on-demand).

We render a single PDF page to a PNG image (base64) for vision models.
"""

from __future__ import annotations

import base64
from typing import Tuple

import fitz  # PyMuPDF


def render_pdf_page_to_png_base64(
    pdf_path: str,
    page_number_1idx: int,
    dpi: int = 150,
) -> Tuple[str, float, float]:
    """
    Render a 1-indexed PDF page into a PNG base64 string.

    Returns: (image_base64_png, page_width_pt, page_height_pt)
    """
    if page_number_1idx <= 0:
        raise ValueError("page_number_1idx must be 1-indexed and positive")

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_number_1idx - 1]
        page_width_pt = float(page.rect.width)
        page_height_pt = float(page.rect.height)

        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        image_b64 = base64.b64encode(png_bytes).decode("utf-8")
        return image_b64, page_width_pt, page_height_pt
    finally:
        doc.close()

