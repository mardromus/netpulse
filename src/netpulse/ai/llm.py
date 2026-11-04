"""Minimal client for any OpenAI-compatible chat completions API.

Configuration (environment)::

    NETPULSE_LLM_API_KEY   or GROQ_API_KEY / OPENAI_API_KEY
    NETPULSE_LLM_BASE_URL  default https://api.groq.com/openai/v1
    NETPULSE_LLM_MODEL     default llama-3.3-70b-versatile

Local models work too: ``NETPULSE_LLM_BASE_URL=http://localhost:11434/v1``
(Ollama) with ``NETPULSE_LLM_MODEL=llama3.1`` and no key.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

Transport = Callable[[str, dict, dict, float], tuple[int, dict]]


class LLMError(RuntimeError):
    pass


def _httpx_transport(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict]:
    r = httpx.post(url, headers=headers, json=body, timeout=timeout)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": {"message": r.text[:300]}}


@dataclass
class LLMConfig:
    base_url: str = "https://api.groq.com/openai/v1"
    api_key: str = ""
    model: str = "llama-3.3-70b-versatile"
    timeout: float = 60.0
    temperature: float = 0.2
    transport: Transport | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> LLMConfig:
        key = os.getenv("NETPULSE_LLM_API_KEY") or os.getenv("GROQ_API_KEY") or ""
        openai = False
        if not key and os.getenv("OPENAI_API_KEY"):
            key, openai = os.environ["OPENAI_API_KEY"], True
        base = os.getenv("NETPULSE_LLM_BASE_URL") or (
            "https://api.openai.com/v1" if openai else "https://api.groq.com/openai/v1")
        model = os.getenv("NETPULSE_LLM_MODEL") or ("gpt-4o-mini" if openai else "llama-3.3-70b-versatile")
        return cls(base_url=base.rstrip("/"), api_key=key, model=model)

    @property
    def usable(self) -> bool:
        local = "localhost" in self.base_url or "127.0.0.1" in self.base_url
        return bool(self.api_key or local or self.transport)


class LLMClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    def chat(self, messages: list[dict], json_mode: bool = True) -> str:
        if not self.cfg.usable:
            raise LLMError("no LLM configured (set NETPULSE_LLM_API_KEY or GROQ_API_KEY)")
        body = {"model": self.cfg.model, "messages": messages, "temperature": self.cfg.temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        send = self.cfg.transport or _httpx_transport
        try:
            status, data = send(f"{self.cfg.base_url}/chat/completions", headers, body, self.cfg.timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"request failed: {exc}") from exc
        if status != 200:
            msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            raise LLMError(f"LLM API returned HTTP {status}: {msg}")
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape") from exc
