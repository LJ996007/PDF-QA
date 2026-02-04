import requests
import time

def check_health():
    try:
        print("Checking /api/health...")
        resp = requests.get("http://localhost:8000/api/health", timeout=5)
        print(f"Health Status: {resp.status_code}")
        print(f"Response: {resp.json()}")
        return True
    except Exception as e:
        print(f"Health check failed: {e}")
        return False

def check_upload():
    try:
        print("Checking /api/documents/upload...")
        # Create a dummy PDF file
        with open("test.pdf", "wb") as f:
            f.write(b"%PDF-1.4 header dummy content")
        
        files = {'file': ('test.pdf', open('test.pdf', 'rb'), 'application/pdf')}
        start = time.time()
        resp = requests.post("http://localhost:8000/api/documents/upload", files=files, timeout=10)
        print(f"Upload Status: {resp.status_code}")
        print(f"Time taken: {time.time() - start:.2f}s")
        if resp.status_code == 200:
             print(f"Response: {resp.json()}")
        else:
             print(f"Error: {resp.text}")
    except Exception as e:
        print(f"Upload check failed: {e}")

if __name__ == "__main__":
    if check_health():
        check_upload()
