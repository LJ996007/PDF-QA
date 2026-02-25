"""Layout summarization for compliance v2."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _get_page_attr(page: Any, key: str, default: Any = None) -> Any:
    if hasattr(page, key):
        return getattr(page, key)
    if isinstance(page, dict):
        return page.get(key, default)
    return default


class LayoutService:
    """Compute lightweight page/layout signals from parsed pages."""

    def summarize_document(self, doc: Dict[str, Any], allowed_pages: Optional[List[int]] = None) -> Dict[str, Any]:
        pages = list(doc.get("pages") or [])
        allowed = set(int(p) for p in (allowed_pages or []) if int(p) > 0)

        page_summaries: List[Dict[str, Any]] = []
        scanned_pages = 0
        native_pages = 0

        for page in pages:
            page_number = int(_get_page_attr(page, "page_number", 0) or 0)
            if page_number <= 0:
                continue
            if allowed and page_number not in allowed:
                continue

            text = str(_get_page_attr(page, "text", "") or "")
            page_type = str(_get_page_attr(page, "type", "ocr") or "ocr")
            coordinates = _get_page_attr(page, "coordinates", None)
            line_count = len([line for line in text.splitlines() if line.strip()])
            char_count = len(text.replace(" ", "").replace("\n", ""))
            has_coordinates = bool(coordinates)

            if page_type == "ocr":
                scanned_pages += 1
            else:
                native_pages += 1

            page_summaries.append(
                {
                    "page_number": page_number,
                    "page_type": page_type,
                    "char_count": char_count,
                    "line_count": line_count,
                    "has_coordinates": has_coordinates,
                    "text_density": round(char_count / max(line_count, 1), 2),
                }
            )

        total = len(page_summaries)
        scanned_ratio = (scanned_pages / total) if total > 0 else 0.0
        avg_density = (
            sum(float(item["text_density"]) for item in page_summaries) / total
            if total > 0
            else 0.0
        )

        if scanned_ratio > 0.7:
            complexity = "high"
        elif scanned_ratio > 0.3 or avg_density < 12:
            complexity = "medium"
        else:
            complexity = "low"

        return {
            "total_pages": total,
            "scanned_pages": scanned_pages,
            "native_pages": native_pages,
            "scanned_ratio": round(scanned_ratio, 3),
            "avg_text_density": round(avg_density, 2),
            "complexity": complexity,
            "pages": page_summaries,
        }


layout_service = LayoutService()
