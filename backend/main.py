"""
PDF智能问答系统 V6.0 - FastAPI后端入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import sys
import io

# 强制设置 stdout/stderr 为 UTF-8 编码（解决 Windows GBK 编码问题）
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from app.routers import documents, ocr, chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时：确保必要目录存在
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("chroma_db", exist_ok=True)
    os.makedirs("thumbnails", exist_ok=True)
    
    yield
    
    # 关闭时：清理资源


app = FastAPI(
    title="PDF智能问答系统 V6.0",
    description="基于渐进式视觉RAG的本地化文档助手",
    version="6.0.0",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:3001", "http://127.0.0.1:3001",
        "http://localhost:3002", "http://127.0.0.1:3002",
        "http://localhost:3003", "http://127.0.0.1:3003",
        "http://localhost:3004", "http://127.0.0.1:3004",
        "http://localhost:3005", "http://127.0.0.1:3005",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(ocr.router, prefix="/api/documents", tags=["ocr"])
app.include_router(chat.router, prefix="/api", tags=["chat"])


@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "version": "6.0.0"}


@app.get("/api/health")
async def health():
    """API健康检查"""
    return {"status": "healthy"}
