from __future__ import annotations

import json

import httpx

from app.core.openai_usage import record_openai_usage
from app.config import Settings


class OpenAIJsonClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_available(self) -> bool:
        return bool(self.settings.openai_api_key)

    async def complete_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        usage_source: str = "llm_json",
    ) -> dict:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.settings.openai_api_base.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage", {})
        record_openai_usage(
            source=usage_source,
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens", usage.get("total_tokens", 0)) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        return json.loads(content)

    def complete_json_sync(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        usage_source: str = "llm_json",
    ) -> dict:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        payload = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.settings.openai_api_base.rstrip('/')}/chat/completions"
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage", {})
        record_openai_usage(
            source=usage_source,
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens", usage.get("total_tokens", 0)) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        return json.loads(content)
