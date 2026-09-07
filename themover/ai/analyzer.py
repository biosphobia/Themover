"""Turn a :class:`Recording` into a mapping :class:`Profile` with Claude."""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from themover.ai.client import ClaudeClient, extract_json_object, text_of
from themover.ai.prompts import ANALYSIS_INSTRUCTIONS, system_prompt
from themover.ai.recorder import Recording
from themover.mapping.profile import Profile, profile_json_schema

log = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    profile: Profile
    problems: list[str]
    raw_text: str
    model: str = ""
    usage: Optional[dict[str, Any]] = None


def build_user_content(recording: Recording, max_frames: int = 12, current_profile: Optional[Profile] = None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    frames = recording.pick_frames(max_frames)
    for i, f in enumerate(frames):
        content.append({"type": "text", "text": f"Screenshot {i + 1}/{len(frames)} at t={f.t:.0f}s:"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(f.jpeg).decode("ascii")},
            }
        )
    text = "INPUT SUMMARY\n" + recording.summary_text() + "\n\n"
    if current_profile is not None and current_profile.bindings:
        text += "The player currently uses this profile (improve on it if it fits, otherwise replace it):\n" + current_profile.to_json(indent=None) + "\n\n"
    text += ANALYSIS_INSTRUCTIONS
    content.append({"type": "text", "text": text})
    return content


class GameAnalyzer:
    def __init__(self, client: ClaudeClient) -> None:
        self.client = client

    def analyze(
        self,
        recording: Recording,
        max_frames: int = 12,
        current_profile: Optional[Profile] = None,
        on_text: Optional[Callable[[str], None]] = None,
    ) -> AnalysisResult:
        messages = [{"role": "user", "content": build_user_content(recording, max_frames, current_profile)}]
        system = system_prompt()
        schema = profile_json_schema()
        try:
            message = self.client.stream_message(system, messages, max_tokens=16000, output_schema=schema, on_text=on_text)
        except Exception as exc:
            # Structured output can be rejected in some configurations; fall back to plain JSON in text.
            if not _is_bad_request(exc):
                raise
            log.warning("structured output rejected (%s); retrying as plain text", exc)
            messages[0]["content"][-1]["text"] += "\nRespond with the JSON object only, no prose, no code fences."
            message = self.client.stream_message(system, messages, max_tokens=16000, on_text=on_text)
        if getattr(message, "stop_reason", "") == "refusal":
            raise RuntimeError("Claude declined to analyse this recording.")
        raw = text_of(message)
        data = extract_json_object(raw)
        profile = Profile.from_dict(data)
        problems = profile.validate()
        if problems:
            log.warning("profile problems: %s", problems)
            profile = profile.sanitized()
        usage = getattr(message, "usage", None)
        return AnalysisResult(
            profile=profile,
            problems=problems,
            raw_text=raw,
            model=getattr(message, "model", "") or "",
            usage=usage.model_dump() if hasattr(usage, "model_dump") else None,
        )


def _is_bad_request(exc: Exception) -> bool:
    try:
        import anthropic

        return isinstance(exc, anthropic.BadRequestError)
    except Exception:  # pragma: no cover
        return False
