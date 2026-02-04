"""数据模型定义"""
from pydantic import BaseModel
from typing import List, Optional


class Paragraph(BaseModel):
    """段落信息"""
    id: str              # 唯一标识，如 "p1_para3" (第1页第3段)
    page_number: int     # 页码(从1开始)
    text: str            # 段落文本内容
    bbox: dict           # 位置坐标 {"x0": 0, "y0": 0, "x1": 100, "y1": 50}
    page_width: Optional[float] = None   # 页面宽度
    page_height: Optional[float] = None  # 页面高度


class Document(BaseModel):
    """PDF文档信息"""
    id: str              # 文档ID
    filename: str        # 文件名
    total_pages: int     # 总页数
    paragraph_count: int # 段落总数


class UploadResponse(BaseModel):
    """上传响应"""
    document_id: str
    filename: str
    total_pages: int
    paragraph_count: int
    message: str


class AskRequest(BaseModel):
    """问答请求"""
    document_id: str
    question: str


class Reference(BaseModel):
    """引用信息"""
    id: str
    page: int
    text: str
    bbox: dict
    page_width: Optional[float] = None
    page_height: Optional[float] = None


class AskResponse(BaseModel):
    """问答响应"""
    answer: str
    references: List[Reference]


class LLMConfigRequest(BaseModel):
    """大模型配置请求"""
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    model: Optional[str] = None


class LLMConfigResponse(BaseModel):
    """大模型配置响应"""
    configured: bool
    provider: str
    model: str
    api_base: str
    api_key_set: bool
    message: str
