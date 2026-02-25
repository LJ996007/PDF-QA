"""Compliance services (legacy + v2 contract workflow)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.schemas import (
    ComplianceFieldResult,
    ComplianceRuleResult,
    ComplianceV2Response,
    EvidenceItem,
    ReviewState,
    TextChunk,
)
from app.services.evidence_service import evidence_service
from app.services.field_extractor import field_extractor
from app.services.layout_service import layout_service
from app.services.llm_router import llm_router
from app.services.rag_engine import rag_engine
from app.services.review_service import review_service
from app.services.rule_engine import rule_engine


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComplianceService:
    """Compatibility wrapper for v1 and orchestrator for v2."""

    async def verify_requirements(
        self,
        doc_id: str,
        requirements: List[str],
        api_key: Optional[str] = None,
        allowed_pages: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Legacy compliance check: retrieval + LLM verdict + markdown table."""

        tasks = [
            self._verify_single_requirement(doc_id, req, api_key=api_key, allowed_pages=allowed_pages)
            for req in (requirements or [])
            if str(req).strip()
        ]
        if not tasks:
            return {"results": [], "markdown": ""}

        results = await asyncio.gather(*tasks)
        for idx, item in enumerate(results):
            item["id"] = idx + 1

        return {"results": results, "markdown": self._format_as_markdown(results)}

    def _format_as_markdown(self, results: List[Dict[str, Any]]) -> str:
        status_map = {
            "satisfied": "PASS",
            "unsatisfied": "FAIL",
            "partial": "PARTIAL",
            "unknown": "UNKNOWN",
            "error": "ERROR",
        }
        lines = [
            "| # | Requirement | Assessment | Status |",
            "|---:|---|---|---|",
        ]

        for item in results:
            requirement = str(item.get("requirement") or "").replace("|", "\\|")
            response = str(item.get("response") or "").replace("|", "\\|")
            status = status_map.get(str(item.get("status") or "unknown"), "UNKNOWN")
            lines.append(f"| {item.get('id', '-')} | {requirement} | {response} | {status} |")

        return "\n".join(lines)

    async def _verify_single_requirement(
        self,
        doc_id: str,
        requirement: str,
        api_key: Optional[str] = None,
        allowed_pages: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        try:
            chunks = await rag_engine.retrieve(
                query=requirement,
                doc_id=doc_id,
                top_k=10,
                api_key=api_key,
                allowed_pages=allowed_pages,
            )

            if not chunks:
                return {
                    "requirement": requirement,
                    "status": "unknown",
                    "response": "No supporting content found in the selected pages.",
                    "references": [],
                }

            context = "\n\n".join([f"[ref-{i + 1}] {chunk.content}" for i, chunk in enumerate(chunks)])
            prompt = (
                "You are a compliance auditor.\n"
                f"Requirement: {requirement}\n"
                "Context:\n"
                f"{context}\n\n"
                "Return JSON with keys: status (satisfied|unsatisfied|partial|unknown), reason.\n"
                "Use [ref-N] citations in reason."
            )

            resp = await llm_router.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                json_mode=True,
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
            content = content.replace("```json", "").replace("```", "").strip()
            data = json.loads(content) if content else {}

            status = str(data.get("status") or "unknown").strip().lower()
            if status not in {"satisfied", "unsatisfied", "partial", "unknown"}:
                status = "unknown"
            reason = str(data.get("reason") or "Unable to determine from retrieved evidence.").strip()

            refs = re.findall(r"\[ref-(\d+)\]", reason)
            active_refs: List[TextChunk] = []
            for ref in sorted(set(refs)):
                idx = int(ref) - 1
                if 0 <= idx < len(chunks):
                    active_refs.append(chunks[idx])

            return {
                "requirement": requirement,
                "status": status,
                "response": reason,
                "references": active_refs,
            }
        except Exception as exc:
            return {
                "requirement": requirement,
                "status": "error",
                "response": f"Check failed: {exc}",
                "references": [],
            }

    async def verify_requirements_v2(
        self,
        doc_id: str,
        requirements: List[str],
        policy_set_id: str = "contracts/base_rules",
        allowed_pages: Optional[List[int]] = None,
        api_key: Optional[str] = None,
        review_required: bool = True,
        doc: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Contract compliance v2:
        field extraction + rule evaluation + evidence + review state.
        """

        requirements = [str(item).strip() for item in (requirements or []) if str(item).strip()]
        if not requirements:
            review_state = review_service.create_initial_state(review_required)
            payload = ComplianceV2Response(
                decision="needs_review",
                confidence=0.0,
                risk_level="high",
                summary="No requirements provided.",
                field_results=[],
                rule_results=[],
                evidence=[],
                review_state=review_state,
                requirements=[],
                allowed_pages=list(allowed_pages or []),
                policy_set_id=policy_set_id,
                markdown="",
                created_at=_now_iso_utc(),
            )
            return payload.model_dump()

        layout = layout_service.summarize_document(doc or {}, allowed_pages=allowed_pages)
        field_records = await field_extractor.extract_contract_fields(
            doc_id=doc_id,
            requirements=requirements,
            allowed_pages=allowed_pages,
            api_key=api_key,
        )
        evidence_items, field_records = evidence_service.build_from_field_records(field_records)
        rule_results = rule_engine.evaluate(field_records, policy_set_id=policy_set_id)

        decision, risk_level, confidence = self._synthesize_decision(field_records, rule_results)
        summary = await self._build_summary_with_llm(
            requirements=requirements,
            field_records=field_records,
            rule_results=rule_results,
            layout=layout,
            api_key=api_key,
        )
        review_state = review_service.create_initial_state(review_required)
        markdown = self._format_v2_markdown(field_records, rule_results, decision, risk_level, confidence)

        public_fields = [
            ComplianceFieldResult(
                field_key=str(item.get("field_key") or ""),
                field_name=str(item.get("field_name") or ""),
                requirement=str(item.get("requirement") or ""),
                value=str(item.get("value") or ""),
                confidence=float(item.get("confidence") or 0.0),
                status=str(item.get("status") or "uncertain"),
                evidence_refs=list(item.get("evidence_refs") or []),
            )
            for item in field_records
        ]

        response = ComplianceV2Response(
            decision=decision,
            confidence=round(confidence, 3),
            risk_level=risk_level,
            summary=summary,
            field_results=public_fields,
            rule_results=rule_results,
            evidence=evidence_items,
            review_state=review_state,
            requirements=requirements,
            allowed_pages=list(allowed_pages or []),
            policy_set_id=policy_set_id,
            markdown=markdown,
            created_at=_now_iso_utc(),
        )
        return response.model_dump()

    def _synthesize_decision(
        self,
        field_records: List[Dict[str, Any]],
        rule_results: List[ComplianceRuleResult],
    ) -> tuple[str, str, float]:
        fail_count = sum(1 for item in rule_results if item.status == "fail")
        warn_count = sum(1 for item in rule_results if item.status == "warn")
        matched = sum(1 for item in field_records if item.get("status") == "matched")
        total = max(len(field_records), 1)
        coverage = matched / total
        avg_confidence = sum(float(item.get("confidence") or 0.0) for item in field_records) / total

        if fail_count > 0:
            return "fail", "high", max(0.25, min(avg_confidence * 0.8, 0.9))
        if warn_count > 0 or coverage < 0.8:
            return "needs_review", "medium", max(0.35, min(avg_confidence, 0.92))
        return "pass", "low", max(0.5, min(avg_confidence + 0.05, 0.98))

    async def _build_summary_with_llm(
        self,
        requirements: List[str],
        field_records: List[Dict[str, Any]],
        rule_results: List[ComplianceRuleResult],
        layout: Dict[str, Any],
        api_key: Optional[str],
    ) -> str:
        fields_text = "\n".join(
            [
                f"- {item.get('field_name')}: value={item.get('value')}, status={item.get('status')}, confidence={item.get('confidence')}"
                for item in field_records
            ]
        )
        rules_text = "\n".join(
            [f"- {rule.rule_name}: {rule.status} ({rule.message})" for rule in rule_results]
        )
        prompt = (
            "Summarize the contract compliance result in 3 bullet points.\n"
            "Mention key risks and missing evidence if present.\n"
            f"Requirements:\n{chr(10).join(requirements)}\n\n"
            f"Field extraction:\n{fields_text}\n\n"
            f"Rule results:\n{rules_text}\n\n"
            f"Layout summary: {layout}\n"
        )
        try:
            resp = await llm_router.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                api_key=api_key,
                json_mode=False,
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
            content = str(content or "").strip()
            if content:
                return content
        except Exception:
            pass

        failed = [rule.rule_name for rule in rule_results if rule.status == "fail"]
        warned = [rule.rule_name for rule in rule_results if rule.status == "warn"]
        return (
            f"Contract analyzed with {len(field_records)} extracted fields; "
            f"failed rules: {failed or 'none'}, warning rules: {warned or 'none'}."
        )

    def _format_v2_markdown(
        self,
        field_records: List[Dict[str, Any]],
        rule_results: List[ComplianceRuleResult],
        decision: str,
        risk_level: str,
        confidence: float,
    ) -> str:
        lines = [
            f"### Decision: {decision}",
            f"- Risk Level: {risk_level}",
            f"- Confidence: {confidence:.2f}",
            "",
            "### Field Results",
            "| Field | Value | Status | Confidence | Evidence |",
            "|---|---|---|---:|---|",
        ]
        for item in field_records:
            field_name = str(item.get("field_name") or "").replace("|", "\\|")
            value = str(item.get("value") or "").replace("|", "\\|")
            status = str(item.get("status") or "uncertain")
            conf = float(item.get("confidence") or 0.0)
            refs = ", ".join(item.get("evidence_refs") or [])
            lines.append(f"| {field_name} | {value} | {status} | {conf:.2f} | {refs} |")

        lines.extend(["", "### Rule Results", "| Rule | Status | Message |", "|---|---|---|"])
        for rule in rule_results:
            rule_name = rule.rule_name.replace("|", "\\|")
            rule_message = rule.message.replace("|", "\\|")
            lines.append(
                f"| {rule_name} | {rule.status} | {rule_message} |"
            )

        return "\n".join(lines)

    def submit_review(
        self,
        decision: str,
        reviewer: Optional[str] = None,
        note: Optional[str] = None,
    ) -> ReviewState:
        return review_service.submit(decision=decision, reviewer=reviewer, note=note)


compliance_service = ComplianceService()
