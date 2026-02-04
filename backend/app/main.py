"""FastAPI 主入口"""
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import upload, chat
from app.config import CORS_ORIGINS, CORS_ALLOW_CREDENTIALS

# 加载 .env 文件
load_dotenv()

# 创建FastAPI应用
app = FastAPI(
    title="PDF智能问答系统",
    description="基于PDF文档的智能问答API",
    version="1.0.0"
)

# 配置CORS - 允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(chat.router, prefix="/api", tags=["chat"])


@app.get("/")
async def root():
    """根路径"""
    return {"message": "PDF智能问答系统 API", "version": "1.0.0"}


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}


@app.get("/api/llm-status")
async def llm_status():
    """检查大模型连接状态"""
    from app.config import get_llm_config, identify_provider

    api_key, api_base, model = get_llm_config()

    # 检查是否配置了 API 密钥
    if not api_key:
        return {
            "configured": False,
            "provider": "none",
            "model": model,
            "api_base": api_base,
            "message": "未配置大模型 API 密钥。请在 .env 文件或前端设置中配置"
        }

    provider, provider_name = identify_provider(api_base)

    return {
        "configured": True,
        "provider": provider,
        "model": model,
        "api_base": api_base,
        "message": provider_name
    }
