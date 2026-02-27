"""测试完整的 OCR 流程"""
import sys
import os
import asyncio

sys.path.insert(0, '.')
os.chdir(os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv('.env')

async def main():
    print("=" * 60)
    print("OCR 完整流程测试")
    print("=" * 60)

    # 1. 环境变量检查
    print("\n1. 环境变量:")
    baidu_url = os.getenv('BAIDU_OCR_API_URL')
    baidu_token = os.getenv('BAIDU_OCR_TOKEN')
    print(f"   BAIDU_OCR_API_URL: {baidu_url}")
    print(f"   BAIDU_OCR_TOKEN: {baidu_token[:30] if baidu_token else None}...")

    # 2. 上传文档
    print("\n2. 模拟上传文档:")
    from app.routers.documents import documents, process_document_async
    from app.services.parser import process_document

    file_path = 'test_valid.pdf'
    doc_id = 'test_doc_12345'

    pages, thumbnails = process_document(file_path)
    print(f"   解析成功: {len(pages)} 页")

    # 手动创建文档记录（模拟上传）
    documents[doc_id] = {
        "id": doc_id,
        "name": "test_valid.pdf",
        "total_pages": len(pages),
        "thumbnails": thumbnails,
        "file_path": file_path,
        "pages": pages,
        "recognized_pages": [],
        "ocr_required_pages": list(range(1, len(pages) + 1)),
        "page_ocr_status": {1: "unrecognized"},
        "ocr_mode": "manual",
        "baidu_ocr_url": baidu_url,
        "baidu_ocr_token": baidu_token,
    }
    print(f"   文档 ID: {doc_id}")
    print(f"   OCR URL 已设置: {documents[doc_id].get('baidu_ocr_url') is not None}")
    print(f"   OCR Token 已设置: {documents[doc_id].get('baidu_ocr_token') is not None}")

    # 3. 执行识别
    print("\n3. 执行 OCR 识别:")
    from app.routers.documents import recognize_document_page

    try:
        result = await recognize_document_page(doc_id, 1)
        print(f"   识别结果: {result}")

        # 检查文档状态
        doc_status = documents[doc_id].get('page_ocr_status', {})
        print(f"   页面状态: {doc_status}")

    except Exception as e:
        print(f"   ❌ 识别失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
