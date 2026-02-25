"""Evidence assembly for compliance v2."""

from __future__ import annotations

from typing import Dict, List, Tuple

from app.models.schemas import BoundingBox, EvidenceItem, TextChunk


class EvidenceService:
    """Build normalized evidence objects from retrieved chunks."""

    def build_from_field_records(self, field_records: List[Dict]) -> Tuple[List[EvidenceItem], List[Dict]]:
        evidence_items: List[EvidenceItem] = []
        cursor = 1

        for record in field_records:
            chunks: List[TextChunk] = list(record.get("chunks") or [])
            refs: List[str] = []

            for idx, chunk in enumerate(chunks[:3]):
                ref_id = f"ev-{cursor}"
                cursor += 1

                support_level = "primary" if idx == 0 else "secondary"
                bbox = chunk.bbox or BoundingBox(page=chunk.page_number, x=0, y=0, w=100, h=20)

                item = EvidenceItem(
                    ref_id=ref_id,
                    page=chunk.page_number,
                    bbox=bbox,
                    source_type=chunk.source_type if chunk.source_type in {"native", "ocr"} else "derived",
                    field_name=record.get("field_name") or record.get("requirement") or "unknown",
                    support_level=support_level,
                    content=(chunk.content or "")[:500],
                )
                evidence_items.append(item)
                refs.append(ref_id)

            record["evidence_refs"] = refs

        return evidence_items, field_records


evidence_service = EvidenceService()
