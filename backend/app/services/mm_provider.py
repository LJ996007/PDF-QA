"""Multimodal provider abstraction and OpenAI-compatible implementation."""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass(slots=True)
class PageImageInput:
    """Image payload for one PDF page."""

    page: int
    image_base64: str
    width: float
    height: float


MULTIMODAL_PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "zhipu": {
        "label": "智谱",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4.6v-flash",
        "base_url_env": "ZHIPU_MULTIMODAL_BASE_URL",
        "model_env": "ZHIPU_MULTIMODAL_MODEL",
        "api_key_env": "ZHIPU_API_KEY",
    },
    "qwen": {
        "label": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-vl-max-latest",
        "base_url_env": "DASHSCOPE_BASE_URL",
        "model_env": "QWEN_VL_MODEL",
        "api_key_env": "DASHSCOPE_API_KEY",
    },
    "siliconflow": {
        "label": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1/chat/completions",
        "model": "Qwen/Qwen2-VL-72B-Instruct",
        "base_url_env": "SILICONFLOW_BASE_URL",
        "model_env": "SILICONFLOW_MULTIMODAL_MODEL",
        "api_key_env": "SILICONFLOW_API_KEY",
    },
}

MULTIMODAL_PROVIDER_ALIASES = {
    "dashscope": "qwen",
    "qwen_dashscope": "qwen",
    "silicon-flow": "siliconflow",
    "silicon_flow": "siliconflow",
}


def normalize_multimodal_provider_name(provider_name: Optional[str] = None) -> str:
    raw = (provider_name or os.getenv("MULTIMODAL_PROVIDER") or "zhipu").strip().lower()
    return MULTIMODAL_PROVIDER_ALIASES.get(raw, raw if raw in MULTIMODAL_PROVIDER_DEFAULTS else "zhipu")


def get_multimodal_provider_defaults(provider_name: Optional[str] = None) -> Dict[str, str]:
    provider = normalize_multimodal_provider_name(provider_name)
    defaults = MULTIMODAL_PROVIDER_DEFAULTS[provider]
    return {
        "provider": provider,
        "label": defaults["label"],
        "base_url": os.getenv(defaults["base_url_env"], defaults["base_url"]),
        "model": os.getenv(defaults["model_env"], defaults["model"]),
        "api_key_env": defaults["api_key_env"],
    }


class MultimodalProvider(ABC):
    """Provider abstraction for vision-capable large models."""

    name: str = "unknown"

    @abstractmethod
    async def analyze_pages(
        self,
        images: List[PageImageInput],
        prompt: str,
        json_schema: Optional[Dict[str, Any]],
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze multiple page images and return structured JSON data."""


class OpenAICompatibleMultimodalProvider(MultimodalProvider):
    """Multimodal provider for OpenAI-compatible chat completions endpoints."""

    name = "openai_compatible"

    def __init__(self) -> None:
        self.timeout_sec = int(os.getenv("MULTIMODAL_AUDIT_TIMEOUT_SEC", "90") or "90")
        self.retry_count = int(os.getenv("MULTIMODAL_AUDIT_RETRY", "1") or "1")

    async def analyze_pages(
        self,
        images: List[PageImageInput],
        prompt: str,
        json_schema: Optional[Dict[str, Any]],
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = get_multimodal_provider_defaults(provider_name)
        provider = resolved["provider"]
        final_api_key = (api_key or os.getenv(resolved["api_key_env"]) or "").strip()
        if not final_api_key:
            raise RuntimeError(f"{resolved['label']} 多模态 API Key 未配置。")

        final_model = (model or resolved["model"]).strip() or resolved["model"]
        final_base_url = (base_url or resolved["base_url"]).strip() or resolved["base_url"]

        payload = self._build_payload(
            images=images,
            prompt=prompt,
            json_schema=json_schema,
            model=final_model,
            provider=provider,
        )
        headers = {
            "Authorization": f"Bearer {final_api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        attempts = max(1, self.retry_count + 1)
        for _ in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=float(self.timeout_sec)) as client:
                    response = await client.post(final_base_url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                return self._extract_structured_json(data, free_text=(json_schema is None), provider=provider)
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(self._format_http_status_error(exc, resolved["label"])) from exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

        raise RuntimeError(f"{resolved['label']} 多模态调用失败: {last_exc}") from last_exc

    def _build_payload(
        self,
        *,
        images: List[PageImageInput],
        prompt: str,
        json_schema: Optional[Dict[str, Any]],
        model: str,
        provider: str,
    ) -> Dict[str, Any]:
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in images:
            content.append({"type": "text", "text": f"[PAGE {image.page}]"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image.image_base64}"},
                }
            )

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 4096,
        }
        if json_schema is not None and provider != "zhipu":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _extract_structured_json(
        self,
        raw: Dict[str, Any],
        *,
        free_text: bool = False,
        provider: str,
    ) -> Dict[str, Any]:
        choices = raw.get("choices") or []
        if not choices:
            raise ValueError(f"{provider} response has no choices.")
        message = choices[0].get("message") or {}
        content_raw = message.get("content")
        text = self._content_to_text(content_raw).strip()
        if not text:
            raise ValueError(f"{provider} response content is empty.")

        if free_text:
            return {"answer": text}

        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            return "\n".join(parts)
        return str(content or "")

    def _format_http_status_error(self, exc: httpx.HTTPStatusError, provider_label: str) -> str:
        response = exc.response
        status = response.status_code
        payload: Dict[str, Any] | None = None
        body_text = ""
        code = ""
        message = ""
        request_id = ""

        try:
            payload = response.json()
        except ValueError:
            payload = None
            try:
                body_text = response.text.strip()
            except Exception:  # noqa: BLE001
                body_text = ""

        if isinstance(payload, dict):
            error_payload = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            if isinstance(error_payload, dict):
                code = str(
                    error_payload.get("code")
                    or payload.get("code")
                    or error_payload.get("type")
                    or payload.get("type")
                    or ""
                ).strip()
                message = str(
                    error_payload.get("message")
                    or payload.get("message")
                    or error_payload.get("msg")
                    or payload.get("msg")
                    or error_payload.get("detail")
                    or payload.get("detail")
                    or ""
                ).strip()
                request_id = str(
                    payload.get("request_id")
                    or error_payload.get("request_id")
                    or response.headers.get("x-request-id")
                    or response.headers.get("request-id")
                    or ""
                ).strip()

        if not message:
            message = body_text or str(exc)
        message = re.sub(r"\s+", " ", message).strip()

        headline = f"{provider_label} 接口返回 HTTP {status}"
        if code:
            headline += f"（错误码: {code}）"

        parts = [headline]
        if message:
            parts.append(message)

        status_hint = self._http_status_hint(status)
        if status_hint and status_hint not in message:
            parts.append(status_hint)

        if request_id:
            parts.append(f"request_id: {request_id}")

        return "；".join(parts)

    def _http_status_hint(self, status: int) -> str:
        hints = {
            400: "请求参数无效，请检查模型名、消息格式和图片内容",
            401: "鉴权失败，请检查 API Key 是否正确且仍然有效",
            403: "当前账户无权访问该模型或该资源",
            404: "请求的模型或接口地址不存在，请检查 Base URL 和模型名",
            429: "请求过于频繁、并发超限或账户额度异常，请稍后重试并检查供应商控制台限额与余额",
            500: "模型服务内部错误，请稍后重试",
            502: "模型网关暂时不可用，请稍后重试",
            503: "模型服务暂时不可用，请稍后重试",
            504: "模型服务处理超时，请稍后重试",
        }
        return hints.get(status, "")


_openai_compatible_multimodal_provider = OpenAICompatibleMultimodalProvider()


def get_multimodal_provider(provider_name: Optional[str] = None) -> MultimodalProvider:
    """Resolve provider by name, defaulting to the configured multimodal provider."""

    normalize_multimodal_provider_name(provider_name)
    return _openai_compatible_multimodal_provider
