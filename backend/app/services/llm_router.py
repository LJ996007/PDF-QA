"""
LLM 路由服务，支持文本问答与共享问答提示词。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from app.models.schemas import ChatPageReferenceGroup, TextChunk


class LLMRouter:
    """LLM 路由，支持 DeepSeek 和 GLM-4 文本问答。"""

    def __init__(self):
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.zhipu_api_key = os.getenv("ZHIPU_API_KEY", "")

    def _build_context_blocks(self, chunks: List[TextChunk]) -> List[str]:
        context_blocks: List[str] = []
        for index, chunk in enumerate(chunks):
            ref_id = chunk.ref_id or f"ref-{index + 1}"
            context_blocks.append(f"[{ref_id}] (第{chunk.page_number}页)\n{chunk.content}")
        return context_blocks

    def _build_reference_catalog(self, references: List[Dict[str, Any]]) -> List[str]:
        catalog: List[str] = []
        for index, ref in enumerate(references):
            ref_id = str(ref.get("ref_id") or f"ref-{index + 1}").strip()
            page = int(ref.get("page") or 1)
            content = str(ref.get("content") or "").strip()
            if content:
                catalog.append(f"[{ref_id}] 第{page}页：{content}")
            else:
                catalog.append(f"[{ref_id}] 第{page}页页面图像")
        return catalog

    def _build_answer_requirements(self) -> str:
        return (
            "回答要求：\n"
            "1. 直接回答用户问题，可自然分段或按需列点，不要输出“逐项核对”“风险提示”“引用说明”等固定栏目。\n"
            "2. 每一句事实性陈述后都必须紧跟至少一个 [ref-N]，且只能使用给定证据中的引用编号。\n"
            "3. 如果现有证据不足，必须明确写：根据现有片段无法确认。\n"
            "4. 不允许臆测，不允许虚构编号、页码或文档中不存在的信息。\n"
            "5. 不要输出 JSON，不要追加与问题无关的模板化标题。"
        )

    def _format_page_reference_group_ranges(self, pages: List[int]) -> str:
        if not pages:
            return ""

        normalized = sorted({page for page in pages if page > 0})
        if not normalized:
            return ""

        parts: List[str] = []
        start = normalized[0]
        end = normalized[0]

        for page in normalized[1:]:
            if page == end + 1:
                end = page
                continue

            parts.append(str(start) if start == end else f"{start}-{end}")
            start = page
            end = page

        parts.append(str(start) if start == end else f"{start}-{end}")
        return "、".join(parts)

    def _build_page_reference_group_section(
        self,
        page_reference_groups: Optional[List[ChatPageReferenceGroup]] = None,
    ) -> str:
        groups = [group for group in (page_reference_groups or []) if group.pages]
        if not groups:
            return ""

        definitions = "\n".join(
            f"{group.placeholder}=第{self._format_page_reference_group_ranges(group.pages)}页"
            for group in groups
        )
        return (
            "页面引用组定义：\n"
            f"{definitions}\n\n"
            "如果问题中出现【页面组X】，必须严格按上面的页码集合来理解、比较和总结，不得把不同页面组混为一谈。"
        )

    def _build_prompt(
        self,
        question: str,
        chunks: List[TextChunk],
        page_reference_groups: Optional[List[ChatPageReferenceGroup]] = None,
    ) -> str:
        context_text = "\n\n".join(self._build_context_blocks(chunks))
        page_reference_group_section = self._build_page_reference_group_section(page_reference_groups)
        page_reference_group_prefix = f"{page_reference_group_section}\n\n" if page_reference_group_section else ""
        return (
            "你是一个严谨的文档问答助手。请仅基于给定文档片段作答，不得臆测。\n\n"
            f"{page_reference_group_prefix}"
            "文档片段：\n"
            "---\n"
            f"{context_text}\n"
            "---\n\n"
            f"问题：{question}\n\n"
            f"{self._build_answer_requirements()}"
        )

    def build_multimodal_prompt(
        self,
        *,
        question: str,
        references: List[Dict[str, Any]],
        chunks: Optional[List[TextChunk]] = None,
        page_reference_groups: Optional[List[ChatPageReferenceGroup]] = None,
    ) -> str:
        reference_catalog = "\n".join(self._build_reference_catalog(references))
        context_appendix = ""
        if chunks:
            context_appendix = "\n\n补充文本片段：\n" + "\n\n".join(self._build_context_blocks(chunks))
        page_reference_group_section = self._build_page_reference_group_section(page_reference_groups)
        page_reference_group_prefix = f"{page_reference_group_section}\n\n" if page_reference_group_section else ""
        return (
            "你是一个严谨的文档问答助手。请同时结合页面图像和给定证据回答用户问题，不得臆测。\n\n"
            f"{page_reference_group_prefix}"
            "可用引用编号与证据：\n"
            f"{reference_catalog or '当前没有可用引用。'}"
            f"{context_appendix}\n\n"
            f"问题：{question}\n\n"
            f"{self._build_answer_requirements()}"
        )

    def extract_ref_ids(self, text: str) -> List[str]:
        return [f"ref-{match}" for match in re.findall(r"\[ref-(\d+)\]", text or "")]

    async def chat_stream(
        self,
        question: str,
        chunks: List[TextChunk],
        history: List[Dict] = None,
        page_reference_groups: Optional[List[ChatPageReferenceGroup]] = None,
        zhipu_api_key: str = None,
        deepseek_api_key: str = None,
    ) -> AsyncGenerator[dict, None]:
        """
        流式对话。
        """
        prompt = self._build_prompt(question, chunks, page_reference_groups)

        messages = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        deepseek_key = deepseek_api_key or self.deepseek_api_key
        zhipu_key = zhipu_api_key or self.zhipu_api_key

        if deepseek_key:
            async for chunk in self._deepseek_stream(messages, deepseek_key):
                yield chunk
        elif zhipu_key:
            async for chunk in self._zhipu_stream(messages, zhipu_key):
                yield chunk
        else:
            yield {
                "type": "error",
                "content": "未配置API Key，请设置DEEPSEEK_API_KEY或ZHIPU_API_KEY",
            }

    async def _deepseek_stream(self, messages: List[Dict], api_key: str) -> AsyncGenerator[dict, None]:
        """DeepSeek 流式调用。"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
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
                    "max_tokens": 4096,
                },
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        yield {"type": "done", "content": ""}
                        break

                    try:
                        chunk_data = json.loads(data)
                        delta = chunk_data["choices"][0].get("delta", {})
                        content = delta.get("content", "")

                        if content:
                            yield {
                                "type": "content",
                                "content": content,
                                "active_refs": self.extract_ref_ids(content),
                            }
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def _zhipu_stream(self, messages: List[Dict], api_key: str) -> AsyncGenerator[dict, None]:
        """智谱 GLM-4 流式调用。"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers=headers,
                json={
                    "model": "glm-4.7",
                    "messages": messages,
                    "stream": True,
                    "max_tokens": 4096,
                },
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        yield {"type": "done", "content": ""}
                        break

                    try:
                        chunk_data = json.loads(data)
                        delta = chunk_data["choices"][0].get("delta", {})
                        content = delta.get("content", "")

                        if content:
                            yield {
                                "type": "content",
                                "content": content,
                                "active_refs": self.extract_ref_ids(content),
                            }
                    except (json.JSONDecodeError, KeyError):
                        continue

    async def chat_completion(
        self,
        messages: List[Dict],
        api_key: str = None,
        json_mode: bool = False,
    ) -> Any:
        """非流式对话，支持 JSON 模式。"""

        deepseek_key = api_key or self.deepseek_api_key
        zhipu_key = api_key or self.zhipu_api_key

        url = ""
        headers = {}
        payload = {}

        if deepseek_key:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {deepseek_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "deepseek-chat",
                "messages": messages,
                "stream": False,
                "max_tokens": 4096,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

        elif zhipu_key:
            url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            headers = {
                "Authorization": f"Bearer {zhipu_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "glm-4.7",
                "messages": messages,
                "stream": False,
                "max_tokens": 4096,
            }
        else:
            raise Exception("API Key not configured")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

            class Message:
                def __init__(self, content):
                    self.content = content

            class Choice:
                def __init__(self, content):
                    self.message = Message(content)

            class Response:
                def __init__(self, content):
                    self.choices = [Choice(content)]

            content = data["choices"][0]["message"]["content"]
            return Response(content)


llm_router = LLMRouter()
