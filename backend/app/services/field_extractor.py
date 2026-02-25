"""Contract field extraction service for compliance v2."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.models.schemas import TextChunk
from app.services.rag_engine import rag_engine


AMOUNT_PATTERN = re.compile(r"(?:人民币)?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?\s*(?:元|万元|亿元))")
DATE_PATTERN = re.compile(r"((?:19|20)\d{2}[年\-/\.]\d{1,2}[月\-/\.]\d{1,2}日?)")
TERM_PATTERN = re.compile(r"(\d+\s*(?:年|个月|月|日)|自.{0,18}至.{0,18})")
PARTY_PATTERN = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()·\-\s]{2,64}(?:有限公司|公司|集团|中心|研究院|大学|学校|医院|协会))")


def _compact_text(text: str, max_len: int = 120) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:max_len]


class FieldExtractor:
    """Extract contract-related fields from retrieved chunks."""

    def _infer_field_key(self, requirement: str, index: int) -> str:
        req = (requirement or "").lower()
        if any(k in req for k in ["甲方", "乙方", "主体", "单位", "公司", "组织", "签约方"]):
            return "party"
        if any(k in req for k in ["金额", "价款", "费用", "人民币", "付款"]):
            return "amount"
        if any(k in req for k in ["日期", "签订", "生效", "时间", "签署"]):
            return "date"
        if any(k in req for k in ["期限", "有效期", "履约期", "到期", "终止"]):
            return "term"
        return f"custom_{index + 1}"

    def _extract_value(self, field_key: str, chunks: List[TextChunk]) -> str:
        if not chunks:
            return ""

        full_text = "\n".join(chunk.content or "" for chunk in chunks[:4])

        if field_key == "amount":
            match = AMOUNT_PATTERN.search(full_text)
            return match.group(1) if match else _compact_text(chunks[0].content)
        if field_key == "date":
            match = DATE_PATTERN.search(full_text)
            return match.group(1) if match else _compact_text(chunks[0].content)
        if field_key == "term":
            match = TERM_PATTERN.search(full_text)
            return match.group(1) if match else _compact_text(chunks[0].content)
        if field_key == "party":
            match = PARTY_PATTERN.search(full_text)
            return match.group(1) if match else _compact_text(chunks[0].content)
        return _compact_text(chunks[0].content)

    def _estimate_confidence(self, field_key: str, value: str, chunks: List[TextChunk]) -> float:
        if not value or not chunks:
            return 0.0

        base = 0.45
        if field_key in {"amount", "date", "term"}:
            base += 0.2
        if len(value) >= 4:
            base += 0.1
        if len(chunks) >= 2:
            base += 0.1
        if len(value) > 16:
            base += 0.05
        return min(base, 0.98)

    async def extract_contract_fields(
        self,
        doc_id: str,
        requirements: List[str],
        allowed_pages: Optional[List[int]] = None,
        api_key: Optional[str] = None,
    ) -> List[Dict]:
        records: List[Dict] = []
        requirements = [str(r).strip() for r in (requirements or []) if str(r).strip()]
        if not requirements:
            return records

        for index, requirement in enumerate(requirements):
            chunks = await rag_engine.retrieve(
                query=requirement,
                doc_id=doc_id,
                top_k=6,
                api_key=api_key,
                allowed_pages=allowed_pages,
                ensure_page_coverage=True,
            )

            field_key = self._infer_field_key(requirement, index)
            field_name = requirement

            if not chunks:
                records.append(
                    {
                        "field_key": field_key,
                        "field_name": field_name,
                        "requirement": requirement,
                        "value": "",
                        "confidence": 0.0,
                        "status": "missing",
                        "evidence_refs": [],
                        "chunks": [],
                    }
                )
                continue

            value = self._extract_value(field_key, chunks)
            confidence = self._estimate_confidence(field_key, value, chunks)
            status = "matched" if value else "uncertain"

            records.append(
                {
                    "field_key": field_key,
                    "field_name": field_name,
                    "requirement": requirement,
                    "value": value,
                    "confidence": confidence,
                    "status": status,
                    "evidence_refs": [],
                    "chunks": chunks,
                }
            )

        return records


field_extractor = FieldExtractor()
