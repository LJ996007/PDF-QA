"""配置文件 - 从环境变量加载配置"""
import os
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 大模型配置
_LLM_API_KEY_ENV = os.getenv("LLM_API_KEY", "")
_LLM_API_BASE_ENV = os.getenv("LLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
_LLM_MODEL_ENV = os.getenv("LLM_MODEL", "glm-4")

_llm_api_key_override = None
_llm_api_base_override = None
_llm_model_override = None


def get_llm_config():
    api_key = _llm_api_key_override if _llm_api_key_override is not None else _LLM_API_KEY_ENV
    api_base = _llm_api_base_override if _llm_api_base_override is not None else _LLM_API_BASE_ENV
    model = _llm_model_override if _llm_model_override is not None else _LLM_MODEL_ENV
    return api_key, api_base, model


def set_llm_config(api_key=None, api_base=None, model=None):
    global _llm_api_key_override, _llm_api_base_override, _llm_model_override
    if api_key is not None:
        _llm_api_key_override = api_key
    if api_base is not None:
        _llm_api_base_override = api_base
    if model is not None:
        _llm_model_override = model


def identify_provider(api_base: str):
    provider = "unknown"
    provider_name = "已配置 API"

    if "bigmodel" in api_base:
        provider = "zhipu"
        provider_name = "已配置智谱 AI (GLM-4)"
    elif "deepseek" in api_base:
        provider = "deepseek"
        provider_name = "已配置 DeepSeek"
    elif "dashscope" in api_base:
        provider = "dashscope"
        provider_name = "已配置通义千问 (DashScope)"
    elif "openai" in api_base:
        provider = "openai"
        provider_name = "已配置 OpenAI API"

    return provider, provider_name

# 服务配置
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./data/uploads")
CHROMA_DIR = os.getenv("CHROMA_DIR", "./data/chroma_db")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "50"))  # MB

# CORS 配置
_cors_origins = os.getenv("CORS_ORIGINS", "*")
if _cors_origins.strip() == "*":
    CORS_ORIGINS = ["*"]
else:
    CORS_ORIGINS = [origin.strip() for origin in _cors_origins.split(",") if origin.strip()]
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"

# 确保目录存在
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)
