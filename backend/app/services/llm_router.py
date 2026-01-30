"""
LLM路由服务 - DeepSeek推理
"""
import httpx
import os
import json
from typing import AsyncGenerator, List, Dict

from app.models.schemas import TextChunk


class LLMRouter:
    """LLM路由，支持DeepSeek和GLM-4"""
    
    def __init__(self):
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.zhipu_api_key = os.getenv("ZHIPU_API_KEY", "")
    
    def _build_prompt(self, question: str, chunks: List[TextChunk]) -> str:
        """构建带引用的Prompt"""
        context_blocks = []
        
        for chunk in chunks:
            ref_id = chunk.ref_id or f"ref-{chunks.index(chunk)+1}"
            context_blocks.append(
                f"[{ref_id}] (第{chunk.page_number}页)\n{chunk.content}"
            )
        
        prompt = f"""基于以下文档片段回答问题。请使用 [ref-N] 格式标注信息来源。

文档片段：
---
{chr(10).join(context_blocks)}
---

问题：{question}

注意：回答中每句事实性陈述后必须跟随引用标记，如"根据文档 [ref-1]，系统支持..."。请用中文回答。"""
        
        return prompt
    
    async def chat_stream(
        self, 
        question: str, 
        chunks: List[TextChunk],
        history: List[Dict] = None,
        zhipu_api_key: str = None,
        deepseek_api_key: str = None
    ) -> AsyncGenerator[dict, None]:
        """
        流式对话
        """
        prompt = self._build_prompt(question, chunks)
        
        messages = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        
        # 使用传入的Key或环境变量
        deepseek_key = deepseek_api_key or self.deepseek_api_key
        zhipu_key = zhipu_api_key or self.zhipu_api_key
        
        # 优先使用DeepSeek
        if deepseek_key:
            async for chunk in self._deepseek_stream(messages, deepseek_key):
                yield chunk
        elif zhipu_key:
            async for chunk in self._zhipu_stream(messages, zhipu_key):
                yield chunk
        else:
            yield {
                "type": "error",
                "content": "未配置API Key，请设置DEEPSEEK_API_KEY或ZHIPU_API_KEY"
            }
    
    async def _deepseek_stream(self, messages: List[Dict], api_key: str) -> AsyncGenerator[dict, None]:
        """DeepSeek流式调用"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 4096
                }
            ) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            yield {"type": "done", "content": ""}
                            break
                        
                        try:
                            chunk_data = json.loads(data)
                            delta = chunk_data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            
                            if content:
                                # 提取引用标记
                                import re
                                refs = re.findall(r'\[ref-\d+\]', content)
                                
                                yield {
                                    "type": "content",
                                    "content": content,
                                    "active_refs": refs
                                }
                        except (json.JSONDecodeError, KeyError):
                            continue
    
    async def _zhipu_stream(self, messages: List[Dict], api_key: str) -> AsyncGenerator[dict, None]:
        """智谱GLM-4流式调用"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers=headers,
                json={
                    "model": "glm-4-flash",
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 4096
                }
            ) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            yield {"type": "done", "content": ""}
                            break
                        
                        try:
                            chunk_data = json.loads(data)
                            delta = chunk_data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            
                            if content:
                                import re
                                refs = re.findall(r'\[ref-\d+\]', content)
                                
                                yield {
                                    "type": "content",
                                    "content": content,
                                    "active_refs": refs
                                }
                        except (json.JSONDecodeError, KeyError):
                            continue


# 全局实例
llm_router = LLMRouter()
