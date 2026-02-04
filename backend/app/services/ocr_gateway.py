"""
GLM-4V OCR服务封装
"""
import httpx
import base64
from typing import List, Optional
import os
import json

from app.models.schemas import OCRChunk, BoundingBox


class OCRGateway:
    """智谱GLM-4V-Flash OCR服务"""
    
    def __init__(self):
        self.api_key = os.getenv("ZHIPU_API_KEY", "")
        self.base_url = "https://open.bigmodel.cn/api/paas/v4"
    
    async def process_image(
        self, 
        image_base64: str, 
        page_number: int,
        page_width: float,
        page_height: float,
        api_key: Optional[str] = None,
        model: str = "glm-4v-flash"
    ) -> List[OCRChunk]:
        """
        [DEPRECATED] 智谱OCR已移除
        """
        print("Warning: Zhipu OCR is deprecated and removed.")
        return []
    
    def _parse_ocr_result(
        self, 
        content: str, 
        page_number: int,
        page_width: float,
        page_height: float
    ) -> List[OCRChunk]:
        """
        解析OCR返回结果（优化版）
        使用段落顺序推断Y坐标，提高定位精度
        """
        chunks = []
        
        try:
            # 移除可能的markdown代码块标记
            content = content.replace("```text", "").replace("```", "").strip()
            
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            total_lines = len(lines)
            
            if total_lines == 0:
                return chunks
            
            # X坐标映射（仅用于左/中/右定位）
            x_position_map = {
                "left": 0.05,
                "center": 0.35,
                "right": 0.65,
            }
            
            for idx, line in enumerate(lines):
                # 解析：内容|位置
                if '|' in line:
                    parts = line.rsplit('|', 1)
                    text = parts[0].strip()
                    position = parts[1].strip().lower()
                else:
                    text = line
                    position = "middle-left"
                
                if not text:
                    continue
                
                # 优化1：Y坐标根据段落顺序自动计算
                # 第一个段落在页面顶部(0.9)，最后一个在底部(0.1)
                # 中间段落按比例均匀分布
                if total_lines == 1:
                    y_ratio = 0.5  # 单段落居中
                else:
                    # 从 0.85 (顶部) 到 0.15 (底部) 均匀分布
                    y_ratio = 0.85 - (idx / (total_lines - 1)) * 0.7
                
                # 优化2：X坐标从位置描述中提取左/中/右
                x_ratio = 0.05  # 默认左对齐
                for pos_key, x_val in x_position_map.items():
                    if pos_key in position:
                        x_ratio = x_val
                        break
                
                # 估算文本框大小
                text_len = len(text)
                estimated_width = min(page_width * 0.85, text_len * 12)
                # 根据文本长度估算行数，每行约40字符
                line_count = max(1, (text_len + 39) // 40)
                estimated_height = line_count * 20
                
                chunks.append(OCRChunk(
                    text=text,
                    bbox=BoundingBox(
                        page=page_number,
                        x=x_ratio * page_width,
                        y=y_ratio * page_height,
                        w=estimated_width,
                        h=estimated_height
                    )
                ))

        except Exception as e:
            print(f"[OCR Parse Error] {e}")
            # 如果解析失败，尝试将整个内容作为一个块
            if content.strip():
                chunks.append(OCRChunk(
                    text=content,
                    bbox=BoundingBox(
                        page=page_number,
                        x=page_width * 0.1,
                        y=page_height * 0.5,
                        w=page_width * 0.8,
                        h=page_height * 0.3
                    )
                ))
        
        return chunks


# 全局实例
ocr_gateway = OCRGateway()
