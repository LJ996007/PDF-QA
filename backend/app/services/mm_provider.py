"""Multimodal provider abstraction."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class PageImageInput:
    """Image payload for one PDF page."""

    page: int
    image_base64: str
    width: float
    height: float


class MultimodalProvider(ABC):
    """Provider abstraction for vision-capable large models."""

    name: str = "unknown"

    @abstractmethod
    async def analyze_pages(
        self,
        images: List[PageImageInput],
        prompt: str,
        json_schema: Dict[str, Any],
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze multiple page images and return structured JSON data."""


def get_multimodal_provider(provider_name: Optional[str] = None) -> MultimodalProvider:
    """Resolve provider by name, defaulting to env-configured provider."""

    resolved = (provider_name or os.getenv("MULTIMODAL_PROVIDER") or "dashscope").strip().lower()
    if resolved in {"dashscope", "qwen", "qwen_dashscope"}:
        from app.services.qwen_dashscope_provider import qwen_dashscope_provider

        return qwen_dashscope_provider
    raise ValueError(f"Unsupported multimodal provider: {resolved}")
