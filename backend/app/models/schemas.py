"""
Pydantic数据模型定义
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime


class BoundingBox(BaseModel):
    """PDF坐标边界框"""
    page: int = Field(..., description="页码（1-indexed）")
    x: float = Field(..., description="左下角x坐标")
    y: float = Field(..., description="左下角y坐标")
    w: float = Field(..., description="宽度")
    h: float = Field(..., description="高度")


class PageContent(BaseModel):
    """页面内容"""
    page_number: int
    type: Literal["native", "ocr"] = Field(..., description="内容类型：原生文本或OCR")
    text: str = ""
    coordinates: Optional[List[BoundingBox]] = None
    confidence: float = 1.0
    image_base64: Optional[str] = None


class TextChunk(BaseModel):
    """文本块（用于RAG检索）"""
    id: str
    document_id: str
    page_number: int
    content: str
    bbox: BoundingBox
    source_type: Literal["native", "ocr"]
    distance: Optional[float] = None
    ref_id: Optional[str] = None
    block_id: Optional[str] = None # Unique block ID for citation (e.g., b0001)


class Document(BaseModel):
    """文档信息"""
    id: str
    name: str
    total_pages: int
    upload_time: datetime
    processing_status: Literal["extracting", "embedding", "completed", "failed"]
    ocr_required_pages: List[int] = []
    thumbnail_urls: List[str] = []


class DocumentUploadResponse(BaseModel):
    """文档上传响应"""
    document_id: str
    status: str
    total_pages: int
    ocr_required_pages: List[int]
    progress_url: str


class OCRRequest(BaseModel):
    """OCR请求"""
    page_number: int


class OCRChunk(BaseModel):
    """OCR结果块"""
    text: str
    bbox: BoundingBox


class OCRResponse(BaseModel):
    """OCR响应"""
    page: int
    chunks: List[OCRChunk]


class ChatRequest(BaseModel):
    """对话请求"""
    document_id: str
    question: str
    history: List[dict] = []
    zhipu_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None


class ChatReference(BaseModel):
    """对话引用"""
    ref_id: str
    chunk_id: str
    page: int
    bbox: BoundingBox
    content: str


class ChatMessage(BaseModel):
    """对话消息"""
    id: str
    document_id: str
    role: Literal["user", "assistant"]
    content: str
    references: List[ChatReference] = []
    timestamp: datetime


class ProgressEvent(BaseModel):
    """进度事件"""
    stage: Literal["extracting", "embedding", "ocr", "completed", "failed"]
    current: int
    total: int
    message: Optional[str] = None
    document_id: Optional[str] = None
