"""Hand a tagged motion recording to the coach and give it tools to tune the profile from it."""
from __future__ import annotations

import base64
import json
from typing import Any, Callable

from themover.ai.motion_capture import MotionSession, auto_fit

_TUNING_PROPS = {
    "onset_g": {"type": "number", "description": "drum hits: acceleration (g) that starts a stroke"},
    "stop_g": {"type": "number", "description": "drum hits: how sharp the stop must be (g); lower = easier"},
    "kat_angle_deg": {"type": "number", "description": "drum hits: angle from straight-down that separates don from kat"},
    "refractory_s": {"type": "number", "description": "drum hits: minimum seconds between two hits of one hand"},
    "max_stroke_s": {"type": "number"},
    "up_angle_deg": {"type": "number"},
    "onset_frames": {"type": "integer"},
    "gesture_sensitivity": {"type": "number", "description": "gestures (swing/thrust/flick/shake): multiplies the thresholds; 0.6 easy, 1.0 normal, 1.5 needs hard moves"},
    "gesture_cooldown_ms": {"type": "integer", "description": "gestures: minimum ms between two of the same gesture"},
}
_SETTINGS_SCHEMA = {"type": "object", "properties": _TUNING_PROPS, "additionalProperties": False}

FINETUNE_TOOLS: list[dict[str, Any]] = [
    {"name": "recording_summary", "description": "Summary of the attached motion recording: rates, orientation ranges, what fired while recording, the bindings in use, the tags, and how the CURRENT tuning scores against the tags.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "tag_window", "description": "Raw samples around one tag (index from recording_summary): accel, gyro, orientation, trigger, camera position, plus hits / gestures / mapping actions / player inputs near it.",
     "input_schema": {"type": "object", "properties": {"index": {"type": "integer"}, "half_ms": {"type": "number", "description": "window half-width in ms (default 150)"}}, "required": ["index"], "additionalProperties": False}},
    {"name": "get_tuning", "description": "The detector tuning in use: drum-hit settings (onset_g, stop_g, kat_angle_deg, refractory_s, ...) and gesture_sensitivity / gesture_cooldown_ms.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "evaluate_tuning", "description": "Replay the recording through the hit and gesture detectors with candidate tuning and score it against the tags (matched / missed / wrong kind / extra, timing offset). Call repeatedly to search; nothing is applied.",
     "input_schema": {"type": "object", "properties": {"settings": _SETTINGS_SCHEMA,
                                                       "complete": {"type": "boolean", "description": "true if the player tagged every moment that should fire (then extra detections count as false positives)"}},
                      "required": ["settings"], "additionalProperties": False}},
    {"name": "auto_fit_tuning", "description": "Local coordinate search over the tuning fields the tags use (hit settings for don/kat tags, gesture sensitivity/cooldown for gesture tags) that maximises the tag score. Returns the best settings and score; nothing is applied.",
     "input_schema": {"type": "object", "properties": {"complete": {"type": "boolean"}}, "additionalProperties": False}},
    {"name": "apply_tuning", "description": "Apply tuning live and store it in the active profile.",
     "input_schema": {"type": "object", "properties": {"settings": _SETTINGS_SCHEMA}, "required": ["settings"], "additionalProperties": False}},
]

FINETUNE_INSTRUCTIONS = """FINE-TUNING FROM A RECORDING
A motion recording of real play with the player's tags is attached. A tag marks the exact moment something should have
happened. Tag kinds: don / kat (drum hits), a gesture name (swing_left, swing_down, thrust, flick, shake...), an output action
(key.space, mouse.left, gamepad.a = "the mapping should have pressed this here"), nothing (= nothing should fire here) or
a free note. Work like an engineer:
1. Call recording_summary, then tag_window on a few tags (matched and missed) to see what the motion really looks like:
   accel/gyro for strokes and gestures, roll/pitch/yaw and camera position for wheel, aiming or lean bindings.
2. For hit or gesture tags, search with evaluate_tuning (and/or auto_fit_tuning) until most tags match with a small,
   consistent offset, then apply_tuning. For action tags, notes or orientation-based controls, fix the profile itself with
   the normal profile tools (bindings, thresholds, input ranges, modes, deadzones, tap_ms, gesture choice).
3. Tell the player in plain words what you changed, how well it scores now, and what to try if it is still off. Short.
"""


def build_finetune_content(session: MotionSession, explanation: str = "", complete: bool = False, max_images: int = 8) -> list[dict[str, Any]]:
    """User-message content: text summary + evaluation + thumbnails at the tags."""
    content: list[dict[str, Any]] = []
    text = "MOTION RECORDING ATTACHED\n" + session.summary()
    if explanation:
        text += f"\nPlayer's explanation: {explanation}"
    current = session.evaluate(None, complete)
    text += f"\nCurrent tuning score: {current.text()}"
    if current.per_tag:
        text += "\nPer tag: " + json.dumps(current.per_tag[:40], separators=(",", ":"))
    text += "\n" + FINETUNE_INSTRUCTIONS
    content.append({"type": "text", "text": text})
    shown = 0
    for i, tag in enumerate(session.tags):
        if shown >= max_images:
            break
        for kind in ("camera", "screen"):
            jpeg = session.thumbnail(kind, tag.t)
            if jpeg:
                content.append({"type": "text", "text": f"{kind} at tag {i} ({tag.label()}, t={tag.t:.2f}s):"})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(jpeg).decode("ascii")}})
                shown += 1
    return content


class FinetuneTools:
    """Tool handlers bound to one session and the running app."""

    def __init__(self, session: MotionSession, apply_settings: Callable[[dict], dict], get_settings: Callable[[], dict], complete: bool = False) -> None:
        self.session = session
        self.apply_settings = apply_settings
        self.get_settings = get_settings
        self.complete = complete
        self.last_result = None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return FINETUNE_TOOLS

    def handles(self, name: str) -> bool:
        return any(t["name"] == name for t in FINETUNE_TOOLS)

    def execute(self, name: str, args: dict[str, Any]) -> str:
        if name == "recording_summary":
            res = self.session.evaluate(self.get_settings(), self.complete)
            return (self.session.summary() + "\nCurrent tuning: " + json.dumps(self.get_settings()) + "\nScore: " + res.text()
                    + "\nTags with index: " + json.dumps([{"index": i, "tag": t.label(), "kind": t.kind, "family": t.family or "note", "t": t.t} for i, t in enumerate(self.session.tags)]))
        if name == "tag_window":
            idx = int(args["index"])
            if idx < 0 or idx >= len(self.session.tags):
                return f"no tag with index {idx} (there are {len(self.session.tags)})"
            return json.dumps(self.session.tag_window(idx, float(args.get("half_ms", 150.0))), separators=(",", ":"))
        if name == "get_tuning":
            return json.dumps(self.get_settings())
        if name == "evaluate_tuning":
            merged = dict(self.get_settings(), **(args.get("settings") or {}))
            res = self.session.evaluate(merged, bool(args.get("complete", self.complete)))
            self.last_result = res
            return json.dumps({"settings": res.config, "score": round(res.score, 2), "summary": res.text(), "per_tag": res.per_tag[:40]}, separators=(",", ":"))
        if name == "auto_fit_tuning":
            cfg, res = auto_fit(self.session, self.get_settings(), bool(args.get("complete", self.complete)))
            self.last_result = res
            return json.dumps({"best_settings": cfg, "score": round(res.score, 2), "summary": res.text()}, separators=(",", ":"))
        if name == "apply_tuning":
            applied = self.apply_settings(dict(self.get_settings(), **(args.get("settings") or {})))
            res = self.session.evaluate(applied, self.complete)
            return "Applied " + json.dumps(applied) + f". Recording now scores: {res.text()}"
        raise ValueError(f"unknown fine-tune tool {name}")
