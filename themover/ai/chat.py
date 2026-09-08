"""Live chat with Claude that can edit the active mapping profile through tools."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional, Protocol

from themover.ai.client import ClaudeClient, content_to_dicts, text_of
from themover.ai.coachlog import CoachLog, _thinking_text
from themover.ai.prompts import CHAT_INSTRUCTIONS, system_prompt
from themover.mapping.profile import Binding, FeedbackRule, Profile

log = logging.getLogger(__name__)


class ProfileHost(Protocol):
    """What the chat needs from the application."""

    def get_profile(self) -> Profile: ...
    def apply_profile(self, profile: Profile) -> None: ...
    def live_signals(self) -> dict[str, float]: ...
    def buzz(self, controller: int, rumble: float, led: Optional[tuple[int, int, int]], duration_ms: int) -> None: ...
    def save_profile(self, name: str) -> str: ...


_BINDING_PROPS = {
    "source": {"type": "string"}, "target": {"type": "string"}, "mode": {"type": "string"},
    "threshold": {"type": "number"}, "compare": {"type": "string"},
    "input_range": {"type": "array", "items": {"type": "number"}}, "deadzone": {"type": "number"},
    "scale": {"type": "number"}, "invert": {"type": "boolean"}, "curve": {"type": "string"},
    "smoothing": {"type": "number"}, "tap_ms": {"type": "integer"}, "repeat_ms": {"type": "integer"},
    "comment": {"type": "string"}, "enabled": {"type": "boolean"},
}
_FEEDBACK_PROPS = {
    "when": {"type": "string"}, "controller": {"type": "integer"}, "rumble": {"type": "number"},
    "duration_ms": {"type": "integer"}, "led": {"type": "array", "items": {"type": "integer"}},
    "led_duration_ms": {"type": "integer"}, "threshold": {"type": "number"}, "rumble_from": {"type": "string"},
    "comment": {"type": "string"},
}

TOOLS: list[dict[str, Any]] = [
    {"name": "get_profile", "description": "Return the currently active mapping profile as JSON, with binding indices (every profile, built-in or custom, is editable; edits are saved automatically).",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_live_signals", "description": "Read the live values of the controller signals right now (orientation, tracking, trigger, pressed buttons, wheel angle). Useful to check calibration or whether a gesture registers.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "add_bindings", "description": "Append bindings to the active profile. Each needs source and target; other fields are optional (see vocabulary).",
     "input_schema": {"type": "object", "properties": {"bindings": {"type": "array", "items": {"type": "object", "properties": _BINDING_PROPS, "required": ["source", "target"], "additionalProperties": False}}}, "required": ["bindings"], "additionalProperties": False}},
    {"name": "modify_binding", "description": "Change fields of one existing binding (by index from get_profile).",
     "input_schema": {"type": "object", "properties": {"index": {"type": "integer"}, "changes": {"type": "object", "properties": _BINDING_PROPS, "additionalProperties": False}}, "required": ["index", "changes"], "additionalProperties": False}},
    {"name": "remove_bindings", "description": "Remove bindings by index, or every binding whose source or target matches.",
     "input_schema": {"type": "object", "properties": {"indices": {"type": "array", "items": {"type": "integer"}}, "sources": {"type": "array", "items": {"type": "string"}}, "targets": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False}},
    {"name": "set_feedback", "description": "Replace the rumble/LED feedback rules of the profile.",
     "input_schema": {"type": "object", "properties": {"feedback": {"type": "array", "items": {"type": "object", "properties": _FEEDBACK_PROPS, "additionalProperties": False}}}, "required": ["feedback"], "additionalProperties": False}},
    {"name": "set_profile_meta", "description": "Update name, play_style, notes, gesture_sensitivity (0.5 = easier gestures, 1.5 = harder), gesture_cooldown_ms (min ms between repeated gestures, 80-100 for drumming) or sphere colours.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "play_style": {"type": "string"}, "notes": {"type": "string"}, "gesture_sensitivity": {"type": "number"}, "gesture_cooldown_ms": {"type": "integer"}, "controller_colors": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}}, "additionalProperties": False}},
    {"name": "replace_profile", "description": "Replace the whole profile with a new one (same JSON shape as get_profile). Use only for big redesigns.",
     "input_schema": {"type": "object", "properties": {"profile": {"type": "object"}}, "required": ["profile"], "additionalProperties": False}},
    {"name": "buzz_controller", "description": "Pulse rumble and/or flash the sphere of a controller so the player can identify it or feel a setting.",
     "input_schema": {"type": "object", "properties": {"controller": {"type": "integer"}, "rumble": {"type": "number"}, "led": {"type": "array", "items": {"type": "integer"}}, "duration_ms": {"type": "integer"}}, "required": ["controller"], "additionalProperties": False}},
    {"name": "save_profile", "description": "Edits are already saved automatically to the active profile. Use this only to store a COPY of the active profile under a new name (e.g. before a big experiment).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}},
]


class CoachChat:
    def __init__(self, client: ClaudeClient, host: ProfileHost, max_tool_rounds: int = 8, coach_log: Optional[CoachLog] = None) -> None:
        self.client = client
        self.host = host
        self.messages: list[dict[str, Any]] = []
        self.max_tool_rounds = max_tool_rounds
        self.on_tool: Optional[Callable[[str, dict[str, Any], str], None]] = None
        self.last_usage: Optional[dict[str, Any]] = None
        self.coach_log = coach_log

    def reset(self) -> None:
        self.messages = []

    # ----------------------------------------------------------------- run
    def send(self, user_text: str, on_text: Optional[Callable[[str], None]] = None) -> str:
        system = system_prompt() + "\n" + CHAT_INSTRUCTIONS
        self.messages.append({"role": "user", "content": user_text})
        final_text = ""
        t0 = time.monotonic()
        tool_log: list[dict[str, Any]] = []
        thinking_parts: list[str] = []
        model = ""
        for _round in range(self.max_tool_rounds + 1):
            try:
                message = self.client.stream_message(system, self.messages, max_tokens=8000, tools=TOOLS, on_text=on_text)
            except Exception as exc:
                if self.coach_log:
                    self.coach_log.log_error("chat", str(exc), user=user_text)
                raise
            usage = getattr(message, "usage", None)
            self.last_usage = usage.model_dump() if hasattr(usage, "model_dump") else None
            model = getattr(message, "model", "") or model
            thought = _thinking_text(message)
            if thought:
                thinking_parts.append(thought)
            self.messages.append({"role": "assistant", "content": content_to_dicts(message.content)})
            final_text += text_of(message)
            if message.stop_reason == "refusal":
                final_text += "\n(Claude declined this request.)"
                break
            tool_uses = [b for b in message.content if getattr(b, "type", "") == "tool_use"]
            if message.stop_reason != "tool_use" or not tool_uses:
                break
            results = []
            for tu in tool_uses:
                tool_input = tu.input if isinstance(tu.input, dict) else json.loads(tu.input or "{}")
                try:
                    out = self.execute_tool(tu.name, tool_input)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
                except Exception as exc:
                    log.exception("tool %s failed", tu.name)
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": f"Error: {exc}", "is_error": True})
                    out = f"Error: {exc}"
                tool_log.append({"name": tu.name, "input": tool_input, "result": out[:400]})
                if self.on_tool:
                    try:
                        self.on_tool(tu.name, tool_input, out)
                    except Exception:
                        pass
            self.messages.append({"role": "user", "content": results})
        if self.coach_log:
            try:
                profile = self.host.get_profile()
                self.coach_log.log_chat(
                    game=profile.game, profile_name=profile.name, user_text=user_text, reply=final_text.strip(),
                    tool_calls=tool_log, thinking="\n\n".join(thinking_parts), model=model, usage=self.last_usage,
                    seconds=time.monotonic() - t0,
                )
            except Exception as exc:  # logging must never break the chat
                log.warning("coach log failed: %s", exc)
        return final_text.strip()

    # --------------------------------------------------------------- tools
    def execute_tool(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            raise ValueError(f"unknown tool {name}")
        return handler(**args)

    def _apply(self, profile: Profile) -> str:
        problems = profile.validate()
        clean = profile.sanitized() if problems else profile
        self.host.apply_profile(clean)
        msg = f"Applied. Profile now has {len(clean.bindings)} bindings and {len(clean.feedback)} feedback rules."
        if problems:
            msg += " Dropped invalid entries: " + "; ".join(problems)
        return msg

    def _tool_get_profile(self) -> str:
        p = self.host.get_profile()
        data = p.to_dict()
        for i, b in enumerate(data["bindings"]):
            b["index"] = i
        return json.dumps(data)

    def _tool_read_live_signals(self) -> str:
        return json.dumps(self.host.live_signals())

    def _tool_add_bindings(self, bindings: list[dict[str, Any]]) -> str:
        p = self.host.get_profile().copy()
        for b in bindings:
            p.bindings.append(Binding.from_dict(b))
        return self._apply(p)

    def _tool_modify_binding(self, index: int, changes: dict[str, Any]) -> str:
        p = self.host.get_profile().copy()
        if index < 0 or index >= len(p.bindings):
            raise IndexError(f"no binding with index {index} (profile has {len(p.bindings)})")
        merged = p.bindings[index].to_dict()
        merged.update(changes)
        p.bindings[index] = Binding.from_dict(merged)
        return self._apply(p)

    def _tool_remove_bindings(self, indices: Optional[list[int]] = None, sources: Optional[list[str]] = None, targets: Optional[list[str]] = None) -> str:
        p = self.host.get_profile().copy()
        idx = set(indices or [])
        srcs = set(sources or [])
        tgts = set(targets or [])
        before = len(p.bindings)
        p.bindings = [b for i, b in enumerate(p.bindings) if i not in idx and b.source not in srcs and b.target not in tgts]
        return f"Removed {before - len(p.bindings)} binding(s). " + self._apply(p)

    def _tool_set_feedback(self, feedback: list[dict[str, Any]]) -> str:
        p = self.host.get_profile().copy()
        p.feedback = [FeedbackRule.from_dict(f) for f in feedback]
        return self._apply(p)

    def _tool_set_profile_meta(self, name: Optional[str] = None, play_style: Optional[str] = None, notes: Optional[str] = None,
                               gesture_sensitivity: Optional[float] = None, gesture_cooldown_ms: Optional[int] = None,
                               controller_colors: Optional[list[list[int]]] = None) -> str:
        p = self.host.get_profile().copy()
        if name is not None:
            p.name = name
        if play_style is not None:
            p.play_style = play_style
        if notes is not None:
            p.notes = notes
        if gesture_sensitivity is not None:
            p.gesture_sensitivity = max(0.2, min(3.0, float(gesture_sensitivity)))
        if gesture_cooldown_ms is not None:
            p.gesture_cooldown_ms = max(30, min(2000, int(gesture_cooldown_ms)))
        if controller_colors:
            for i, c in enumerate(controller_colors[:2]):
                p.controllers[i].color = [int(x) for x in c][:3]
        return self._apply(p)

    def _tool_replace_profile(self, profile: dict[str, Any]) -> str:
        return self._apply(Profile.from_dict(profile))

    def _tool_buzz_controller(self, controller: int, rumble: float = 0.8, led: Optional[list[int]] = None, duration_ms: int = 300) -> str:
        self.host.buzz(int(controller), float(rumble), tuple(led) if led else None, int(duration_ms))  # type: ignore[arg-type]
        return f"Buzzed controller {controller}."

    def _tool_save_profile(self, name: str) -> str:
        return self.host.save_profile(name)
