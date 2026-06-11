from __future__ import annotations

import os
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class ChatResult(dict):
    @property
    def content(self) -> str:
        return str(self.get("content") or "")


def resolve_config(settings: dict[str, Any] | None = None) -> dict[str, str]:
    settings = settings or {}
    api_base = (
        settings.get("api_base")
        or os.getenv("APD_API_BASE")
        or os.getenv("OPENAI_API_BASE")
        or os.getenv("STORM_API_BASE")
        or ""
    ).rstrip("/")
    api_key = (
        settings.get("api_key")
        or os.getenv("APD_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("STORM_API_KEY")
        or ""
    )
    model = (
        settings.get("model")
        or os.getenv("APD_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("STORM_MODEL")
        or "gpt-5.5"
    )
    return {"api_base": api_base, "api_key": api_key, "model": model}


async def chat_json_result(messages: list[dict[str, str]], settings: dict[str, Any] | None = None) -> ChatResult:
    cfg = resolve_config(settings)
    if not cfg["api_base"] or not cfg["api_key"]:
        raise LLMError("missing api_base or api_key")

    url = f'{cfg["api_base"]}/chat/completions'
    headers = {
        "Authorization": f'Bearer {cfg["api_key"]}',
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": int(settings.get("max_tokens") or os.getenv("APD_MAX_TOKENS", "8000")),
    }
    if str(settings.get("response_format") or os.getenv("APD_RESPONSE_FORMAT", "0")) == "1":
        payload["response_format"] = {"type": "json_object"}
    timeout_seconds = float(settings.get("timeout") or os.getenv("APD_LLM_TIMEOUT", "120"))
    timeout = httpx.Timeout(timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise LLMError(f"LLM HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        choice = data["choices"][0]
        return ChatResult({
            "content": choice["message"].get("content") or "",
            "finish_reason": choice.get("finish_reason"),
            "model": data.get("model"),
            "usage": data.get("usage"),
            "status_code": response.status_code,
            "max_tokens": payload.get("max_tokens"),
        })
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"unexpected LLM response: {data}") from exc


async def chat_json(messages: list[dict[str, str]], settings: dict[str, Any] | None = None) -> str:
    return (await chat_json_result(messages, settings=settings)).content
