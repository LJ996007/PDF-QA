from app.services.qwen_dashscope_provider import QwenDashScopeProvider


def test_extract_structured_json_supports_markdown_fence():
    provider = QwenDashScopeProvider()
    raw = {
        "choices": [
            {
                "message": {
                    "content": "```json\n{\"facts\": [{\"page\": 1, \"key\": \"contract_sign_date\", \"value\": \"2026-01-01\", \"evidence_text\": \"2026-01-01\", \"confidence\": 0.8}]}\n```"
                }
            }
        ]
    }
    parsed = provider._extract_structured_json(raw)
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("facts"), list)
