"""LLM client for the cascade.

Talks to an OpenAI-compatible endpoint, which is what `llama-server` from
llama.cpp exposes. That indirection is the point: Track M trains with QLoRA on
Colab, merges, converts to GGUF, and serves locally — and nothing in the
application changes when the fine-tuned checkpoint replaces the base one.

Why not bitsandbytes in-process: the RTX 5050 is Blackwell (`sm_120`), and
bitsandbytes support there is the most fragile link in the stack. llama.cpp has
solid Blackwell support, and running the cascade on CPU keeps the GPU free for
Moshi anyway.

`variant` threads through every call so the M9 base-vs-fine-tuned comparison can
hit both checkpoints over the same prompts without a redeploy.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.errors import ModelUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class Message:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    variant: str = "base"
    #: Time to first token, the number that matters for perceived latency (M10).
    ttft_ms: float | None = None


class LLMService:
    """Thin async client with one shared connection pool."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=settings.llm_base_url.rstrip("/"),
                timeout=httpx.Timeout(settings.llm_timeout_s, connect=5.0),
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _payload(
        self,
        messages: list[Message],
        *,
        stream: bool,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, object]:
        return {
            "model": settings.llm_model,
            "messages": [m.as_dict() for m in messages],
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "temperature": (
                temperature if temperature is not None else settings.llm_temperature
            ),
            "stream": stream,
        }

    async def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        variant: str | None = None,
    ) -> Completion:
        body = self._payload(
            messages, stream=False, max_tokens=max_tokens, temperature=temperature
        )
        try:
            resp = await self._http().post("/chat/completions", json=body)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise ModelUnavailableError(
                "The coaching model isn't running. Start llama-server with the "
                "GGUF checkpoint, or check SCC_LLM_BASE_URL."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ModelUnavailableError(
                f"The coaching model returned {exc.response.status_code}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ModelUnavailableError(
                "The coaching model took too long to respond."
            ) from exc

        data = resp.json()
        usage = data.get("usage") or {}
        return Completion(
            text=(data["choices"][0]["message"]["content"] or "").strip(),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            variant=variant or settings.llm_variant,
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Yield content deltas as they arrive.

        Streaming is what keeps the cascade tolerable: the first sentence can be
        synthesized and playing while the rest is still being generated.
        """
        body = self._payload(
            messages, stream=True, max_tokens=max_tokens, temperature=temperature
        )
        try:
            async with self._http().stream("POST", "/chat/completions", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    if delta := (choices[0].get("delta") or {}).get("content"):
                        yield delta
        except httpx.ConnectError as exc:
            raise ModelUnavailableError(
                "The coaching model isn't running. Start llama-server with the "
                "GGUF checkpoint, or check SCC_LLM_BASE_URL."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ModelUnavailableError(
                f"The coaching model returned {exc.response.status_code}."
            ) from exc

    async def health(self) -> bool:
        try:
            resp = await self._http().get("/models", timeout=3.0)
            return resp.status_code < 500
        except Exception:
            return False


llm_service = LLMService()
