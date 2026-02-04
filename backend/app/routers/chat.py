"""问答接口 - 智能问答"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import AskRequest, AskResponse, LLMConfigRequest, LLMConfigResponse
from app.services.vector_store import VectorStore
from app.services.llm_service import get_llm_service
from typing import List
from app.config import get_llm_config, set_llm_config, identify_provider

router = APIRouter()

# 全局向量存储实例
_vector_store = None


def get_vector_store():
    """获取向量存储实例"""
    global _vector_store
    if _vector_store is None:
        from app.services.vector_store import VectorStore
        _vector_store = VectorStore()
    return _vector_store


@router.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest):
    """
    接收问题，返回带索引的答案

    处理流程：
    1. 从向量库检索相关段落（Top 5）
    2. 将问题+相关段落发送给大模型
    3. 大模型生成答案，标注引用的段落ID
    4. 返回答案和引用信息
    """
    try:
        # 1. 获取服务实例
        vector_store = get_vector_store()
        llm_service = get_llm_service()

        # 2. 从向量库检索相关段落
        relevant_paragraphs = vector_store.search(
            query=request.question,
            document_id=request.document_id,
            top_k=5  # 检索最相关的5个段落
        )

        if not relevant_paragraphs:
            return AskResponse(
                answer="抱歉，文档中没有找到与您的问题相关的内容。",
                references=[]
            )

        # 3. 调用大模型生成答案
        result = llm_service.ask(
            question=request.question,
            context_paragraphs=relevant_paragraphs
        )

        # 4. 获取引用的详细信息
        references = llm_service.get_reference_details(
            reference_ids=result["references"],
            all_paragraphs=relevant_paragraphs
        )

        return AskResponse(
            answer=result["answer"],
            references=references
        )

    except ValueError as e:
        # 通常是LLM服务未配置
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"问答处理失败: {str(e)}")


@router.post("/search")
async def search_paragraphs(request: AskRequest):
    """
    仅检索相关段落，不调用大模型

    用于快速查看相关内容
    """
    try:
        vector_store = get_vector_store()

        paragraphs = vector_store.search(
            query=request.question,
            document_id=request.document_id,
            top_k=10
        )

        return {
            "paragraphs": paragraphs,
            "count": len(paragraphs)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)}")


@router.get("/llm-config", response_model=LLMConfigResponse)
async def get_llm_config_endpoint():
    """获取当前大模型配置（不返回密钥）"""
    api_key, api_base, model = get_llm_config()
    provider, provider_name = identify_provider(api_base)
    return LLMConfigResponse(
        configured=bool(api_key),
        provider=provider,
        model=model,
        api_base=api_base,
        api_key_set=bool(api_key),
        message=provider_name if api_key else "未配置大模型 API 密钥"
    )


@router.post("/llm-config", response_model=LLMConfigResponse)
async def set_llm_config_endpoint(request: LLMConfigRequest):
    """更新大模型配置（仅内存，不落盘）"""
    api_key = request.api_key
    api_base = request.api_base
    model = request.model

    # 仅更新传入字段，支持单项修改
    set_llm_config(
        api_key=api_key.strip() if isinstance(api_key, str) else None,
        api_base=api_base.strip() if isinstance(api_base, str) else None,
        model=model.strip() if isinstance(model, str) else None
    )

    api_key, api_base, model = get_llm_config()
    provider, provider_name = identify_provider(api_base)

    return LLMConfigResponse(
        configured=bool(api_key),
        provider=provider,
        model=model,
        api_base=api_base,
        api_key_set=bool(api_key),
        message=provider_name if api_key else "未配置大模型 API 密钥"
    )
