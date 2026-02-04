"""上传接口 - 上传PDF并解析"""
import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Dict
from app.services.pdf_parser import PDFParser, is_valid_pdf
from app.services.vector_store import VectorStore
from app.config import UPLOAD_DIR, MAX_FILE_SIZE
from app.models.schemas import UploadResponse

router = APIRouter()

# 全局向量存储实例（实际项目中可能需要更复杂的管理）
vector_store = None


def get_vector_store():
    """获取或创建向量存储实例"""
    global vector_store
    if vector_store is None:
        from app.services.vector_store import VectorStore
        vector_store = VectorStore()
    return vector_store


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """
    上传PDF文件，解析并建立索引

    处理流程：
    1. 验证文件类型和大小
    2. 保存PDF文件
    3. 解析PDF内容（提取段落和位置）
    4. 向量化存储到ChromaDB
    """
    # 1. 验证文件类型
    filename = file.filename or ""
    if not filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF文件")
    original_filename = os.path.basename(filename)

    # 2. 生成文档ID并保存文件
    document_id = uuid.uuid4().hex[:8]
    file_path = os.path.join(UPLOAD_DIR, f"{document_id}.pdf")

    # 确保上传目录存在
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # 3. 流式写入并检查大小
    max_size_bytes = MAX_FILE_SIZE * 1024 * 1024
    written = 0
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                written += len(chunk)
                if written > max_size_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail=f"文件大小超过限制（最大{MAX_FILE_SIZE}MB）"
                    )
                f.write(chunk)
    except HTTPException:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {str(e)}")

    # 4. 验证是否为有效PDF
    if not is_valid_pdf(file_path):
        os.remove(file_path)
        raise HTTPException(status_code=400, detail="无效的PDF文件")

    # 5. 解析PDF
    try:
        parser = PDFParser(file_path, document_id=document_id)
        paragraphs, metadata = parser.parse()
        parser.close()
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"PDF解析失败: {str(e)}")

    # 6. 存储到向量数据库
    try:
        store = get_vector_store()
        store.add_document(
            document_id=metadata["id"],
            paragraphs=paragraphs
        )
    except Exception as e:
        # 如果向量存储失败，删除已上传的文件
        os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"向量存储失败: {str(e)}")

    return UploadResponse(
        document_id=metadata["id"],
        filename=original_filename,
        total_pages=metadata["total_pages"],
        paragraph_count=metadata["paragraph_count"],
        message="解析成功"
    )


@router.get("/documents/{document_id}")
async def get_document(document_id: str):
    """获取文档信息"""
    # TODO: 实现从数据库获取文档信息
    return {"document_id": document_id, "status": "active"}
