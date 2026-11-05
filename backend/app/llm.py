"""Thin OpenAI wrapper.

All model calls go through :class:`LLMClient.complete_json`, which uses JSON mode,
temperature 0 for determinism, SDK-level retries/timeouts, and records token
usage per workflow step. Tests inject a fake implementing the same protocol.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import AsyncOpenAI

from .config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMCall:
    purpose: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


@dataclass
class UsageTracker:
    calls: list[LLMCall] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "llm_calls": len(self.calls),
            "prompt_tokens": sum(c.prompt_tokens for c in self.calls),
            "completion_tokens": sum(c.completion_tokens for c in self.calls),
        }


class LLM(Protocol):
    async def complete_json(
        self, *, purpose: str, system: str, user: str, model: str | None = None,
        usage: UsageTracker | None = None, temperature: float = 0.0,
    ) -> dict[str, Any]: ...


class OpenAILLM:
    def __init__(self, settings: Settings) -> None:
        key = settings.openai_api_key.get_secret_value()
        self.enabled = bool(key)
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=key or "missing", timeout=settings.openai_timeout_s, max_retries=2
        )

    async def complete_json(
        self, *, purpose: str, system: str, user: str, model: str | None = None,
        usage: UsageTracker | None = None, temperature: float = 0.0,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise LLMError("OPENAI_API_KEY is not configured. Add it to .env and restart.")
        model = model or self.settings.openai_model_fast
        started = time.perf_counter()
        try:
            resp = await self.client.chat.completions.create(
                model=model,
                temperature=temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - surface a clean error to the workflow
            raise LLMError(f"OpenAI call failed during {purpose}: {exc}") from exc

        latency = int((time.perf_counter() - started) * 1000)
        content = resp.choices[0].message.content or "{}"
        if usage is not None:
            u = resp.usage
            usage.calls.append(LLMCall(
                purpose=purpose, model=model, latency_ms=latency,
                prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(u, "completion_tokens", 0) or 0,
            ))
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned invalid JSON during {purpose}") from exc
