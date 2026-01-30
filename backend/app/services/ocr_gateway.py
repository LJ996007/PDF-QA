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
        使用GLM-4V处理图片，提取文本和坐标
        """
        # 优先使用传入的key，否则使用环境变量
        final_api_key = api_key or self.api_key
        
        if not final_api_key:
            # 如果没有key，返回空列表而不是报错，避免阻断流程
             print("Warning: No Zhipu API Key provided for OCR")
             return []

        # 优化1：压缩图片以节省Input Token
        try:
            from PIL import Image
            import io
            
            # 解码图片
            img_data = base64.b64decode(image_base64)
            img = Image.open(io.BytesIO(img_data))
            
            # 如果图片长边超过1024px，进行压缩
            max_size = 1024
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                
                # 重新编码为base64
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                print(f"[OCR Optimization] Resized image to {new_size}, saved input tokens.")
        except Exception as e:
            print(f"Image resize failed, using original: {e}")
        
        # 优化2：精简输出格式以节省Output Token
        # 使用自定义分隔符格式：段落文本|位置
        # 节省了JSON的大量结构Token
        prompt = """请识别图中文本，按段落返回。每行一条，格式为：
内容|位置
其中位置只能是：top-left, top-center, top-right, middle-left, middle-center, middle-right, bottom-left, bottom-center, bottom-right
例如：
这是第一段话|top-left
这是第二段话|middle-center
不要包含任何其他内容或Markdown标记。"""
        
        headers = {
            "Authorization": f"Bearer {final_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "max_tokens": 4096
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
            
            # 解析返回的内容
            content = result["choices"][0]["message"]["content"]
            return self._parse_ocr_result(content, page_number, page_width, page_height)
        except Exception as e:
            print(f"OCR execution failed: {e}")
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
