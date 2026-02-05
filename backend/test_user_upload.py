import requests
import time
import os
import json

file_path = r"C:\Users\markl\Desktop\供应商彩页-简版.pdf"
upload_url = "http://localhost:8000/api/documents/upload"
compliance_url = "http://localhost:8000/api/documents/verify"

def test_workflow():
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    # 1. Upload
    print(f"\n=== Step 1: Uploading {os.path.basename(file_path)} ===")
    doc_id = None
    try:
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f, 'application/pdf')}
            data = {'ocr_provider': 'baidu', 'ocr_model': 'glm-4v-flash'}
            
            start = time.time()
            resp = requests.post(upload_url, files=files, data=data, timeout=120) 
            
            print(f"Upload Status: {resp.status_code}")
            print(f"Time Taken: {time.time() - start:.2f}s")
            
            if resp.status_code == 200:
                result = resp.json()
                print("Upload Response:", json.dumps(result, ensure_ascii=False))
                doc_id = result.get('document_id')
            else:
                print("Upload Error:", resp.text)
                return

    except Exception as e:
        print(f"Exception during upload: {e}")
        return

    if not doc_id:
        print("Failed to get doc_id, stopping.")
        return

    # Poll progress
    print("Polling progress...")
    progress_url = f"http://localhost:8000/api/documents/{doc_id}/progress"
    
    # We can't use SSE easily with simple requests, but we might just hit it? 
    # The endpoint returns EventSourceResponse causing loop if we just requests.get on it?
    # Actually the backend uses sse_starlette.
    # Let's just wait longer and maybe try to hit GET /doc_id to see if it exists?
    
    start_wait = time.time()
    while time.time() - start_wait < 60:
        try:
            # Check if doc exists
            status_resp = requests.get(f"http://localhost:8000/api/documents/{doc_id}")
            if status_resp.status_code == 200:
                print("Document processed successfully!")
                break
        except:
            pass
        print(".", end="", flush=True)
        time.sleep(2)
    print("\n")
    
    # 2. Compliance Check (Verification)
    print(f"\n=== Step 2: Running Compliance Check on {doc_id} ===")
    try:
        # Correct endpoint: /api/documents/{doc_id}/compliance
        target_url = f"http://localhost:8000/api/documents/{doc_id}/compliance"
        
        payload = {
            "requirements": ["能量 >= 130KV", "焦点 <= 8um"]
        }
        
        start = time.time()
        resp = requests.post(target_url, json=payload, timeout=60)
        
        print(f"Compliance Status: {resp.status_code}")
        print(f"Time Taken: {time.time() - start:.2f}s")
        
        if resp.status_code == 200:
            result = resp.json()
            # print("Compliance Response:", json.dumps(result, ensure_ascii=False, indent=2))
            
            # Simple validation
            results = result.get('results', [])
            print(f"Received {len(results)} results.")
            for item in results:
                print(f" - {item.get('requirement')}: {item.get('status')} ({item.get('response')})")
        else:
            print("Compliance Error:", resp.text)

    except Exception as e:
        print(f"Exception during compliance check: {e}")

if __name__ == "__main__":
    test_workflow()
