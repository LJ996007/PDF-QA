"""
PDF解析服务 - 使用PyMuPDF提取文本和坐标
"""
import fitz  # PyMuPDF
import base64
import io
import re
from typing import List, Tuple
from pathlib import Path
from PIL import Image

from app.models.schemas import PageContent, BoundingBox


def has_garbled_text(text: str) -> bool:
    """检测是否包含乱码（大量特殊字符）"""
    if not text:
        return True
    # 计算可识别字符比例
    readable = sum(1 for c in text if c.isalnum() or c.isspace() or ord(c) > 0x4E00)
    return readable / len(text) < 0.5 if text else True


def extract_text_with_coordinates(page: fitz.Page) -> Tuple[str, List[BoundingBox]]:
    """
    从PDF页面提取文本及其坐标
    返回: (完整文本, 坐标列表)

    注意：返回的坐标是行级别的，与文本按换行分割后一一对应
    """
    text_dict = page.get_text("dict")
    full_text = []
    coordinates = []
    page_height = page.rect.height

    for block_idx, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") != 0:  # 0 = 文本块
            continue

        for line in block.get("lines", []):
            line_text = ""
            line_bbox = None

            for span in line.get("spans", []):
                span_text = span.get("text", "")
                if span_text.strip():
                    line_text += span_text

                    # 合并行内所有span的bbox
                    span_bbox = span.get("bbox", [0, 0, 0, 0])
                    if line_bbox is None:
                        line_bbox = list(span_bbox)
                    else:
                        # 扩展bbox：取最小x0, 最小y0, 最大x1, 最大y1
                        line_bbox[0] = min(line_bbox[0], span_bbox[0])
                        line_bbox[1] = min(line_bbox[1], span_bbox[1])
                        line_bbox[2] = max(line_bbox[2], span_bbox[2])
                        line_bbox[3] = max(line_bbox[3], span_bbox[3])

            if line_text and line_bbox:
                full_text.append(line_text)
                # 保存行级别的坐标
                coordinates.append(BoundingBox(
                    page=0,  # 后续填充
                    x=line_bbox[0],
                    y=line_bbox[1],  # 保持Top-Left坐标系，与OCR和前端统一
                    w=line_bbox[2] - line_bbox[0],
                    h=line_bbox[3] - line_bbox[1]
                ))

    return "\n".join(full_text), coordinates


def render_page_to_image(page: fitz.Page, dpi: int = 150) -> str:
    """
    将PDF页面渲染为Base64图片
    """
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    
    # 转换为PIL Image再转Base64
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    return base64.b64encode(buffer.read()).decode("utf-8")


def generate_thumbnail(page: fitz.Page, size: int = 200) -> str:
    """生成页面缩略图"""
    # 计算缩放比例
    scale = size / max(page.rect.width, page.rect.height)
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat)
    
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buffer = io.BytesIO()
    img.save(buffer, format="WEBP", quality=80)
    buffer.seek(0)
    
    return base64.b64encode(buffer.read()).decode("utf-8")


def process_page(page: fitz.Page, page_number: int) -> PageContent:
    """
    处理单页PDF，智能决定使用原生文本还是OCR
    
    决策逻辑：
    1. 先提取原生文本（保留坐标）
    2. 检查文本密度和完整性
    3. 若文本<100字符或包含大量乱码 -> 标记为需要OCR
    """
    text, coordinates = extract_text_with_coordinates(page)
    char_count = len(text.replace(" ", "").replace("\n", ""))
    
    if char_count > 100 and not has_garbled_text(text):
        # 原生文本足够，直接使用
        # 更新坐标的页码
        for coord in coordinates:
            coord.page = page_number
        
        return PageContent(
            page_number=page_number,
            type="native",
            text=text,
            coordinates=coordinates,
            confidence=1.0
        )
    else:
        # 需要OCR处理
        image_base64 = render_page_to_image(page)
        return PageContent(
            page_number=page_number,
            type="ocr",
            text="",
            coordinates=None,
            confidence=0.0,
            image_base64=image_base64
        )


def process_document(pdf_path: str) -> Tuple[List[PageContent], List[str]]:
    """
    处理整个PDF文档
    返回: (页面内容列表, 缩略图列表)
    """
    doc = fitz.open(pdf_path)
    pages = []
    thumbnails = []
    
    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # 处理页面内容
            page_content = process_page(page, page_num + 1)
            pages.append(page_content)
            
            # 生成缩略图
            thumbnail = generate_thumbnail(page)
            thumbnails.append(f"data:image/webp;base64,{thumbnail}")
    finally:
        doc.close()
    
    return pages, thumbnails


def get_ocr_required_pages(pages: List[PageContent]) -> List[int]:
    """获取需要OCR的页码列表"""
    return [p.page_number for p in pages if p.type == "ocr"]
