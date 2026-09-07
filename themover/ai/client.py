"""Thin wrapper around the Anthropic SDK with the settings The Mover uses."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterable, Optional

from themover.config import Settings

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5-1")


class ClaudeClient:
    """Builds requests consistently (model, effort, thinking, fallbacks, caching)."""

    def __init__(self, settings: Settings, client: Any = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            key = self.settings.effective_api_key
            if not key:
                raise RuntimeError("No API key. Open Settings and paste your Anthropic API key.")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _base_kwargs(self, system: str, messages: list[dict[str, Any]], max_tokens: int, tools: Optional[list] = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.settings.effort},
        }
        if tools:
            kwargs["tools"] = tools
        return kwargs

    def _use_fallback(self) -> bool:
        return self.settings.refusal_fallback and self.settings.model in FALLBACK_MODELS

    def stream_message(
        self,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 16000,
        tools: Optional[list] = None,
        output_schema: Optional[dict[str, Any]] = None,
        on_text: Optional[Callable[[str], None]] = None,
    ):
        """Stream a response, forwarding text deltas; returns the final message."""
        kwargs = self._base_kwargs(system, messages, max_tokens, tools)
        if output_schema is not None:
            kwargs["output_config"]["format"] = {"type": "json_schema", "schema": output_schema}
        api = self.client.messages
        if self._use_fallback():
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
            api = self.client.beta.messages
        with api.stream(**kwargs) as stream:
            for text in stream.text_stream:
                if on_text is not None:
                    on_text(text)
            return stream.get_final_message()

    def test_connection(self) -> str:
        """Cheap round-trip used by the Settings 'Test key' button."""
        response = self.client.messages.create(
            model=self.settings.model,
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return text.strip() or f"stop_reason={response.stop_reason}"


def text_of(message) -> str:
    return "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text")


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse the first JSON object found in ``text`` (tolerates ```json fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in response")


def content_to_dicts(content: Iterable[Any]) -> list[dict[str, Any]]:
    """Serialise SDK content blocks so history can be stored/replayed as plain dicts."""
    out: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            out.append(block)
        elif hasattr(block, "model_dump"):
            out.append(block.model_dump(exclude_none=True))
        else:
            out.append({"type": "text", "text": str(block)})
    return out
