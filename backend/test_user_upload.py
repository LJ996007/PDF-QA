import requests
import time
import os

file_path = r"C:\Users\markl\Desktop\供应商彩页-全部.pdf"
url = "http://localhost:8000/api/documents/upload"

def upload_file():
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    print(f"Uploading file: {file_path}")
    try:
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f, 'application/pdf')}
            data = {'ocr_provider': 'baidu', 'ocr_model': 'glm-4v-flash'} # default params
            
            start = time.time()
            resp = requests.post(url, files=files, data=data, timeout=60) # Increased timeout for real file
            
            print(f"Status Code: {resp.status_code}")
            print(f"Time Taken: {time.time() - start:.2f}s")
            
            if resp.status_code == 200:
                print("Response:", resp.json())
            else:
                print("Error:", resp.text)
                
    except Exception as e:
        print(f"Exception during upload: {e}")

if __name__ == "__main__":
    upload_file()
