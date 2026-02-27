"""DashScope Qwen multimodal provider."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from app.services.mm_provider import MultimodalProvider, PageImageInput


class QwenDashScopeProvider(MultimodalProvider):
    name = "dashscope"

    def __init__(self) -> None:
        self.base_url = os.getenv(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        )
        self.default_model = os.getenv("QWEN_VL_MODEL", "qwen-vl-max-latest")
        self.timeout_sec = int(os.getenv("MULTIMODAL_AUDIT_TIMEOUT_SEC", "90") or "90")
        self.retry_count = int(os.getenv("MULTIMODAL_AUDIT_RETRY", "1") or "1")

    async def analyze_pages(
        self,
        images: List[PageImageInput],
        prompt: str,
        json_schema: Dict[str, Any],
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        final_api_key = (api_key or os.getenv("DASHSCOPE_API_KEY") or "").strip()
        if not final_api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured.")

        final_model = (model or self.default_model).strip() or self.default_model
        payload = self._build_payload(
            images=images,
            prompt=prompt,
            json_schema=json_schema,
            model=final_model,
            include_schema=True,
        )
        fallback_payload = self._build_payload(
            images=images,
            prompt=prompt,
            json_schema=json_schema,
            model=final_model,
            include_schema=False,
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
                    response = await client.post(self.base_url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                return self._extract_structured_json(data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {400, 422}:
                    async with httpx.AsyncClient(timeout=float(self.timeout_sec)) as client:
                        response = await client.post(self.base_url, headers=headers, json=fallback_payload)
                        response.raise_for_status()
                        data = response.json()
                    return self._extract_structured_json(data)
                last_exc = exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

        raise RuntimeError(f"DashScope multimodal call failed after retries: {last_exc}") from last_exc

    def _build_payload(
        self,
        *,
        images: List[PageImageInput],
        prompt: str,
        json_schema: Dict[str, Any],
        model: str,
        include_schema: bool,
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

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 4096,
        }
        if include_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "multimodal_audit", "schema": json_schema},
            }
        return payload

    def _extract_structured_json(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        choices = raw.get("choices") or []
        if not choices:
            raise ValueError("DashScope response has no choices.")
        message = choices[0].get("message") or {}
        content_raw = message.get("content")
        text = self._content_to_text(content_raw).strip()
        if not text:
            raise ValueError("DashScope response content is empty.")

        # Some models still wrap JSON in markdown fences.
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


qwen_dashscope_provider = QwenDashScopeProvider()
