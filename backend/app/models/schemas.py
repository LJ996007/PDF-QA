"""
Pydantic data models.
"""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """PDF coordinate bounding box."""

    page: int = Field(..., description="Page number (1-indexed)")
    x: float = Field(..., description="Left x")
    y: float = Field(..., description="Top y")
    w: float = Field(..., description="Width")
    h: float = Field(..., description="Height")


class PageContent(BaseModel):
    """Page content."""

    page_number: int
    type: Literal["native", "ocr"] = Field(..., description="native text or OCR")
    text: str = ""
    coordinates: Optional[List[BoundingBox]] = None
    confidence: float = 1.0
    image_base64: Optional[str] = None


class TextChunk(BaseModel):
    """Chunk for retrieval."""

    id: str
    document_id: str
    page_number: int
    content: str
    bbox: BoundingBox
    source_type: Literal["native", "ocr"]
    distance: Optional[float] = None
    ref_id: Optional[str] = None
    block_id: Optional[str] = None


class Document(BaseModel):
    """Document metadata."""

    id: str
    name: str
    total_pages: int
    upload_time: datetime
    processing_status: Literal["extracting", "embedding", "completed", "failed"]
    ocr_required_pages: List[int] = Field(default_factory=list)
    recognized_pages: List[int] = Field(default_factory=list)
    page_ocr_status: Dict[int, Literal["unrecognized", "processing", "recognized", "failed"]] = Field(default_factory=dict)
    ocr_mode: Literal["manual", "full"] = "manual"
    thumbnail_urls: List[str] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    """Upload response."""

    document_id: str
    status: str
    total_pages: int
    ocr_required_pages: List[int]
    progress_url: str
    ocr_mode: Literal["manual", "full"] = "manual"


class OCRRequest(BaseModel):
    """OCR request."""

    page_number: int


class OCRChunk(BaseModel):
    """OCR chunk."""

    text: str
    bbox: BoundingBox


class OCRResponse(BaseModel):
    """OCR response."""

    page: int
    chunks: List[OCRChunk]
    status: Literal["recognized", "already_recognized", "processing"] = "recognized"
    already_recognized: bool = False
    message: Optional[str] = None


class ChatRequest(BaseModel):
    """Chat request."""

    document_id: str
    question: str
    history: List[dict] = Field(default_factory=list)
    zhipu_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    allowed_pages: List[int] = Field(default_factory=list)


class ChatReference(BaseModel):
    """Chat citation."""

    ref_id: str
    chunk_id: str
    page: int
    bbox: BoundingBox
    content: str


class ChatMessage(BaseModel):
    """Chat message."""

    id: str
    document_id: str
    role: Literal["user", "assistant"]
    content: str
    references: List[ChatReference] = Field(default_factory=list)
    timestamp: datetime


class ProgressEvent(BaseModel):
    """Progress event for SSE."""

    stage: Literal["extracting", "embedding", "ocr", "completed", "failed"]
    current: int
    total: int
    message: Optional[str] = None
    document_id: Optional[str] = None


class ComplianceV2Request(BaseModel):
    """Request payload for contract compliance v2."""

    requirements: List[str] = Field(default_factory=list)
    policy_set_id: str = "contracts/base_rules"
    allowed_pages: List[int] = Field(default_factory=list)
    api_key: Optional[str] = None
    review_required: bool = True


class EvidenceItem(BaseModel):
    """Evidence record returned by compliance v2."""

    ref_id: str
    page: int
    bbox: BoundingBox
    source_type: Literal["native", "ocr", "derived"] = "native"
    field_name: str
    support_level: Literal["primary", "secondary"] = "primary"
    content: str = ""


class ComplianceFieldResult(BaseModel):
    """Extracted field result for compliance v2."""

    field_key: str
    field_name: str
    requirement: str
    value: str = ""
    confidence: float = 0.0
    status: Literal["matched", "missing", "uncertain"] = "uncertain"
    evidence_refs: List[str] = Field(default_factory=list)


class ComplianceRuleResult(BaseModel):
    """Rule evaluation result."""

    rule_id: str
    rule_name: str
    status: Literal["pass", "fail", "warn"] = "warn"
    message: str
    field_names: List[str] = Field(default_factory=list)


class ReviewState(BaseModel):
    """Human review state for a compliance result."""

    state: Literal["pending_review", "approved", "rejected"] = "pending_review"
    reviewer: Optional[str] = None
    note: Optional[str] = None
    updated_at: Optional[str] = None


class ComplianceV2Response(BaseModel):
    """Response payload for contract compliance v2."""

    decision: Literal["pass", "fail", "needs_review"] = "needs_review"
    confidence: float = 0.0
    risk_level: Literal["low", "medium", "high"] = "high"
    summary: str = ""
    field_results: List[ComplianceFieldResult] = Field(default_factory=list)
    rule_results: List[ComplianceRuleResult] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    review_state: ReviewState = Field(default_factory=ReviewState)
    requirements: List[str] = Field(default_factory=list)
    allowed_pages: List[int] = Field(default_factory=list)
    policy_set_id: str = "contracts/base_rules"
    markdown: str = ""
    created_at: Optional[str] = None
