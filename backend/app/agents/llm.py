"""LLM client for the Tender Co-Pilot agents.

Configuration comes from environment only (``LLM_API_KEY``, ``LLM_BASE_URL``,
``LLM_MODEL`` via ``app.config.settings`` — imported lazily so this module
imports without the config layer). OpenAI-compatible ``/chat/completions``
API. No key handling beyond env: the key is read once and used in the
Authorization header; it is never logged or persisted.

``available`` is False when no key is set; every agent must treat that as
"LLM unavailable" and run its deterministic fallback.
"""

from __future__ import annotations


class LLMError(Exception):
    """Raised when an LLM call fails (no key, network, HTTP error, bad payload)."""


class LLMClient:
    def __init__(self, api_key: str | None = None,
                 base_url: str | None = None,
                 model: str | None = None):
        from app.config import settings  # lazy: config owned by another stream

        self.api_key = api_key if api_key is not None else settings.LLM_API_KEY
        self.base_url = (base_url if base_url is not None
                         else settings.LLM_BASE_URL).rstrip("/")
        self.model = model if model is not None else settings.LLM_MODEL

    @property
    def available(self) -> bool:
        """True when an API key is configured (LLM may be used)."""
        return bool(self.api_key)

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def chat(self, system: str, user: str, max_tokens: int = 1500) -> str:
        """Single-turn chat completion. Raises ``LLMError`` on any failure."""
        if not self.available:
            raise LLMError("LLM_API_KEY is not set; LLM unavailable")

        import httpx  # lazy: heavy-ish dep only needed when actually calling

        try:
            resp = httpx.post(
                self._endpoint(),
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
                timeout=60,
            )
            resp.raise_for_status()
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        try:
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"Unexpected LLM response shape: {exc}") from exc
