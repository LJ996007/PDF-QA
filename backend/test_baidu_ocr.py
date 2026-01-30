"""
PP-OCRv5 API 测试脚本
用于验证百度OCR服务连通性
"""
import asyncio
import base64
import httpx


# ============ 用户提供的凭证 ============
API_URL = "https://o99acegcpft3abvc.aistudio-app.com/ocr"
TOKEN = "27ce6981b110be74a0cfc46c75900bc7a9c41bb6"
# ========================================


async def test_ocr():
    """测试 PP-OCRv5 API"""
    
    # 创建一个简单的测试图片（白底黑字）
    # 这里使用一个公开的测试图片URL
    test_image_url = "https://paddle-model-ecology.bj.bcebos.com/paddlex/imgs/demo_image/general_ocr_002.png"
    
    print(f"[Test] Downloading test image from: {test_image_url}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 下载测试图片
        img_response = await client.get(test_image_url)
        if img_response.status_code != 200:
            print(f"[Error] Failed to download test image: {img_response.status_code}")
            return
        
        image_base64 = base64.b64encode(img_response.content).decode("ascii")
        print(f"[Test] Image downloaded, base64 length: {len(image_base64)}")
        
        # 调用 PP-OCRv5 API
        headers = {
            "Authorization": f"token {TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "file": image_base64,
            "fileType": 1,  # 图片
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useTextlineOrientation": False,
            "visualize": False
        }
        
        print(f"[Test] Calling API: {API_URL[:50]}...")
        
        response = await client.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=60.0
        )
        
        print(f"[Test] Response status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"[Error] API returned error: {response.text[:500]}")
            return
        
        result = response.json()
        
        print(f"\n===== API Response =====")
        print(f"errorCode: {result.get('errorCode')}")
        print(f"errorMsg: {result.get('errorMsg')}")
        
        ocr_results = result.get("result", {}).get("ocrResults", [])
        print(f"\nocrResults count: {len(ocr_results)}")
        
        if ocr_results:
            first = ocr_results[0]
            print(f"\nFirst result keys: {list(first.keys())}")
            
            pruned = first.get("prunedResult", {})
            if pruned:
                print(f"\nprunedResult keys: {list(pruned.keys())}")
                
                texts = pruned.get("rec_texts", [])
                boxes = pruned.get("rec_boxes")
                polys = pruned.get("rec_polys", [])
                
                print(f"\n- rec_texts count: {len(texts)}")
                print(f"- rec_boxes: {'存在' if boxes else '不存在'} ({len(boxes) if boxes else 0} items)")
                print(f"- rec_polys count: {len(polys)}")
                
                if texts:
                    print(f"\n=== 识别到的前5个文本 ===")
                    for i, text in enumerate(texts[:5]):
                        coord_info = ""
                        if boxes and i < len(boxes):
                            coord_info = f" | box: {boxes[i]}"
                        elif polys and i < len(polys):
                            coord_info = f" | poly first point: {polys[i][0] if polys[i] else 'N/A'}"
                        print(f"  [{i}] {text}{coord_info}")
                    
                    print(f"\n✅ 测试成功！共识别到 {len(texts)} 个文本块")
            else:
                print("[Warning] prunedResult is empty")
        else:
            print("[Warning] No ocrResults returned")


if __name__ == "__main__":
    if API_URL == "YOUR_API_URL_HERE" or TOKEN == "YOUR_TOKEN_HERE":
        print("❌ 请先填入您的 API_URL 和 TOKEN！")
        print("   从 https://aistudio.baidu.com/paddleocr/task 获取")
    else:
        asyncio.run(test_ocr())
