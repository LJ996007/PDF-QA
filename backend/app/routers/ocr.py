"""On-demand OCR routes."""

from fastapi import APIRouter

from app.models.schemas import OCRResponse
from app.routers.documents import recognize_document_page

router = APIRouter()


@router.post("/{doc_id}/pages/{page_num}/ocr", response_model=OCRResponse)
async def ocr_page(doc_id: str, page_num: int):
    result = await recognize_document_page(doc_id, page_num)
    return OCRResponse(
        page=result["page"],
        chunks=result["chunks"],
        status=result["status"],
        already_recognized=result["already_recognized"],
        message=result.get("message"),
    )
