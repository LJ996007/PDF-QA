"""PDF解析服务 - 提取文本、切分段落、记录位置"""
import os
import fitz  # PyMuPDF
from typing import List, Dict, Tuple
import uuid
from app.models.schemas import Paragraph


class PDFParser:
    """PDF解析器"""

    def __init__(self, file_path: str):
        """
        初始化PDF解析器

        Args:
            file_path: PDF文件路径
        """
        self.file_path = file_path
        self.doc = fitz.open(file_path)
        self.document_id = str(uuid.uuid4())[:8]

    def get_total_pages(self) -> int:
        """获取PDF总页数"""
        return len(self.doc)

    def parse(self) -> Tuple[List[Paragraph], Dict]:
        """
        解析PDF，提取段落并记录位置

        Returns:
            (段落列表, 文档元数据)
        """
        paragraphs = []
        para_index = 0

        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            blocks = self._get_text_blocks(page)

            for block in blocks:
                # 过滤掉太短的文本块（可能是页眉页脚）
                text = block["text"].strip()
                if len(text) < 20:  # 最少20个字符才算有效段落
                    continue

                para_index += 1
                paragraph_id = f"{self.document_id}_p{page_num + 1}_para{para_index}"

                # 获取文本块的位置坐标 (bbox)
                bbox = block.get("bbox", [0, 0, 0, 0])

                paragraph = Paragraph(
                    id=paragraph_id,
                    page_number=page_num + 1,  # 页码从1开始
                    text=text,
                    bbox={
                        "x0": bbox[0],
                        "y0": bbox[1],
                        "x1": bbox[2],
                        "y1": bbox[3]
                    }
                )
                paragraphs.append(paragraph)

        # 文档元数据
        metadata = {
            "id": self.document_id,
            "total_pages": len(self.doc),
            "paragraph_count": len(paragraphs)
        }

        return paragraphs, metadata

    def _get_text_blocks(self, page: fitz.Page) -> List[Dict]:
        """
        获取页面的文本块

        处理流程：
        1. 首先尝试直接提取文本（适用于文字PDF）
        2. 如果文本量太少，尝试OCR（适用于扫描版PDF）
        """
        # 获取页面尺寸
        rect = page.rect

        # 方法1: 直接提取文本
        text_blocks = page.get_text("blocks")  # 返回文本块列表

        # 检查是否有足够的文本
        total_text = ""
        for block in text_blocks:
            total_text += block[4]  # block[4]是文本内容

        # 如果文本量太少（可能是扫描版），尝试OCR
        if len(total_text.strip()) < 50:
            text_blocks = self._ocr_page(page)

        # 格式化文本块
        formatted_blocks = []
        for block in text_blocks:
            # block格式: (x0, y0, x1, y1, text, block_no, block_type)
            if isinstance(block, tuple) and len(block) >= 5:
                formatted_blocks.append({
                    "text": block[4],
                    "bbox": [block[0], block[1], block[2], block[3]]
                })
            elif isinstance(block, dict):
                formatted_blocks.append(block)

        return formatted_blocks

    def _ocr_page(self, page: fitz.Page) -> List[Dict]:
        """
        对页面进行OCR识别（扫描版PDF）

        注意：需要安装Tesseract OCR引擎
        """
        try:
            # 尝试使用PyMuPDF的OCR功能（需要安装OCR引擎）
            # 渲染页面为图像
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")

            # 使用第三方OCR库（这里预留接口）
            # 实际使用时可以集成 pytesseract 或其他OCR服务
            # 目前先返回空列表，后续可扩展
            return []

        except Exception as e:
            print(f"OCR识别失败: {e}")
            return []

    def close(self):
        """关闭PDF文档"""
        if self.doc:
            self.doc.close()

    def __enter__(self):
        """支持with语句"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出with语句时自动关闭"""
        self.close()


def parse_pdf_file(file_path: str) -> Tuple[List[Paragraph], Dict]:
    """
    解析PDF文件的便捷函数

    Args:
        file_path: PDF文件路径

    Returns:
        (段落列表, 文档元数据)
    """
    with PDFParser(file_path) as parser:
        return parser.parse()


def is_valid_pdf(file_path: str) -> bool:
    """
    验证文件是否为有效的PDF

    Args:
        file_path: 文件路径

    Returns:
        是否为有效PDF
    """
    try:
        doc = fitz.open(file_path)
        is_valid = len(doc) > 0
        doc.close()
        return is_valid
    except Exception:
        return False
