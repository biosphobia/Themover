"""Hand a tagged motion recording to the coach and give it tools to fit the detector."""
from __future__ import annotations

import base64
import json
from typing import Any, Callable, Optional

from themover.ai.motion_capture import MotionSession, auto_fit
from themover.core.hits import hit_config_to_dict

FINETUNE_TOOLS: list[dict[str, Any]] = [
    {"name": "recording_summary", "description": "Summary of the attached motion recording: rates, tags, hits detected while recording, and how the CURRENT detector settings score against the tags.",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "tag_window", "description": "Raw accelerometer/gyro/trigger samples around one tag (index from recording_summary), plus hits detected near it.",
     "input_schema": {"type": "object", "properties": {"index": {"type": "integer"}, "half_ms": {"type": "number"}}, "required": ["index"], "additionalProperties": False}},
    {"name": "get_hit_settings", "description": "The drum-hit detector settings currently in use (onset_g, stop_g, kat_angle_deg, refractory_s, max_stroke_s, up_angle_deg, onset_frames).",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "evaluate_hit_settings", "description": "Replay the recording through the detector with candidate settings and score against the tags (matched / missed / wrong colour / extra, timing offset). Call repeatedly to search; nothing is applied.",
     "input_schema": {"type": "object", "properties": {"settings": {"type": "object", "properties": {
         "onset_g": {"type": "number"}, "stop_g": {"type": "number"}, "kat_angle_deg": {"type": "number"}, "refractory_s": {"type": "number"},
         "max_stroke_s": {"type": "number"}, "up_angle_deg": {"type": "number"}, "onset_frames": {"type": "integer"}}, "additionalProperties": False},
         "complete": {"type": "boolean", "description": "true if the player tagged every real hit (then extra detections count as false positives)"}},
         "required": ["settings"], "additionalProperties": False}},
    {"name": "auto_fit_hit_settings", "description": "Local coordinate search over the detector settings that maximises the tag score. Returns the best settings and their score; nothing is applied.",
     "input_schema": {"type": "object", "properties": {"complete": {"type": "boolean"}}, "additionalProperties": False}},
    {"name": "apply_hit_settings", "description": "Apply detector settings live and store them in the active profile.",
     "input_schema": {"type": "object", "properties": {"settings": {"type": "object", "properties": {
         "onset_g": {"type": "number"}, "stop_g": {"type": "number"}, "kat_angle_deg": {"type": "number"}, "refractory_s": {"type": "number"},
         "max_stroke_s": {"type": "number"}, "up_angle_deg": {"type": "number"}, "onset_frames": {"type": "integer"}}, "additionalProperties": False}},
         "required": ["settings"], "additionalProperties": False}},
]

FINETUNE_INSTRUCTIONS = """FINE-TUNING FROM A RECORDING
A motion recording with the player's tags is attached. Tags mark the exact moment a hit should have registered and which
colour it should be (don = straight down, kat = angled / rim). Work like an engineer:
1. Call recording_summary, then tag_window on a few tags (matched and missed) to see what the strokes really look like.
2. Search settings with evaluate_hit_settings (and/or auto_fit_hit_settings) until most tags match with a small, consistent offset.
   stop_g sets how sharp the stop must be, onset_g how much acceleration starts a stroke, kat_angle_deg separates don from kat,
   refractory_s limits repeats. If don/kat are confused, consider the trigger-modifier fallback in the profile too.
3. apply_hit_settings with the best result, adjust profile bindings if the recording shows they are wrong, and tell the player
   in plain words what you changed, how well it scores now, and what to try if it is still off. Keep it short.
"""


def build_finetune_content(session: MotionSession, explanation: str = "", complete: bool = False, max_images: int = 8) -> list[dict[str, Any]]:
    """User-message content: text summary + evaluation + thumbnails at the tags."""
    content: list[dict[str, Any]] = []
    text = "MOTION RECORDING ATTACHED\n" + session.summary()
    if explanation:
        text += f"\nPlayer's explanation: {explanation}"
    current = session.evaluate(None, complete)
    text += f"\nCurrent detector settings score: {current.text()}"
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
            return self.session.summary() + "\nCurrent settings: " + json.dumps(self.get_settings()) + "\nScore: " + res.text() + "\nTags with index: " + json.dumps([{"index": i, "tag": t.label(), "t": t.t} for i, t in enumerate(self.session.tags)])
        if name == "tag_window":
            idx = int(args["index"])
            if idx < 0 or idx >= len(self.session.tags):
                return f"no tag with index {idx} (there are {len(self.session.tags)})"
            return json.dumps(self.session.tag_window(idx, float(args.get("half_ms", 150.0))), separators=(",", ":"))
        if name == "get_hit_settings":
            return json.dumps(self.get_settings())
        if name == "evaluate_hit_settings":
            merged = dict(self.get_settings(), **(args.get("settings") or {}))
            res = self.session.evaluate(merged, bool(args.get("complete", self.complete)))
            self.last_result = res
            return json.dumps({"settings": res.config, "score": round(res.score, 2), "summary": res.text(), "per_tag": res.per_tag[:40]}, separators=(",", ":"))
        if name == "auto_fit_hit_settings":
            cfg, res = auto_fit(self.session, self.get_settings(), bool(args.get("complete", self.complete)))
            self.last_result = res
            return json.dumps({"best_settings": cfg, "score": round(res.score, 2), "summary": res.text()}, separators=(",", ":"))
        if name == "apply_hit_settings":
            applied = self.apply_settings(dict(self.get_settings(), **(args.get("settings") or {})))
            res = self.session.evaluate(applied, self.complete)
            return "Applied " + json.dumps(applied) + f". Recording now scores: {res.text()}"
        raise ValueError(f"unknown fine-tune tool {name}")
