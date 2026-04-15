"""Shared multimodal audit profile management."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.document_store import document_store


class AuditProfileService:
    """CRUD helpers for shared editable audit profiles."""

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _default_profiles_payload(self) -> Dict[str, Any]:
        now = self._now_iso()
        return {
            "version": 1,
            "profiles": [
                {
                    "id": "contract",
                    "name": "合同扫描件审核",
                    "bidder_name_required": False,
                    "rules": [
                        {
                            "id": "contract_main_content",
                            "title": "合同主要内容完整性",
                            "instruction": "检查合同主要内容是否清晰可辨识，是否能直接识别出关键合同条款或主要内容。",
                            "enabled": True,
                        },
                        {
                            "id": "contract_sign_date",
                            "title": "签订日期识别",
                            "instruction": "检查是否识别到明确的签订日期；若识别到多个日期，优先输出最能代表合同签订日期的结果。",
                            "enabled": True,
                        },
                        {
                            "id": "contract_party_a_stamp",
                            "title": "甲方盖章核验",
                            "instruction": "检查甲方是否已盖章、签章或加盖单位印章；若无法确认，标记为需复核。",
                            "enabled": True,
                        },
                        {
                            "id": "contract_party_b_stamp",
                            "title": "乙方盖章核验",
                            "instruction": "检查乙方是否已盖章、签章或加盖单位印章；若无法确认，标记为需复核。",
                            "enabled": True,
                        },
                    ],
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "certificate",
                    "name": "证件扫描件审核",
                    "bidder_name_required": True,
                    "rules": [
                        {
                            "id": "certificate_name",
                            "title": "证书名称识别",
                            "instruction": "检查是否识别到明确的证书、资质证件或许可证名称。",
                            "enabled": True,
                        },
                        {
                            "id": "certificate_valid_until",
                            "title": "证书有效期核验",
                            "instruction": "检查证书有效期是否可识别，并判断相对于当前日期是否已过期；若文本模糊无法判断则标记为需复核。",
                            "enabled": True,
                        },
                        {
                            "id": "certificate_holder_org",
                            "title": "证书获取单位与投标人一致性",
                            "instruction": "检查证书获取单位、持证单位或所属单位是否与投标人名称完全一致；若不一致应标记为不通过。",
                            "enabled": True,
                        },
                    ],
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "personnel",
                    "name": "人员资质审核",
                    "bidder_name_required": True,
                    "rules": [
                        {
                            "id": "person_qualification",
                            "title": "人员资质信息完整性",
                            "instruction": "检查是否识别到人员姓名和对应资质名称，两者任一缺失都应标记为需复核。",
                            "enabled": True,
                        },
                        {
                            "id": "qualification_valid_until",
                            "title": "人员资质有效期核验",
                            "instruction": "检查人员资质是否存在有效期，并判断相对于当前日期是否已过期；若无法明确判断则标记为需复核。",
                            "enabled": True,
                        },
                        {
                            "id": "qualification_org",
                            "title": "人员资质单位与投标人一致性",
                            "instruction": "检查人员资质所属单位、注册单位或聘用单位是否与投标人名称完全一致；若不一致应标记为不通过。",
                            "enabled": True,
                        },
                    ],
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        }

    def _normalize_rule(self, raw_rule: Any, index: int) -> Dict[str, Any]:
        if not isinstance(raw_rule, dict):
            raise ValueError("审核项格式不正确。")

        rule_id = str(raw_rule.get("id") or f"rule_{index + 1:02d}").strip()
        title = str(raw_rule.get("title") or "").strip()
        instruction = str(raw_rule.get("instruction") or "").strip()
        enabled = bool(raw_rule.get("enabled", True))

        if not rule_id:
            raise ValueError("审核项 ID 不能为空。")
        if not title:
            raise ValueError("审核项标题不能为空。")
        if not instruction:
            raise ValueError(f"审核项“{title}”的审核说明不能为空。")

        return {
            "id": rule_id,
            "title": title,
            "instruction": instruction,
            "enabled": enabled,
        }

    def _normalize_profile(self, raw_profile: Any, *, fallback_id: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(raw_profile, dict):
            raise ValueError("审核模板格式不正确。")

        profile_id = str(raw_profile.get("id") or fallback_id or f"audit_{uuid.uuid4().hex[:12]}").strip()
        name = str(raw_profile.get("name") or "").strip()
        bidder_name_required = bool(raw_profile.get("bidder_name_required", False))
        rules_raw = raw_profile.get("rules") or []
        created_at = str(raw_profile.get("created_at") or self._now_iso()).strip() or self._now_iso()
        updated_at = str(raw_profile.get("updated_at") or self._now_iso()).strip() or self._now_iso()

        if not profile_id:
            raise ValueError("审核模板 ID 不能为空。")
        if not name:
            raise ValueError("审核模板名称不能为空。")
        if not isinstance(rules_raw, list) or not rules_raw:
            raise ValueError(f"审核模板“{name}”至少需要一条审核项。")

        rules = [self._normalize_rule(rule, index) for index, rule in enumerate(rules_raw)]
        if not any(rule["enabled"] for rule in rules):
            raise ValueError(f"审核模板“{name}”至少需要启用一条审核项。")

        return {
            "id": profile_id,
            "name": name,
            "bidder_name_required": bidder_name_required,
            "rules": rules,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _load_or_seed(self) -> Dict[str, Any]:
        payload = document_store.load_audit_profiles()
        if not payload:
            payload = self._default_profiles_payload()
            document_store.save_audit_profiles(payload)
            return payload

        profiles_raw = payload.get("profiles")
        if not isinstance(profiles_raw, list) or not profiles_raw:
            payload = self._default_profiles_payload()
            document_store.save_audit_profiles(payload)
            return payload

        normalized_profiles: List[Dict[str, Any]] = []
        changed = False
        seen_ids: set[str] = set()
        for index, raw_profile in enumerate(profiles_raw):
            try:
                normalized = self._normalize_profile(raw_profile)
            except ValueError:
                changed = True
                continue
            if normalized["id"] in seen_ids:
                normalized["id"] = f"{normalized['id']}_{index + 1}"
                changed = True
            seen_ids.add(normalized["id"])
            if normalized != raw_profile:
                changed = True
            normalized_profiles.append(normalized)

        if not normalized_profiles:
            payload = self._default_profiles_payload()
            document_store.save_audit_profiles(payload)
            return payload

        normalized_payload = {
            "version": 1,
            "profiles": normalized_profiles,
        }
        if changed or payload.get("version") != 1:
            document_store.save_audit_profiles(normalized_payload)
        return normalized_payload

    def list_profiles(self) -> List[Dict[str, Any]]:
        payload = self._load_or_seed()
        profiles = payload.get("profiles") or []
        return copy.deepcopy(profiles)

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        profile_id = (profile_id or "").strip()
        if not profile_id:
            return None
        for profile in self.list_profiles():
            if profile.get("id") == profile_id:
                return profile
        return None

    def create_profile(self, raw_profile: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._load_or_seed()
        profiles = payload.get("profiles") or []
        created = self._normalize_profile(
            {
                **raw_profile,
                "id": raw_profile.get("id") or f"audit_{uuid.uuid4().hex[:12]}",
                "created_at": self._now_iso(),
                "updated_at": self._now_iso(),
            }
        )
        if any(profile.get("id") == created["id"] for profile in profiles):
            created["id"] = f"audit_{uuid.uuid4().hex[:12]}"

        profiles.append(created)
        payload["profiles"] = profiles
        document_store.save_audit_profiles(payload)
        return copy.deepcopy(created)

    def update_profile(self, profile_id: str, raw_profile: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._load_or_seed()
        profiles = payload.get("profiles") or []

        for index, existing in enumerate(profiles):
            if existing.get("id") != profile_id:
                continue

            updated = self._normalize_profile(
                {
                    **raw_profile,
                    "id": profile_id,
                    "created_at": existing.get("created_at") or self._now_iso(),
                    "updated_at": self._now_iso(),
                },
                fallback_id=profile_id,
            )
            profiles[index] = updated
            payload["profiles"] = profiles
            document_store.save_audit_profiles(payload)
            return copy.deepcopy(updated)

        raise KeyError(profile_id)

    def delete_profile(self, profile_id: str) -> None:
        payload = self._load_or_seed()
        profiles = payload.get("profiles") or []
        if len(profiles) <= 1:
            raise ValueError("至少保留一个审核模板。")

        remaining = [profile for profile in profiles if profile.get("id") != profile_id]
        if len(remaining) == len(profiles):
            raise KeyError(profile_id)

        payload["profiles"] = remaining
        document_store.save_audit_profiles(payload)


audit_profile_service = AuditProfileService()
