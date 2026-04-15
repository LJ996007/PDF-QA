"""Multimodal audit orchestration service."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.models.schemas import BoundingBox
from app.services.mm_provider import PageImageInput, get_multimodal_provider
from app.services.rag_engine import rag_engine

ProgressCallback = Callable[[str, int, int, str], Awaitable[None] | None]


class MultimodalAuditService:
    """Run multimodal audit with editable profile rules and RAG calibration."""

    def __init__(self) -> None:
        self.page_batch_size = max(1, int(os.getenv("MULTIMODAL_AUDIT_PAGE_BATCH", "6") or "6"))
        self.max_pages = max(1, int(os.getenv("MULTIMODAL_AUDIT_MAX_PAGES", "120") or "120"))

    async def run_audit(
        self,
        *,
        doc_id: str,
        audit_profile: Dict[str, Any],
        page_images: List[PageImageInput],
        bidder_name: str = "",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider_name: Optional[str] = None,
        base_url: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        provider = get_multimodal_provider(provider_name)
        page_sizes = {image.page: (image.width, image.height) for image in page_images}
        enabled_rules = [
            self._normalize_rule(rule)
            for rule in (audit_profile.get("rules") or [])
            if isinstance(rule, dict) and bool(rule.get("enabled", True))
        ]
        if not enabled_rules:
            raise ValueError("审核模板没有启用的审核项。")

        candidates = await self._extract_rule_results_with_batches(
            provider=provider,
            audit_profile=audit_profile,
            enabled_rules=enabled_rules,
            page_images=page_images,
            bidder_name=bidder_name,
            api_key=api_key,
            model=model,
            provider_name=provider_name,
            base_url=base_url,
            progress_callback=progress_callback,
        )
        items = self._aggregate_rule_results(enabled_rules, candidates)
        calibrated_items = await self._calibrate_references(
            doc_id=doc_id,
            items=items,
            page_sizes=page_sizes,
            progress_callback=progress_callback,
        )
        summary = self._build_summary(calibrated_items)

        return {
            "generated_at": datetime.utcnow().isoformat(),
            "status": "completed",
            "items": calibrated_items,
            "summary": summary,
            "rule_count": len(enabled_rules),
        }

    def _normalize_rule(self, raw_rule: Dict[str, Any]) -> Dict[str, str]:
        return {
            "id": str(raw_rule.get("id") or "").strip(),
            "title": str(raw_rule.get("title") or "").strip(),
            "instruction": str(raw_rule.get("instruction") or "").strip(),
        }

    def _build_prompt(
        self,
        *,
        audit_profile_name: str,
        enabled_rules: List[Dict[str, str]],
        bidder_name: str,
    ) -> str:
        rule_lines = "\n".join(
            [
                f'- rule_id: "{rule["id"]}"\n  title: "{rule["title"]}"\n  instruction: "{rule["instruction"]}"'
                for rule in enabled_rules
            ]
        )
        bidder_line = f"投标人名称: {bidder_name}\n" if bidder_name.strip() else ""
        return (
            "你是一名投标文件专项审核助手。请只根据当前批次页面中可以直接看到的证据进行判断，"
            "不要臆测未出现的信息，只返回 JSON。\n"
            f"审核模板名称: {audit_profile_name}\n"
            f"当前日期: {date.today().isoformat()}\n"
            f"{bidder_line}"
            "请针对以下审核项逐条判断；如果当前批次页面没有直接证据，请不要输出该审核项：\n"
            f"{rule_lines}\n"
            "输出 JSON 结构："
            '{"results":[{"rule_id":"...","title":"...","status":"pass|fail|needs_review","reason":"...","page":1,"evidence_text":"...","confidence":0.0}]}\n'
            "要求：\n"
            "1. rule_id 必须来自给定审核项。\n"
            "2. page 必须是当前批次中实际存在的页码。\n"
            "3. evidence_text 必须摘录页面上可见的直接证据原文。\n"
            "4. status 只能是 pass、fail、needs_review。\n"
            "5. reason 要简洁，明确说明通过、不通过或需复核的依据。\n"
            "6. 若当前批次无法支持判断，就不要输出该审核项。"
        )

    def _build_json_schema(self, allowed_rule_ids: List[str]) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rule_id": {"type": "string", "enum": allowed_rule_ids},
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": ["pass", "fail", "needs_review"]},
                            "reason": {"type": "string"},
                            "page": {"type": "integer"},
                            "evidence_text": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["rule_id", "title", "status", "reason", "page", "evidence_text", "confidence"],
                    },
                }
            },
            "required": ["results"],
        }

    async def _extract_rule_results_with_batches(
        self,
        *,
        provider,
        audit_profile: Dict[str, Any],
        enabled_rules: List[Dict[str, str]],
        page_images: List[PageImageInput],
        bidder_name: str,
        api_key: Optional[str],
        model: Optional[str],
        provider_name: Optional[str],
        base_url: Optional[str],
        progress_callback: Optional[ProgressCallback],
    ) -> List[Dict[str, Any]]:
        allowed_rule_ids = [rule["id"] for rule in enabled_rules]
        prompt = self._build_prompt(
            audit_profile_name=str(audit_profile.get("name") or audit_profile.get("id") or "专项审核"),
            enabled_rules=enabled_rules,
            bidder_name=bidder_name,
        )
        schema = self._build_json_schema(allowed_rule_ids)
        all_candidates: List[Dict[str, Any]] = []

        total_batches = max(1, (len(page_images) + self.page_batch_size - 1) // self.page_batch_size)
        for index in range(total_batches):
            start = index * self.page_batch_size
            batch = page_images[start : start + self.page_batch_size]
            batch_pages = {image.page for image in batch}
            await self._emit_progress(
                progress_callback,
                "vision_analyzing",
                index,
                total_batches,
                f"视觉识别中（批次 {index + 1}/{total_batches}）",
            )
            payload = await provider.analyze_pages(
                images=batch,
                prompt=prompt,
                json_schema=schema,
                api_key=api_key,
                model=model,
                provider_name=provider_name,
                base_url=base_url,
            )
            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                continue
            for item in results:
                normalized = self._normalize_rule_result(item, allowed_rule_ids, batch_pages)
                if normalized:
                    all_candidates.append(normalized)

        await self._emit_progress(progress_callback, "vision_analyzing", total_batches, total_batches, "视觉识别完成")
        return all_candidates

    def _normalize_rule_result(
        self,
        item: Any,
        allowed_rule_ids: List[str],
        allowed_pages: set[int],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        rule_id = str(item.get("rule_id") or "").strip()
        title = str(item.get("title") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        evidence_text = str(item.get("evidence_text") or "").strip()
        if rule_id not in allowed_rule_ids or status not in {"pass", "fail", "needs_review"}:
            return None
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            return None
        if page not in allowed_pages:
            return None
        confidence = self._to_confidence(item.get("confidence"))
        return {
            "rule_id": rule_id,
            "title": title,
            "status": status,
            "reason": reason or "模型未提供说明。",
            "page": page,
            "evidence_text": evidence_text,
            "confidence": confidence,
        }

    def _aggregate_rule_results(
        self,
        enabled_rules: List[Dict[str, str]],
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate["rule_id"], []).append(candidate)

        items: List[Dict[str, Any]] = []
        for rule in enabled_rules:
            rule_id = rule["id"]
            title = rule["title"] or rule_id
            chosen = self._pick_best_candidate(grouped.get(rule_id) or [])
            if not chosen:
                items.append(
                    self._item(
                        check_key=rule_id,
                        check_title=title,
                        status="needs_review",
                        reason="未从当前文档中找到直接证据，需人工复核。",
                        confidence=0.0,
                        evidence_candidates=[],
                    )
                )
                continue

            evidence_candidates = []
            if chosen.get("evidence_text"):
                evidence_candidates.append(
                    {
                        "page": int(chosen.get("page") or 1),
                        "evidence_text": str(chosen.get("evidence_text") or "").strip(),
                        "source": "vision",
                    }
                )
            items.append(
                self._item(
                    check_key=rule_id,
                    check_title=title,
                    status=str(chosen.get("status") or "needs_review"),
                    reason=str(chosen.get("reason") or "模型未提供说明。"),
                    confidence=self._to_confidence(chosen.get("confidence")),
                    evidence_candidates=evidence_candidates,
                )
            )
        return items

    def _pick_best_candidate(self, candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None

        def rank(item: Dict[str, Any]) -> Tuple[int, float, int]:
            status_order = {"fail": 3, "pass": 2, "needs_review": 1}
            return (
                status_order.get(str(item.get("status") or "needs_review"), 1),
                self._to_confidence(item.get("confidence")),
                len(str(item.get("evidence_text") or "").strip()),
            )

        return sorted(candidates, key=rank, reverse=True)[0]

    def _to_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))

    async def _calibrate_references(
        self,
        *,
        doc_id: str,
        items: List[Dict[str, Any]],
        page_sizes: Dict[int, Tuple[float, float]],
        progress_callback: Optional[ProgressCallback],
    ) -> List[Dict[str, Any]]:
        total = len(items)
        ref_counter = 0
        output: List[Dict[str, Any]] = []

        for index, item in enumerate(items, start=1):
            await self._emit_progress(
                progress_callback,
                "rag_calibrating",
                index - 1,
                total,
                f"证据定位中（{index}/{total}）",
            )
            candidates = list(item.pop("evidence_candidates", []))
            references = []
            for candidate in candidates[:2]:
                calibrated = await self._calibrate_single_reference(doc_id=doc_id, candidate=candidate, page_sizes=page_sizes)
                ref_counter += 1
                calibrated["ref_id"] = f"ref-{ref_counter}"
                references.append(calibrated)

            if references:
                tags = " ".join(f"[{ref['ref_id']}]" for ref in references)
                reason = str(item.get("reason") or "").strip()
                item["reason"] = f"{reason} {tags}".strip()
            item["references"] = references
            output.append(item)

        await self._emit_progress(progress_callback, "rag_calibrating", total, total, "证据定位完成")
        return output

    async def _calibrate_single_reference(
        self,
        *,
        doc_id: str,
        candidate: Dict[str, Any],
        page_sizes: Dict[int, Tuple[float, float]],
    ) -> Dict[str, Any]:
        page = int(candidate.get("page") or 1)
        if page <= 0:
            page = 1
        evidence_text = str(candidate.get("evidence_text") or "").strip()
        query_text = evidence_text or "关键证据"

        best_chunk = None
        try:
            chunks = await rag_engine.retrieve(
                query=query_text,
                doc_id=doc_id,
                top_k=5,
                allowed_pages=[page],
            )
            if chunks:
                best_chunk = self._pick_best_chunk(query_text, chunks)
        except Exception:
            best_chunk = None

        if best_chunk:
            bbox = best_chunk.bbox.model_dump()
            content = evidence_text or best_chunk.content
            source = "rag_calibrated"
            page = int(best_chunk.page_number)
        else:
            width, height = page_sizes.get(page, (595.0, 842.0))
            fallback_bbox = self._build_fallback_bbox(page=page, width=width, height=height)
            bbox = fallback_bbox.model_dump()
            content = evidence_text or "未能自动定位精确文本，已回退到页级定位。"
            source = "fallback_page"

        return {
            "page": page,
            "evidence_text": content,
            "bbox": bbox,
            "source": source,
        }

    def _pick_best_chunk(self, query_text: str, chunks: List[Any]):
        best_chunk = chunks[0]
        best_score = self._text_overlap_score(query_text, chunks[0].content)
        for chunk in chunks[1:]:
            score = self._text_overlap_score(query_text, chunk.content)
            if score > best_score:
                best_score = score
                best_chunk = chunk
        return best_chunk

    def _text_overlap_score(self, lhs: str, rhs: str) -> float:
        left_tokens = set(re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", lhs or ""))
        right_tokens = set(re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", rhs or ""))
        if not left_tokens or not right_tokens:
            return 0.0
        inter = left_tokens & right_tokens
        return len(inter) / len(left_tokens)

    def _build_fallback_bbox(self, *, page: int, width: float, height: float) -> BoundingBox:
        return BoundingBox(
            page=page,
            x=36.0,
            y=36.0,
            w=max(100.0, width * 0.85),
            h=max(30.0, min(96.0, height * 0.15)),
        )

    def _item(
        self,
        *,
        check_key: str,
        check_title: str,
        status: str,
        reason: str,
        confidence: float,
        evidence_candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        normalized_status = status if status in {"pass", "fail", "needs_review", "error"} else "needs_review"
        return {
            "check_key": check_key,
            "check_title": check_title,
            "status": normalized_status,
            "reason": reason.strip(),
            "confidence": self._to_confidence(confidence),
            "evidence_candidates": evidence_candidates,
        }

    async def _emit_progress(
        self,
        callback: Optional[ProgressCallback],
        stage: str,
        current: int,
        total: int,
        message: str,
    ) -> None:
        if callback is None:
            return
        maybe = callback(stage, current, total, message)
        if maybe is not None and hasattr(maybe, "__await__"):
            await maybe

    def _build_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {"pass": 0, "fail": 0, "needs_review": 0, "error": 0, "total": len(items)}
        for item in items:
            status = str(item.get("status") or "needs_review")
            if status not in summary:
                status = "needs_review"
            summary[status] += 1
        return summary

    def dump_debug_json(self, payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)


multimodal_audit_service = MultimodalAuditService()
