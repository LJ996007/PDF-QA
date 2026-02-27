"""Multimodal audit orchestration service."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.models.schemas import BoundingBox
from app.services.mm_provider import PageImageInput, get_multimodal_provider
from app.services.rag_engine import rag_engine

ProgressCallback = Callable[[str, int, int, str], Awaitable[None] | None]


class MultimodalAuditService:
    """Run multimodal audit with visual extraction + rule aggregation + RAG calibration."""

    def __init__(self) -> None:
        self.page_batch_size = max(1, int(os.getenv("MULTIMODAL_AUDIT_PAGE_BATCH", "6") or "6"))
        self.max_pages = max(1, int(os.getenv("MULTIMODAL_AUDIT_MAX_PAGES", "120") or "120"))

    async def run_audit(
        self,
        *,
        doc_id: str,
        audit_type: str,
        page_images: List[PageImageInput],
        bidder_name: str = "",
        custom_checks: Optional[List[str]] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        provider_name: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        custom_checks = [c.strip() for c in (custom_checks or []) if c and c.strip()]
        template = self._get_template(audit_type)
        provider = get_multimodal_provider(provider_name)
        page_sizes = {image.page: (image.width, image.height) for image in page_images}

        facts, custom_results = await self._extract_facts_with_batches(
            audit_type=audit_type,
            provider=provider,
            template=template,
            page_images=page_images,
            custom_checks=custom_checks,
            api_key=api_key,
            model=model,
            progress_callback=progress_callback,
        )
        items = self._apply_rule_engine(
            audit_type=audit_type,
            facts=facts,
            bidder_name=bidder_name,
            custom_checks=custom_checks,
            custom_results=custom_results,
        )
        calibrated_items = await self._calibrate_references(
            doc_id=doc_id,
            items=items,
            page_sizes=page_sizes,
            progress_callback=progress_callback,
        )
        summary = self._build_summary(calibrated_items)

        return {
            "audit_type": audit_type,
            "generated_at": datetime.utcnow().isoformat(),
            "status": "completed",
            "items": calibrated_items,
            "summary": summary,
            "fact_count": len(facts),
        }

    def normalize_name_strict(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def is_same_name_strict(self, lhs: str, rhs: str) -> bool:
        return bool(self.normalize_name_strict(lhs)) and self.normalize_name_strict(lhs) == self.normalize_name_strict(rhs)

    def _get_template(self, audit_type: str) -> Dict[str, Any]:
        if audit_type == "contract":
            return {
                "fact_keys": [
                    "contract_main_content",
                    "contract_sign_date",
                    "party_a_stamp",
                    "party_b_stamp",
                ],
                "builtin_checks": [
                    ("contract_main_content", "合同主要内容完整性"),
                    ("contract_sign_date", "签订日期识别"),
                    ("party_a_stamp", "甲方盖章核验"),
                    ("party_b_stamp", "乙方盖章核验"),
                ],
            }
        if audit_type == "certificate":
            return {
                "fact_keys": [
                    "certificate_name",
                    "certificate_valid_until",
                    "certificate_holder_org",
                ],
                "builtin_checks": [
                    ("certificate_name", "证书名称识别"),
                    ("certificate_valid_until", "证书有效期核验"),
                    ("certificate_holder_org", "证书获取单位与投标人一致性"),
                ],
            }
        if audit_type == "personnel":
            return {
                "fact_keys": [
                    "person_name",
                    "qualification_name",
                    "qualification_valid_until",
                    "qualification_org",
                ],
                "builtin_checks": [
                    ("person_qualification", "人员资质信息完整性"),
                    ("qualification_valid_until", "人员资质有效期核验"),
                    ("qualification_org", "人员资质单位与投标人一致性"),
                ],
            }
        raise ValueError(f"Unsupported audit_type: {audit_type}")

    def _build_prompt(self, audit_type: str, fact_keys: List[str], custom_checks: List[str]) -> str:
        custom_section = ""
        if custom_checks:
            custom_lines = "\n".join([f"- {item}" for item in custom_checks])
            custom_section = (
                "\n另外请对以下自定义检查项给出结果，输出到 custom_checks 数组：\n"
                f"{custom_lines}\n"
            )

        return (
            "你是一名投标文件审核助手。请逐页识别扫描件中的关键信息，"
            "只返回 JSON，不要输出额外解释。\n"
            f"审核场景: {audit_type}\n"
            f"fact.key 只能使用这些值: {', '.join(fact_keys)}\n"
            "facts 数组每项结构："
            '{"page":1,"key":"...","value":"...","evidence_text":"...","confidence":0.0}\n'
            "要求：\n"
            "1. page 必须是图片对应页码。\n"
            "2. evidence_text 必须是页面上可见的证据原文片段。\n"
            "3. confidence 取 0 到 1。\n"
            "4. 没有把握时可以不输出该事实。\n"
            f"{custom_section}"
            "custom_checks 每项结构："
            '{"check":"...","status":"pass|fail|needs_review","reason":"...","page":1,"evidence_text":"...","confidence":0.0}'
        )

    def _build_json_schema(self, fact_keys: List[str]) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page": {"type": "integer"},
                            "key": {"type": "string", "enum": fact_keys},
                            "value": {"type": "string"},
                            "evidence_text": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["page", "key", "value", "evidence_text", "confidence"],
                    },
                },
                "custom_checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "check": {"type": "string"},
                            "status": {"type": "string", "enum": ["pass", "fail", "needs_review"]},
                            "reason": {"type": "string"},
                            "page": {"type": "integer"},
                            "evidence_text": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["check", "status", "reason", "page", "evidence_text", "confidence"],
                    },
                },
            },
            "required": ["facts"],
        }

    async def _extract_facts_with_batches(
        self,
        *,
        audit_type: str,
        provider,
        template: Dict[str, Any],
        page_images: List[PageImageInput],
        custom_checks: List[str],
        api_key: Optional[str],
        model: Optional[str],
        progress_callback: Optional[ProgressCallback],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        fact_keys = list(template["fact_keys"])
        prompt = self._build_prompt(audit_type, fact_keys, custom_checks)
        schema = self._build_json_schema(fact_keys)
        all_facts: List[Dict[str, Any]] = []
        all_custom: List[Dict[str, Any]] = []

        total_batches = max(1, (len(page_images) + self.page_batch_size - 1) // self.page_batch_size)
        for idx in range(total_batches):
            start = idx * self.page_batch_size
            batch = page_images[start : start + self.page_batch_size]
            await self._emit_progress(
                progress_callback,
                "vision_analyzing",
                idx,
                total_batches,
                f"视觉识别中（批次 {idx + 1}/{total_batches}）",
            )
            payload = await provider.analyze_pages(
                images=batch,
                prompt=prompt,
                json_schema=schema,
                api_key=api_key,
                model=model,
            )
            facts = payload.get("facts") if isinstance(payload, dict) else None
            if isinstance(facts, list):
                for item in facts:
                    normalized = self._normalize_fact(item, fact_keys)
                    if normalized:
                        all_facts.append(normalized)
            custom_payload = payload.get("custom_checks") if isinstance(payload, dict) else None
            if isinstance(custom_payload, list):
                for item in custom_payload:
                    normalized_custom = self._normalize_custom_check(item)
                    if normalized_custom:
                        all_custom.append(normalized_custom)

        await self._emit_progress(progress_callback, "vision_analyzing", total_batches, total_batches, "视觉识别完成")
        return all_facts, all_custom

    def _normalize_fact(self, item: Any, allowed_keys: List[str]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        key = str(item.get("key") or "").strip()
        if key not in allowed_keys:
            return None
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            return None
        if page <= 0:
            return None
        value = str(item.get("value") or "").strip()
        evidence_text = str(item.get("evidence_text") or "").strip()
        confidence = self._to_confidence(item.get("confidence"))
        if not value and not evidence_text:
            return None
        return {
            "page": page,
            "key": key,
            "value": value,
            "evidence_text": evidence_text or value,
            "confidence": confidence,
        }

    def _normalize_custom_check(self, item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        check = str(item.get("check") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        if not check or status not in {"pass", "fail", "needs_review"}:
            return None
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            page = 1
        if page <= 0:
            page = 1
        reason = str(item.get("reason") or "").strip() or "模型未提供说明。"
        evidence_text = str(item.get("evidence_text") or "").strip()
        confidence = self._to_confidence(item.get("confidence"))
        return {
            "check": check,
            "status": status,
            "reason": reason,
            "page": page,
            "evidence_text": evidence_text,
            "confidence": confidence,
        }

    def _to_confidence(self, value: Any) -> float:
        try:
            conf = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, conf))

    def _apply_rule_engine(
        self,
        *,
        audit_type: str,
        facts: List[Dict[str, Any]],
        bidder_name: str,
        custom_checks: List[str],
        custom_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        fact_map: Dict[str, List[Dict[str, Any]]] = {}
        for fact in facts:
            fact_map.setdefault(fact["key"], []).append(fact)
        for values in fact_map.values():
            values.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)

        if audit_type == "contract":
            items = self._build_contract_items(fact_map)
        elif audit_type == "certificate":
            items = self._build_certificate_items(fact_map, bidder_name)
        elif audit_type == "personnel":
            items = self._build_personnel_items(fact_map, bidder_name)
        else:
            raise ValueError(f"Unsupported audit_type: {audit_type}")

        items.extend(self._build_custom_items(custom_checks, custom_results))

        for item in items:
            evidence = item.get("evidence_candidates") or []
            if evidence:
                continue
            item["status"] = "needs_review"
            item["reason"] = f"{item['reason']}（未找到直接证据，需人工复核）".strip()
        return items

    def _build_contract_items(self, fact_map: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        items.append(self._presence_check("contract_main_content", "合同主要内容核验", fact_map.get("contract_main_content", [])))
        sign_date_fact = self._pick_best_fact(fact_map.get("contract_sign_date", []))
        if sign_date_fact:
            parsed = self._extract_latest_date(sign_date_fact.get("value") or sign_date_fact.get("evidence_text") or "")
            if parsed:
                status = "pass"
                reason = f"识别到签订日期 {parsed.isoformat()}。"
            else:
                status = "needs_review"
                reason = f"检测到疑似签订日期文本“{sign_date_fact.get('value') or sign_date_fact.get('evidence_text')}”，但无法规范化解析。"
            items.append(
                self._item(
                    check_key="contract_sign_date",
                    check_title="签订日期识别",
                    status=status,
                    reason=reason,
                    confidence=sign_date_fact.get("confidence", 0.0),
                    evidence_candidates=[self._fact_to_candidate(sign_date_fact)],
                )
            )
        else:
            items.append(
                self._item(
                    check_key="contract_sign_date",
                    check_title="签订日期识别",
                    status="needs_review",
                    reason="未识别到明确签订日期。",
                    confidence=0.0,
                    evidence_candidates=[],
                )
            )

        items.append(self._stamp_check("contract_party_a_stamp", "甲方盖章核验", fact_map.get("party_a_stamp", [])))
        items.append(self._stamp_check("contract_party_b_stamp", "乙方盖章核验", fact_map.get("party_b_stamp", [])))
        return items

    def _build_certificate_items(self, fact_map: Dict[str, List[Dict[str, Any]]], bidder_name: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        items.append(self._presence_check("certificate_name", "证书名称识别", fact_map.get("certificate_name", [])))

        validity_fact = self._pick_best_fact(fact_map.get("certificate_valid_until", []))
        if validity_fact:
            end_date = self._extract_latest_date(validity_fact.get("value") or validity_fact.get("evidence_text") or "")
            if end_date:
                if end_date < date.today():
                    status = "fail"
                    reason = f"证书已过期（有效期至 {end_date.isoformat()}）。"
                else:
                    status = "pass"
                    reason = f"证书有效期至 {end_date.isoformat()}，当前未过期。"
            else:
                status = "needs_review"
                reason = "识别到有效期文本但无法解析为标准日期。"
            items.append(
                self._item(
                    check_key="certificate_valid_until",
                    check_title="证书有效期核验",
                    status=status,
                    reason=reason,
                    confidence=validity_fact.get("confidence", 0.0),
                    evidence_candidates=[self._fact_to_candidate(validity_fact)],
                )
            )
        else:
            items.append(
                self._item(
                    check_key="certificate_valid_until",
                    check_title="证书有效期核验",
                    status="needs_review",
                    reason="未识别到证书有效期。",
                    confidence=0.0,
                    evidence_candidates=[],
                )
            )

        holder_fact = self._pick_best_fact(fact_map.get("certificate_holder_org", []))
        if holder_fact:
            holder_org = holder_fact.get("value") or holder_fact.get("evidence_text") or ""
            if self.is_same_name_strict(holder_org, bidder_name):
                status = "pass"
                reason = f"证书获取单位“{holder_org}”与投标人名称严格一致。"
            else:
                status = "fail"
                reason = f"证书获取单位“{holder_org}”与投标人“{bidder_name}”不一致。"
            items.append(
                self._item(
                    check_key="certificate_holder_org",
                    check_title="证书获取单位与投标人一致性",
                    status=status,
                    reason=reason,
                    confidence=holder_fact.get("confidence", 0.0),
                    evidence_candidates=[self._fact_to_candidate(holder_fact)],
                )
            )
        else:
            items.append(
                self._item(
                    check_key="certificate_holder_org",
                    check_title="证书获取单位与投标人一致性",
                    status="needs_review",
                    reason="未识别到证书获取单位。",
                    confidence=0.0,
                    evidence_candidates=[],
                )
            )
        return items

    def _build_personnel_items(self, fact_map: Dict[str, List[Dict[str, Any]]], bidder_name: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        person_fact = self._pick_best_fact(fact_map.get("person_name", []))
        qualification_fact = self._pick_best_fact(fact_map.get("qualification_name", []))
        evidence_candidates = []
        if person_fact:
            evidence_candidates.append(self._fact_to_candidate(person_fact))
        if qualification_fact:
            evidence_candidates.append(self._fact_to_candidate(qualification_fact))
        if person_fact and qualification_fact:
            status = "pass"
            reason = f"识别到人员“{person_fact.get('value')}”及资质“{qualification_fact.get('value')}”。"
            confidence = max(person_fact.get("confidence", 0.0), qualification_fact.get("confidence", 0.0))
        else:
            status = "needs_review"
            reason = "人员姓名或资质名称信息不完整。"
            confidence = max((person_fact or {}).get("confidence", 0.0), (qualification_fact or {}).get("confidence", 0.0))
        items.append(
            self._item(
                check_key="person_qualification",
                check_title="人员资质信息完整性",
                status=status,
                reason=reason,
                confidence=confidence,
                evidence_candidates=evidence_candidates,
            )
        )

        valid_fact = self._pick_best_fact(fact_map.get("qualification_valid_until", []))
        if valid_fact:
            valid_until = self._extract_latest_date(valid_fact.get("value") or valid_fact.get("evidence_text") or "")
            if valid_until:
                status = "pass" if valid_until >= date.today() else "fail"
                reason = (
                    f"人员资质有效期至 {valid_until.isoformat()}，当前未过期。"
                    if status == "pass"
                    else f"人员资质已过期（有效期至 {valid_until.isoformat()}）。"
                )
            else:
                status = "needs_review"
                reason = "识别到人员资质有效期文本但无法解析。"
            items.append(
                self._item(
                    check_key="qualification_valid_until",
                    check_title="人员资质有效期核验",
                    status=status,
                    reason=reason,
                    confidence=valid_fact.get("confidence", 0.0),
                    evidence_candidates=[self._fact_to_candidate(valid_fact)],
                )
            )
        else:
            items.append(
                self._item(
                    check_key="qualification_valid_until",
                    check_title="人员资质有效期核验",
                    status="needs_review",
                    reason="未识别到人员资质有效期。",
                    confidence=0.0,
                    evidence_candidates=[],
                )
            )

        org_fact = self._pick_best_fact(fact_map.get("qualification_org", []))
        if org_fact:
            org = org_fact.get("value") or org_fact.get("evidence_text") or ""
            if self.is_same_name_strict(org, bidder_name):
                status = "pass"
                reason = f"人员资质所属单位“{org}”与投标人名称严格一致。"
            else:
                status = "fail"
                reason = f"人员资质所属单位“{org}”与投标人“{bidder_name}”不一致。"
            items.append(
                self._item(
                    check_key="qualification_org",
                    check_title="人员资质单位与投标人一致性",
                    status=status,
                    reason=reason,
                    confidence=org_fact.get("confidence", 0.0),
                    evidence_candidates=[self._fact_to_candidate(org_fact)],
                )
            )
        else:
            items.append(
                self._item(
                    check_key="qualification_org",
                    check_title="人员资质单位与投标人一致性",
                    status="needs_review",
                    reason="未识别到人员资质所属单位。",
                    confidence=0.0,
                    evidence_candidates=[],
                )
            )
        return items

    def _build_custom_items(self, custom_checks: List[str], custom_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        indexed_results: Dict[str, Dict[str, Any]] = {}
        for item in custom_results:
            check = str(item.get("check") or "").strip()
            if check and check not in indexed_results:
                indexed_results[check] = item

        items = []
        for idx, check in enumerate(custom_checks, start=1):
            result = indexed_results.get(check)
            if result:
                evidence_candidates = []
                if result.get("evidence_text"):
                    evidence_candidates.append(
                        {
                            "page": int(result.get("page") or 1),
                            "evidence_text": str(result.get("evidence_text") or "").strip(),
                            "source": "vision",
                        }
                    )
                items.append(
                    self._item(
                        check_key=f"custom_{idx:02d}",
                        check_title=f"自定义检查：{check}",
                        status=result.get("status") or "needs_review",
                        reason=result.get("reason") or "模型未提供说明。",
                        confidence=self._to_confidence(result.get("confidence")),
                        evidence_candidates=evidence_candidates,
                    )
                )
            else:
                items.append(
                    self._item(
                        check_key=f"custom_{idx:02d}",
                        check_title=f"自定义检查：{check}",
                        status="needs_review",
                        reason="模型未返回该自定义检查结果。",
                        confidence=0.0,
                        evidence_candidates=[],
                    )
                )
        return items

    def _presence_check(self, check_key: str, title: str, facts: List[Dict[str, Any]]) -> Dict[str, Any]:
        fact = self._pick_best_fact(facts)
        if fact:
            value = fact.get("value") or fact.get("evidence_text") or ""
            return self._item(
                check_key=check_key,
                check_title=title,
                status="pass",
                reason=f"识别到关键信息：{value}",
                confidence=fact.get("confidence", 0.0),
                evidence_candidates=[self._fact_to_candidate(fact)],
            )
        return self._item(
            check_key=check_key,
            check_title=title,
            status="needs_review",
            reason="未识别到明确证据。",
            confidence=0.0,
            evidence_candidates=[],
        )

    def _stamp_check(self, check_key: str, title: str, facts: List[Dict[str, Any]]) -> Dict[str, Any]:
        fact = self._pick_best_fact(facts)
        if not fact:
            return self._item(
                check_key=check_key,
                check_title=title,
                status="needs_review",
                reason="未识别到盖章信息。",
                confidence=0.0,
                evidence_candidates=[],
            )

        verdict = self._parse_presence_value(fact.get("value") or fact.get("evidence_text") or "")
        if verdict == "pass":
            status = "pass"
            reason = "识别结果显示已盖章。"
        elif verdict == "fail":
            status = "fail"
            reason = "识别结果显示未盖章。"
        else:
            status = "needs_review"
            reason = "盖章状态不明确。"
        return self._item(
            check_key=check_key,
            check_title=title,
            status=status,
            reason=reason,
            confidence=fact.get("confidence", 0.0),
            evidence_candidates=[self._fact_to_candidate(fact)],
        )

    def _parse_presence_value(self, value: str) -> str:
        text = (value or "").strip().lower()
        if not text:
            return "unknown"
        present_tokens = ["present", "yes", "true", "有", "已盖章", "盖章", "存在", "签章"]
        absent_tokens = ["absent", "no", "false", "无", "未盖章", "未签章", "不存在"]
        for token in absent_tokens:
            if token in text:
                return "fail"
        for token in present_tokens:
            if token in text:
                return "pass"
        return "unknown"

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

        for idx, item in enumerate(items, start=1):
            await self._emit_progress(
                progress_callback,
                "rag_calibrating",
                idx - 1,
                total,
                f"证据定位中（{idx}/{total}）",
            )
            candidates = list(item.pop("evidence_candidates", []))
            refs = []
            for candidate in candidates[:2]:
                calibrated = await self._calibrate_single_reference(doc_id=doc_id, candidate=candidate, page_sizes=page_sizes)
                ref_counter += 1
                calibrated["ref_id"] = f"ref-{ref_counter}"
                refs.append(calibrated)

            if refs:
                tags = " ".join(f"[{ref['ref_id']}]" for ref in refs)
                reason = str(item.get("reason") or "").strip()
                item["reason"] = f"{reason} {tags}".strip()
            item["references"] = refs
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

    def _extract_latest_date(self, text: str) -> Optional[date]:
        raw = (text or "").strip()
        if not raw:
            return None

        matches = re.findall(r"(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})", raw)
        parsed: List[date] = []
        for y, m, d in matches:
            try:
                parsed.append(date(int(y), int(m), int(d)))
            except ValueError:
                continue
        if not parsed:
            return None
        parsed.sort()
        return parsed[-1]

    def _pick_best_fact(self, facts: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        if not facts:
            return None
        return sorted(facts, key=lambda item: item.get("confidence", 0.0), reverse=True)[0]

    def _fact_to_candidate(self, fact: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "page": int(fact.get("page") or 1),
            "evidence_text": str(fact.get("evidence_text") or fact.get("value") or "").strip(),
            "source": "vision",
        }

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
