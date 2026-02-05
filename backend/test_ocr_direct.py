import asyncio
import os
from dotenv import load_dotenv

# Load .env from parent directory or current
load_dotenv(r"c:\Users\markl\Desktop\ai\PDFQA2\.env")

from app.services.ocr_gateway import ocr_gateway

# Mock base64 image (small white square)
# iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==
TEST_IMAGE_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

async def test_ocr():
    print("Testing OCR Gateway...")
    key = os.getenv("ZHIPU_API_KEY")
    print(f"API Key present: {bool(key)}")
    
    try:
        chunks = await ocr_gateway.process_image(
            image_base64=TEST_IMAGE_B64,
            page_number=1,
            page_width=100,
            page_height=100,
            api_key=key
        )
        print(f"Chunks returned: {len(chunks)}")
        for c in chunks:
            print(c)
    except Exception as e:
        print(f"Error caught: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_ocr())
