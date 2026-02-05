"""
百度 PP-OCRv5 云服务 API 封装
返回精确的像素级坐标
"""
import httpx
import base64
from typing import List, Optional
from app.models.schemas import OCRChunk, BoundingBox


class BaiduOCRGateway:
    """百度 PP-OCRv5 云服务"""
    
    def __init__(self):
        self.api_url = ""
        self.token = ""
    
    async def process_image(
        self, 
        image_base64: str, 
        page_number: int,
        page_width: float,
        page_height: float,
        api_url: Optional[str] = None,
        token: Optional[str] = None
    ) -> List[OCRChunk]:
        """
        使用百度 PP-OCRv5 处理图片，提取文本和精确坐标
        """
        final_api_url = api_url or self.api_url
        final_token = token or self.token
        
        if not final_api_url or not final_token:
            print("[PP-OCRv5] Warning: API URL or Token not provided")
            return []
        
        headers = {
            "Authorization": f"token {final_token}",
            "Content-Type": "application/json"
        }
        
        # PP-OCRv5 API 参数
        payload = {
            "file": image_base64,
            "fileType": 1,  # 1 表示图片
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "visualize": False
        }
        
        try:
            import sys
            print(f"[PP-OCRv5] Calling API: {final_api_url[:50]}...", flush=True)
            print(f"[PP-OCRv5] Image base64 length: {len(image_base64)}", flush=True)
            sys.stdout.flush()
            
            async with httpx.AsyncClient(timeout=120.0) as client:  # 增加到120秒
                response = await client.post(
                    final_api_url,
                    headers=headers,
                    json=payload
                )
                
                print(f"[PP-OCRv5] Response status: {response.status_code}")
                
                if response.status_code != 200:
                    print(f"[PP-OCRv5] Error response: {response.text[:500]}")
                    return []
                
                result = response.json()
            
            error_code = result.get("errorCode")
            error_msg = result.get("errorMsg")
            print(f"[PP-OCRv5] errorCode: {error_code}, errorMsg: {error_msg}")
            
            if error_code != 0:
                print(f"[PP-OCRv5] API Error: {error_msg}")
                return []
            
            # PP-OCRv5 返回 ocrResults
            ocr_results = result.get("result", {}).get("ocrResults", [])
            print(f"[PP-OCRv5] ocrResults count: {len(ocr_results)}")
            
            return self._parse_ocr_result(ocr_results, page_number, page_width, page_height)
            
        except Exception as e:
            print(f"[PP-OCRv5] Execution failed: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _parse_ocr_result(
        self, 
        ocr_results: list,
        page_number: int,
        page_width: float,
        page_height: float
    ) -> List[OCRChunk]:
        """
        解析 PP-OCRv5 返回结果
        prunedResult 结构:
        {
            "rec_texts": ["文本1", "文本2", ...],
            "rec_scores": [0.99, 0.98, ...],
            "rec_polys": [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ...],
            "rec_boxes": [[x_min, y_min, x_max, y_max], ...]
        }
        """
        chunks = []
        
        try:
            for ocr_result in ocr_results:
                pruned = ocr_result.get("prunedResult", {})
                
                # 打印结构以便调试
                if pruned:
                    print(f"[PP-OCRv5] prunedResult keys: {list(pruned.keys())}")
                
                # 获取文本列表
                texts = pruned.get("rec_texts", [])
                
                # 优先使用 rec_boxes（矩形边界框）
                boxes = pruned.get("rec_boxes")
                
                # 备用：使用多边形坐标
                polys = pruned.get("rec_polys") or pruned.get("dt_polys", [])
                
                print(f"[PP-OCRv5] texts: {len(texts)}, boxes: {boxes is not None and len(boxes) if boxes else 0}, polys: {len(polys)}")
                # 打印前几个原始坐标用于调试
                if boxes and len(boxes) > 0:
                    print(f"[PP-OCRv5] Sample boxes[0]: {boxes[0]}")
                if polys and len(polys) > 0:
                    print(f"[PP-OCRv5] Sample polys[0]: {polys[0]}")
                
                for i, text in enumerate(texts):
                    if not text or not text.strip():
                        continue
                    
                    x, y, w, h = 0, 0, page_width * 0.8, 20  # 默认值
                    
                    # 优先使用 rec_boxes
                    if boxes is not None and i < len(boxes):
                        box = boxes[i]
                        # box 格式: [x_min, y_min, x_max, y_max]
                        if len(box) >= 4:
                            x = box[0]
                            y = box[1]
                            w = box[2] - box[0]
                            h = box[3] - box[1]
                    # 备用：使用多边形
                    elif i < len(polys):
                        poly = polys[i]
                        if len(poly) >= 4:
                            xs = [p[0] for p in poly]
                            ys = [p[1] for p in poly]
                            x = min(xs)
                            y = min(ys)
                            w = max(xs) - min(xs)
                            h = max(ys) - min(ys)
                    
                    # 坐标转换：像素 -> PDF坐标
                    # DPI转换：图片是 150 DPI 渲染的，PDF坐标使用 72 DPI
                    # PDF尺寸 = 像素尺寸 * 72 / 150
                    # 注意：不在这里做Y轴翻转，让前端的 pdfToCss 统一处理
                    
                    scale = 72.0 / 150.0
                    
                    # 转换后的PDF坐标（保持像素坐标系的Y方向，前端会翻转）
                    pdf_x = x * scale
                    pdf_y = y * scale  # 不翻转Y轴
                    pdf_w = w * scale
                    pdf_h = h * scale
                    
                    # 调试日志
                    print(f"[PP-OCRv5 Coord] text='{text[:20]}...', pixel=({x},{y},{w},{h}), pdf=({pdf_x:.1f},{pdf_y:.1f},{pdf_w:.1f},{pdf_h:.1f})")

                    chunks.append(OCRChunk(
                        text=text,
                        bbox=BoundingBox(
                            page=page_number,
                            x=pdf_x,
                            y=pdf_y,
                            w=pdf_w,
                            h=pdf_h
                        )
                    ))
                    
        except Exception as e:
            print(f"[PP-OCRv5 Parse Error] {e}")
            import traceback
            traceback.print_exc()
        
        print(f"[PP-OCRv5] Parsed {len(chunks)} raw chunks")
        
        # 3. 后处理：合并相邻块 & 过滤
        merged_chunks = self._merge_ocr_chunks(chunks, page_height)
        print(f"[PP-OCRv5] Merged into {len(merged_chunks)} chunks")
        
        return merged_chunks

    def _merge_ocr_chunks(self, chunks: List[OCRChunk], page_height: float) -> List[OCRChunk]:
        """
        合并相邻的文本块，过滤低质量内容
        合并规则：
        1. 同一行（Y坐标接近）
        2. 水平距离近（X坐标连续）
        """
        if not chunks:
            return []

        # 1. 过滤无效块
        valid_chunks = []
        for chunk in chunks:
            # 过滤掉空文本或过短的非数字符号
            if not chunk.text or len(chunk.text.strip()) == 0:
                continue
            # 简单过滤：单个字符且非数字/字母（标点符号）
            if len(chunk.text.strip()) == 1 and not chunk.text.strip().isalnum():
                continue
            valid_chunks.append(chunk)

        if not valid_chunks:
            return []

        # 按Y坐标排序（主要），X坐标排序（次要）
        # 允许一定的Y误差（行高的一半）
        sorted_chunks = sorted(valid_chunks, key=lambda c: (c.bbox.y, c.bbox.x))
        
        merged = []
        current_block = sorted_chunks[0]
        
        for next_chunk in sorted_chunks[1:]:
            # 判断是否同一行
            # Y坐标差异小于行高的一半 (假设行高约10-15px，这里取10作为阈值，或者动态计算)
            y_diff = abs(current_block.bbox.y - next_chunk.bbox.y)
            height_avg = (current_block.bbox.h + next_chunk.bbox.h) / 2
            
            # 判断水平距离
            # next.x 应该在 current.x + current.w 附近
            x_gap = next_chunk.bbox.x - (current_block.bbox.x + current_block.bbox.w)
            
            # 合并条件：
            # 1. Y 差距小 (认为是同一行)
            # 2. X 差距不大 (认为是同一句话的断开，或者中间有空格)
            # 3. 如果X差距过大（如表格的列），则不合并
            
            is_same_line = y_diff < (height_avg * 0.5)
            is_adjacent = -20 < x_gap < 50  # 允许重叠(-20)或一定间距(50)
            
            if is_same_line and is_adjacent:
                # 合并
                # 扩展边界框
                new_x = min(current_block.bbox.x, next_chunk.bbox.x)
                new_y = min(current_block.bbox.y, next_chunk.bbox.y)
                new_max_x = max(current_block.bbox.x + current_block.bbox.w, next_chunk.bbox.x + next_chunk.bbox.w)
                new_max_y = max(current_block.bbox.y + current_block.bbox.h, next_chunk.bbox.y + next_chunk.bbox.h)
                
                merged_bbox = BoundingBox(
                    page=current_block.bbox.page,
                    x=new_x,
                    y=new_y,
                    w=new_max_x - new_x,
                    h=new_max_y - new_y
                )
                
                # 合并文本（加空格）
                current_block = OCRChunk(
                    text=current_block.text + " " + next_chunk.text,
                    bbox=merged_bbox
                )
            else:
                # 开始新块
                merged.append(current_block)
                current_block = next_chunk
        
        # 添加最后一个块
        merged.append(current_block)
        
        return merged


# 全局实例
baidu_ocr_gateway = BaiduOCRGateway()
