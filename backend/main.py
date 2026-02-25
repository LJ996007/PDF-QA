"""FastAPI entrypoint for PDF QA backend."""

from contextlib import asynccontextmanager
import io
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load env vars from repository root .env
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# Force UTF-8 stdout/stderr on Windows terminals.
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from app.routers import chat, documents, ocr  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("chroma_db", exist_ok=True)
    os.makedirs("thumbnails", exist_ok=True)
    os.makedirs(os.path.join("doc_store", "ocr"), exist_ok=True)
    os.makedirs(os.path.join("doc_store", "chat"), exist_ok=True)
    os.makedirs(os.path.join("doc_store", "compliance"), exist_ok=True)
    os.makedirs(os.path.join("doc_store", "compliance_v2"), exist_ok=True)
    os.makedirs(os.path.join("doc_store", "evidence"), exist_ok=True)
    os.makedirs(os.path.join("doc_store", "review"), exist_ok=True)

    try:
        documents.load_persisted_documents()
    except Exception as exc:
        print(f"[DOC_STORE] Failed to load persisted documents: {exc}")

    try:
        await documents.start_ocr_worker()
    except Exception as exc:
        print(f"[OCR_QUEUE] Failed to start OCR worker: {exc}")

    yield

    try:
        await documents.stop_ocr_worker()
    except Exception as exc:
        print(f"[OCR_QUEUE] Failed to stop OCR worker: {exc}")


app = FastAPI(
    title="PDF智能问答系统 V6.0",
    description="基于渐进式视觉RAG的本地化文档助手",
    version="6.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
        "http://localhost:3003",
        "http://127.0.0.1:3003",
        "http://localhost:3004",
        "http://127.0.0.1:3004",
        "http://localhost:3005",
        "http://127.0.0.1:3005",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(ocr.router, prefix="/api/documents", tags=["ocr"])
app.include_router(chat.router, prefix="/api", tags=["chat"])


@app.get("/")
async def root():
    return {"status": "ok", "version": "6.0.0"}


@app.get("/api/health")
async def health():
    return {"status": "healthy"}
