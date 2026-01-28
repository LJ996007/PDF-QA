"""FastAPI 主入口"""
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import upload, chat

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
    allow_origins=["*"],  # 开发环境允许所有来源
    allow_credentials=True,
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
    from app.config import LLM_API_KEY, LLM_API_BASE, LLM_MODEL

    # 检查是否配置了 API 密钥
    if not LLM_API_KEY:
        return {
            "configured": False,
            "provider": "none",
            "message": "未配置大模型 API 密钥。请在 .env 文件中设置 LLM_API_KEY"
        }

    # 检测使用的是哪个提供商
    provider = "unknown"
    provider_name = "已配置 API"

    if "bigmodel" in LLM_API_BASE:
        provider = "zhipu"
        provider_name = "已配置智谱 AI (GLM-4)"
    elif "deepseek" in LLM_API_BASE:
        provider = "deepseek"
        provider_name = "已配置 DeepSeek"
    elif "dashscope" in LLM_API_BASE:
        provider = "dashscope"
        provider_name = "已配置通义千问 (DashScope)"
    elif "openai" in LLM_API_BASE:
        provider = "openai"
        provider_name = "已配置 OpenAI API"

    return {
        "configured": True,
        "provider": provider,
        "model": LLM_MODEL,
        "message": provider_name
    }
