"""Rule engine for contract compliance v2."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from app.models.schemas import ComplianceRuleResult

try:
    import yaml
except Exception:  # pragma: no cover - fallback used when dependency missing
    yaml = None


DATE_PATTERN = re.compile(r"(?:19|20)\d{2}[年\-/\.]\d{1,2}[月\-/\.]\d{1,2}日?")
AMOUNT_PATTERN = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?\s*(?:元|万元|亿元)")


class RuleEngine:
    def __init__(self) -> None:
        self._policy_cache: Dict[str, Dict] = {}
        self._default_policy = {
            "policy_id": "contracts/base_rules",
            "rules": [
                {"id": "required_fields", "name": "关键字段存在性", "type": "required_fields", "fields": ["party", "amount", "date", "term"]},
                {"id": "amount_format", "name": "金额格式有效性", "type": "amount_format", "fields": ["amount"]},
                {"id": "date_format", "name": "日期格式有效性", "type": "date_format", "fields": ["date"]},
                {"id": "term_consistency", "name": "期限字段完整性", "type": "term_consistency", "fields": ["term"]},
                {"id": "evidence_completeness", "name": "证据完整性", "type": "evidence_completeness", "fields": []},
            ],
        }

    def _policy_path(self, policy_set_id: str) -> Path:
        normalized = (policy_set_id or "contracts/base_rules").strip().replace("\\", "/")
        if normalized.endswith(".yaml"):
            rel_path = normalized
        elif normalized.startswith("contracts/"):
            rel_path = f"{normalized}.yaml"
        else:
            rel_path = f"contracts/{normalized}.yaml"
        return Path(__file__).resolve().parents[2] / "policies" / rel_path

    def _load_policy(self, policy_set_id: str) -> Dict:
        normalized = (policy_set_id or "contracts/base_rules").strip()
        if normalized in self._policy_cache:
            return self._policy_cache[normalized]

        path = self._policy_path(normalized)
        if path.exists() and yaml is not None:
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict) and isinstance(loaded.get("rules"), list):
                    self._policy_cache[normalized] = loaded
                    return loaded
            except Exception:
                pass

        self._policy_cache[normalized] = self._default_policy
        return self._default_policy

    def _records_by_key(self, field_records: List[Dict]) -> Dict[str, List[Dict]]:
        out: Dict[str, List[Dict]] = {}
        for record in field_records:
            key = str(record.get("field_key") or "").strip()
            if not key:
                continue
            out.setdefault(key, []).append(record)
        return out

    def _has_valid_value(self, records: List[Dict]) -> bool:
        for record in records:
            if str(record.get("value") or "").strip():
                return True
        return False

    def evaluate(self, field_records: List[Dict], policy_set_id: str = "contracts/base_rules") -> List[ComplianceRuleResult]:
        policy = self._load_policy(policy_set_id)
        grouped = self._records_by_key(field_records)

        results: List[ComplianceRuleResult] = []
        for rule in policy.get("rules", []):
            rule_id = str(rule.get("id") or "rule")
            rule_name = str(rule.get("name") or rule_id)
            rule_type = str(rule.get("type") or "")
            fields = [str(x) for x in (rule.get("fields") or []) if str(x).strip()]

            status = "pass"
            message = "规则通过"

            if rule_type == "required_fields":
                missing = [field for field in fields if not self._has_valid_value(grouped.get(field, []))]
                if missing:
                    status = "fail"
                    message = f"缺少关键字段: {', '.join(missing)}"
            elif rule_type == "amount_format":
                amount_values = [str(r.get("value") or "") for r in grouped.get("amount", []) if str(r.get("value") or "").strip()]
                if amount_values and not any(AMOUNT_PATTERN.search(v) for v in amount_values):
                    status = "warn"
                    message = "金额字段存在但格式可疑"
            elif rule_type == "date_format":
                date_values = [str(r.get("value") or "") for r in grouped.get("date", []) if str(r.get("value") or "").strip()]
                if date_values and not any(DATE_PATTERN.search(v) for v in date_values):
                    status = "warn"
                    message = "日期字段存在但格式可疑"
            elif rule_type == "term_consistency":
                terms = [str(r.get("value") or "") for r in grouped.get("term", []) if str(r.get("value") or "").strip()]
                if not terms:
                    status = "warn"
                    message = "未识别有效期限字段"
            elif rule_type == "evidence_completeness":
                no_evidence = [
                    r.get("field_name") or r.get("field_key")
                    for r in field_records
                    if not list(r.get("evidence_refs") or [])
                ]
                if no_evidence:
                    status = "warn"
                    message = f"以下字段缺少证据: {', '.join(str(v) for v in no_evidence)}"

            results.append(
                ComplianceRuleResult(
                    rule_id=rule_id,
                    rule_name=rule_name,
                    status=status,  # type: ignore[arg-type]
                    message=message,
                    field_names=fields,
                )
            )

        return results


rule_engine = RuleEngine()
