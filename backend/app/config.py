"""配置文件 - 从环境变量加载配置"""
import os
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 大模型配置
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4")

# 服务配置
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")
CHROMA_DIR = os.getenv("CHROMA_DIR", "./data/chroma_db")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "50"))  # MB

# 确保目录存在
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
