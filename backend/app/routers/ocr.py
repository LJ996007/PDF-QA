"""
按需OCR路由
"""
from fastapi import APIRouter, HTTPException
import fitz

from app.models.schemas import OCRResponse
from app.services.ocr_gateway import ocr_gateway
from app.services.rag_engine import rag_engine
from app.routers.documents import documents


router = APIRouter()


@router.post("/{doc_id}/pages/{page_num}/ocr", response_model=OCRResponse)
async def ocr_page(doc_id: str, page_num: int):
    """
    按需OCR指定页面
    """
    if doc_id not in documents:
        raise HTTPException(status_code=404, detail="文档不存在")
    
    doc = documents[doc_id]
    
    if page_num < 1 or page_num > doc["total_pages"]:
        raise HTTPException(status_code=400, detail="页码超出范围")
    
    # 检查是否需要OCR
    pages = doc.get("pages", [])
    target_page = None
    for p in pages:
        if p.page_number == page_num:
            target_page = p
            break
    
    if not target_page:
        raise HTTPException(status_code=404, detail="页面不存在")
    
    if target_page.type == "native":
        # 原生文本页，不需要OCR
        return OCRResponse(page=page_num, chunks=[])
    
    # 获取图片和页面尺寸
    pdf_doc = fitz.open(doc["file_path"])
    try:
        page = pdf_doc[page_num - 1]
        page_width = page.rect.width
        page_height = page.rect.height
    finally:
        pdf_doc.close()
    
    # 调用OCR
    if not target_page.image_base64:
        raise HTTPException(status_code=500, detail="图片数据不存在")
    
    chunks = await ocr_gateway.process_image(
        target_page.image_base64,
        page_num,
        page_width,
        page_height
    )
    
    # 索引OCR结果
    await rag_engine.index_ocr_result(
        doc_id,
        page_num,
        [{"text": c.text, "bbox": c.bbox.model_dump()} for c in chunks]
    )
    
    # 更新页面状态
    target_page.type = "native"
    target_page.text = "\n".join([c.text for c in chunks])
    
    return OCRResponse(page=page_num, chunks=chunks)
