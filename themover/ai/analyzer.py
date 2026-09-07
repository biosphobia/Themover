"""Turn a :class:`Recording` into a mapping :class:`Profile` with Claude."""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from themover.ai.client import ClaudeClient, extract_json_object, text_of
from themover.ai.coachlog import CoachLog, _thinking_text
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
    analysis: dict[str, Any] = field(default_factory=dict)
    thinking: str = ""
    seconds: float = 0.0
    report_path: str = ""

    def analysis_text(self) -> str:
        """Short human-readable version of the analysis block."""
        a = self.analysis or {}
        parts = []
        if a.get("game"):
            parts.append(f"Game: {a['game']}" + (f" ({a['genre']})" if a.get("genre") else ""))
        if a.get("metaphor"):
            parts.append(f"Idea: {a['metaphor']}")
        if a.get("camera_used") is not None:
            parts.append("Camera: " + ("used - " + str(a.get("camera_reason", "")) if a.get("camera_used") else "not needed"))
        if a.get("playability"):
            parts.append(f"Playability: {a['playability']}")
        return "\n".join(parts)


def analysis_json_schema() -> dict[str, Any]:
    """Response schema: the coach's reasoning plus the profile."""
    analysis = {
        "type": "object",
        "properties": {
            "game": {"type": "string", "description": "title if recognisable, else a description"},
            "genre": {"type": "string"},
            "perspective": {"type": "string", "description": "first person / third person / top-down / 2D ..."},
            "inputs": {"type": "array", "items": {"type": "string"}, "description": "what each important input does, e.g. 'w: walk forward (held 80%)'"},
            "metaphor": {"type": "string", "description": "the physical metaphor chosen and why it fits"},
            "camera_used": {"type": "boolean"},
            "camera_reason": {"type": "string", "description": "why the camera is or is not used"},
            "unused_features": {"type": "string", "description": "features deliberately left out and why"},
            "playability": {"type": "string", "description": "fatigue / reliability concerns and how the design handles them"},
        },
        "required": ["game", "genre", "perspective", "inputs", "metaphor", "camera_used", "camera_reason", "unused_features", "playability"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"analysis": analysis, "profile": profile_json_schema()},
        "required": ["analysis", "profile"],
        "additionalProperties": False,
    }


def build_user_content(recording: Recording, max_frames: int = 12, current_profile: Optional[Profile] = None, lessons: str = "") -> list[dict[str, Any]]:
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
    if lessons:
        text += lessons + "\n\n"
    text += ANALYSIS_INSTRUCTIONS
    content.append({"type": "text", "text": text})
    return content


class GameAnalyzer:
    def __init__(self, client: ClaudeClient, coach_log: Optional[CoachLog] = None) -> None:
        self.client = client
        self.coach_log = coach_log

    def analyze(
        self,
        recording: Recording,
        max_frames: int = 12,
        current_profile: Optional[Profile] = None,
        on_text: Optional[Callable[[str], None]] = None,
        recording_folder: str = "",
    ) -> AnalysisResult:
        t0 = time.monotonic()
        lessons = self.coach_log.lessons_for(recording.game_hint) if self.coach_log else ""
        messages = [{"role": "user", "content": build_user_content(recording, max_frames, current_profile, lessons)}]
        system = system_prompt()
        schema = analysis_json_schema()
        try:
            message = self.client.stream_message(system, messages, max_tokens=16000, output_schema=schema, on_text=on_text)
        except Exception as exc:
            # Structured output can be rejected in some configurations; fall back to plain JSON in text.
            if not _is_bad_request(exc):
                if self.coach_log:
                    self.coach_log.log_error("analysis", str(exc), game_hint=recording.game_hint)
                raise
            log.warning("structured output rejected (%s); retrying as plain text", exc)
            messages[0]["content"][-1]["text"] += "\nRespond with one JSON object {\"analysis\": ..., \"profile\": ...} only, no prose, no code fences."
            message = self.client.stream_message(system, messages, max_tokens=16000, on_text=on_text)
        if getattr(message, "stop_reason", "") == "refusal":
            if self.coach_log:
                self.coach_log.log_error("analysis", "refusal", game_hint=recording.game_hint)
            raise RuntimeError("Claude declined to analyse this recording.")
        raw = text_of(message)
        data = extract_json_object(raw)
        analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
        profile_data = data.get("profile") if isinstance(data.get("profile"), dict) else data
        profile = Profile.from_dict(profile_data)
        problems = profile.validate()
        if problems:
            log.warning("profile problems: %s", problems)
            profile = profile.sanitized()
        usage = getattr(message, "usage", None)
        result = AnalysisResult(
            profile=profile,
            problems=problems,
            raw_text=raw,
            model=getattr(message, "model", "") or "",
            usage=usage.model_dump() if hasattr(usage, "model_dump") else None,
            analysis=analysis,
            thinking=_thinking_text(message),
            seconds=time.monotonic() - t0,
        )
        if self.coach_log:
            report = self.coach_log.log_analysis(
                game_hint=recording.game_hint, notes=recording.notes, recording_summary=recording.summary_text(),
                recording_folder=recording_folder, analysis=analysis, profile=profile.to_dict(), problems=problems,
                thinking=result.thinking, model=result.model, usage=result.usage, seconds=result.seconds, raw_text=raw,
            )
            result.report_path = str(report)
        return result


def _is_bad_request(exc: Exception) -> bool:
    try:
        import anthropic

        return isinstance(exc, anthropic.BadRequestError)
    except Exception:  # pragma: no cover
        return False
