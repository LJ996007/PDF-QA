"""
OpenAI-compatible vision gateway (on-demand).

This calls an OpenAI-compatible `/v1/chat/completions` endpoint with an image input
and returns the model's text description.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx


def normalize_base_url(base_url: str) -> str:
    """
    Accepts either:
    - https://host/v1
    - https://host
    Returns a base url that ends with /v1.
    """
    if not base_url:
        return ""
    b = base_url.rstrip("/")
    if b.endswith("/v1"):
        return b
    return b + "/v1"


def build_chat_completions_payload(model: str, prompt: str, image_b64_png: str) -> Dict[str, Any]:
    image_url = f"data:image/png;base64,{image_b64_png}"
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }


async def describe_page_image(
    *,
    image_b64_png: str,
    prompt: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout_s: float = 60.0,
) -> str:
    """
    Returns a short textual description (Chinese preferred by prompt).
    """
    final_api_key = api_key or os.getenv("VISION_API_KEY") or ""
    final_base_url = normalize_base_url(base_url or os.getenv("VISION_BASE_URL") or "")
    final_model = model or os.getenv("VISION_MODEL") or ""

    if not final_api_key:
        raise ValueError("VISION api_key is missing")
    if not final_base_url:
        raise ValueError("VISION base_url is missing")
    if not final_model:
        raise ValueError("VISION model is missing")

    url = f"{final_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {final_api_key}",
        "Content-Type": "application/json",
    }
    payload = build_chat_completions_payload(final_model, prompt, image_b64_png)

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Invalid vision response shape: {e}") from e

    return (content or "").strip()

