from __future__ import annotations

import json
from typing import Any

import httpx


async def chat_completion(
    *,
    api_key: str,
    model: str,
    base_url: str,
    system: str,
    user: str,
    timeout_seconds: float = 60.0,
    max_tokens: int | None = None,
    include_reasoning: bool | None = None,
    response_format: dict[str, str] | None = None,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://heatshield.local",
        "X-Title": "HeatShield",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if include_reasoning is not None:
        payload["include_reasoning"] = include_reasoning
    if response_format is not None:
        payload["response_format"] = response_format
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("OpenRouter response did not include a completion choice")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("OpenRouter response did not include a completion message")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenRouter response did not include text content")
        return content


def parse_explanation_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    return json.loads(text)
