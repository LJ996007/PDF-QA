from __future__ import annotations

from typing import Iterable, List, Optional


def select_candidate_pages(
    *,
    explicit_pages: Optional[List[int]],
    chunk_pages: Iterable[int],
    max_pages: int,
    total_pages: Optional[int] = None,
) -> List[int]:
    """
    Select pages to analyze with vision.

    Priority:
    1) explicit_pages (if provided)
    2) unique pages from retrieved chunks (preserve order)
    3) fallback to [1]
    """
    try:
        max_pages = int(max_pages)
    except Exception:
        max_pages = 2
    if max_pages <= 0:
        max_pages = 1

    if explicit_pages:
        pages = [int(p) for p in explicit_pages]
    else:
        seen = set()
        pages = []
        for p in chunk_pages:
            try:
                p = int(p)
            except Exception:
                continue
            if p not in seen:
                seen.add(p)
                pages.append(p)

    if not pages:
        pages = [1]

    if isinstance(total_pages, int) and total_pages > 0:
        pages = [p for p in pages if 1 <= p <= total_pages]
        if not pages:
            pages = [1]

    return pages[:max_pages]

