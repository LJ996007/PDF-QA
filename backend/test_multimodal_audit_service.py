from datetime import date, timedelta

from app.services.multimodal_audit_service import MultimodalAuditService


def test_strict_name_match_uses_nfkc_and_space_fold():
    service = MultimodalAuditService()
    assert service.is_same_name_strict("投 标 人（中国）有限公司", "投 标 人（中国）有限公司")
    assert service.is_same_name_strict("ＡＢＣ  公司", "ABC 公司")
    assert not service.is_same_name_strict("投标人有限公司", "投标人有限责任公司")


def test_certificate_rule_marks_holder_mismatch_as_fail():
    service = MultimodalAuditService()
    future_day = (date.today() + timedelta(days=30)).isoformat()
    facts = [
        {"page": 1, "key": "certificate_name", "value": "安全生产许可证", "evidence_text": "安全生产许可证", "confidence": 0.9},
        {"page": 1, "key": "certificate_valid_until", "value": future_day, "evidence_text": future_day, "confidence": 0.9},
        {"page": 1, "key": "certificate_holder_org", "value": "甲公司", "evidence_text": "甲公司", "confidence": 0.9},
    ]
    items = service._apply_rule_engine(
        audit_type="certificate",
        facts=facts,
        bidder_name="乙公司",
        custom_checks=[],
        custom_results=[],
    )
    holder_item = next(item for item in items if item["check_key"] == "certificate_holder_org")
    assert holder_item["status"] == "fail"


def test_fallback_bbox_has_positive_size():
    service = MultimodalAuditService()
    bbox = service._build_fallback_bbox(page=3, width=595, height=842)
    assert bbox.page == 3
    assert bbox.w > 0
    assert bbox.h > 0
