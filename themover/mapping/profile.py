"""Mapping profile data model, JSON schema and validation."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from themover.mapping import vocabulary as V


@dataclass
class Binding:
    source: str
    target: str
    mode: str = "auto"
    threshold: float = 0.5
    compare: str = ">"
    input_range: list[float] = field(default_factory=lambda: [-1.0, 1.0])
    deadzone: float = 0.0
    scale: float = 1.0
    invert: bool = False
    curve: str = "linear"
    smoothing: float = 0.0
    tap_ms: int = 60
    repeat_ms: int = 150
    comment: str = ""
    enabled: bool = True

    def effective_mode(self) -> str:
        if self.mode != "auto":
            return self.mode
        kind = V.target_kind(self.target)
        if kind == "button":
            return "hold"
        if kind == "axis":
            if self.target in ("mouse.move_x", "mouse.move_y", "mouse.wheel"):
                return "mouse"
            if self.target in ("mouse.abs_x", "mouse.abs_y"):
                return "absolute"
            return "axis"
        return "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Binding":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        b = cls(**known)
        b.input_range = [float(x) for x in (b.input_range or [-1.0, 1.0])][:2]
        if len(b.input_range) != 2:
            b.input_range = [-1.0, 1.0]
        return b

    def describe(self) -> str:
        opts = []
        if self.mode != "auto":
            opts.append(self.mode)
        if self.effective_mode() in ("hold", "tap", "toggle", "repeat") and self.threshold != 0.5:
            opts.append(f"{self.compare}{self.threshold:g}")
        if self.effective_mode() in ("axis", "mouse", "absolute"):
            if self.input_range != [-1.0, 1.0]:
                opts.append(f"range {self.input_range[0]:g}..{self.input_range[1]:g}")
            if self.scale != 1.0:
                opts.append(f"x{self.scale:g}")
            if self.deadzone:
                opts.append(f"dz {self.deadzone:g}")
        if self.invert:
            opts.append("inverted")
        return f"{self.source} -> {self.target}" + (f"  ({', '.join(opts)})" if opts else "")


@dataclass
class FeedbackRule:
    """Rumble / LED reactions to a signal."""

    when: str = ""  # pulse when this source crosses threshold (or continuous if rumble_from)
    controller: int = 0
    rumble: float = 0.0
    duration_ms: int = 150
    led: list[int] | None = None
    led_duration_ms: int = 150
    threshold: float = 0.5
    rumble_from: str = ""  # continuous: rumble strength follows this source (0..1)
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeedbackRule":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class ControllerConfig:
    color: list[int] = field(default_factory=lambda: [255, 0, 255])
    role: str = "right hand"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Profile:
    name: str = "Untitled"
    game: str = ""
    description: str = ""
    play_style: str = ""  # one-paragraph explanation for the player
    controllers: list[ControllerConfig] = field(
        default_factory=lambda: [ControllerConfig([255, 0, 255], "right hand"), ControllerConfig([0, 255, 255], "left hand")]
    )
    bindings: list[Binding] = field(default_factory=list)
    feedback: list[FeedbackRule] = field(default_factory=list)
    gesture_sensitivity: float = 1.0  # multiplies gesture thresholds (lower = easier)
    gesture_cooldown_ms: int = 220  # minimum time between two of the same gesture
    notes: str = ""
    based_on: str = ""  # template key this profile started from ("" = coach / custom)

    # ------------------------------------------------------------- serialise
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "game": self.game,
            "description": self.description,
            "play_style": self.play_style,
            "controllers": [c.to_dict() for c in self.controllers],
            "bindings": [b.to_dict() for b in self.bindings],
            "feedback": [f.to_dict() for f in self.feedback],
            "gesture_sensitivity": self.gesture_sensitivity,
            "gesture_cooldown_ms": self.gesture_cooldown_ms,
            "notes": self.notes,
            "based_on": self.based_on,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        p = cls(
            name=str(data.get("name", "Untitled")),
            game=str(data.get("game", "")),
            description=str(data.get("description", "")),
            play_style=str(data.get("play_style", "")),
            gesture_sensitivity=float(data.get("gesture_sensitivity", 1.0) or 1.0),
            gesture_cooldown_ms=int(data.get("gesture_cooldown_ms", 220) or 220),
            notes=str(data.get("notes", "")),
            based_on=str(data.get("based_on", "") or ""),
        )
        ctrls = data.get("controllers") or []
        if ctrls:
            p.controllers = []
            for i, c in enumerate(ctrls[:2]):
                color = [int(x) for x in (c.get("color") or [255, 0, 255])][:3]
                while len(color) < 3:
                    color.append(0)
                p.controllers.append(ControllerConfig(color, str(c.get("role", "right hand" if i == 0 else "left hand"))))
            while len(p.controllers) < 2:
                p.controllers.append(ControllerConfig([0, 255, 255], "left hand"))
        p.bindings = [Binding.from_dict(b) for b in data.get("bindings") or [] if isinstance(b, dict)]
        p.feedback = [FeedbackRule.from_dict(f) for f in data.get("feedback") or [] if isinstance(f, dict)]
        return p

    @classmethod
    def from_json(cls, text: str) -> "Profile":
        return cls.from_dict(json.loads(text))

    def copy(self) -> "Profile":
        return copy.deepcopy(self)

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> "Profile":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    # ------------------------------------------------------------ validation
    def validate(self) -> list[str]:
        """Return a list of problems (empty when the profile is usable)."""
        problems: list[str] = []
        for i, b in enumerate(self.bindings):
            if not V.is_valid_source(b.source):
                problems.append(f"binding {i}: unknown source '{b.source}'")
            if not V.is_valid_target(b.target):
                problems.append(f"binding {i}: unknown target '{b.target}'")
            if b.mode not in V.MODES:
                problems.append(f"binding {i}: unknown mode '{b.mode}'")
            if b.curve not in V.CURVES:
                problems.append(f"binding {i}: unknown curve '{b.curve}'")
            if b.compare not in V.COMPARES:
                problems.append(f"binding {i}: unknown compare '{b.compare}'")
            if b.input_range[0] == b.input_range[1]:
                problems.append(f"binding {i}: input_range must span two different values")
        for i, f in enumerate(self.feedback):
            if f.when and not V.is_valid_source(f.when):
                problems.append(f"feedback {i}: unknown source '{f.when}'")
            if f.rumble_from and not V.is_valid_source(f.rumble_from):
                problems.append(f"feedback {i}: unknown source '{f.rumble_from}'")
            if f.controller not in (0, 1):
                problems.append(f"feedback {i}: controller must be 0 or 1")
        return problems

    def sanitized(self) -> "Profile":
        """Drop invalid bindings/feedback so the rest still works."""
        p = self.copy()
        p.bindings = [
            b for b in p.bindings
            if V.is_valid_source(b.source) and V.is_valid_target(b.target)
            and b.mode in V.MODES and b.curve in V.CURVES and b.compare in V.COMPARES
            and b.input_range[0] != b.input_range[1]
        ]
        p.feedback = [
            f for f in p.feedback
            if (not f.when or V.is_valid_source(f.when))
            and (not f.rumble_from or V.is_valid_source(f.rumble_from))
            and f.controller in (0, 1)
        ]
        return p

    def summary(self) -> str:
        lines = [f"{self.name}" + (f" ({self.game})" if self.game else "")]
        if self.play_style:
            lines.append(self.play_style)
        for b in self.bindings:
            if b.enabled:
                lines.append("  " + b.describe() + (f"  # {b.comment}" if b.comment else ""))
        for f in self.feedback:
            what = f"rumble {f.rumble:g}" if f.rumble else ""
            if f.led:
                what += (", " if what else "") + f"led {f.led}"
            if f.rumble_from:
                lines.append(f"  feedback P{f.controller + 1}: rumble follows {f.rumble_from}")
            elif f.when:
                lines.append(f"  feedback P{f.controller + 1}: {what} when {f.when}")
        return "\n".join(lines)


# ------------------------------------------------------------------ schema
def profile_json_schema() -> dict[str, Any]:
    """JSON schema used for Claude's structured output."""
    binding = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "signal name from the vocabulary, e.g. c0.gesture.swing_left"},
            "target": {"type": "string", "description": "action name, e.g. key.space or gamepad.left_stick_x"},
            "mode": {"type": "string", "enum": list(V.MODES)},
            "threshold": {"type": "number"},
            "compare": {"type": "string", "enum": list(V.COMPARES)},
            "input_range": {"type": "array", "items": {"type": "number"}},
            "deadzone": {"type": "number"},
            "scale": {"type": "number"},
            "invert": {"type": "boolean"},
            "curve": {"type": "string", "enum": list(V.CURVES)},
            "smoothing": {"type": "number"},
            "tap_ms": {"type": "integer"},
            "repeat_ms": {"type": "integer"},
            "comment": {"type": "string"},
        },
        "required": ["source", "target", "mode", "threshold", "compare", "input_range", "deadzone", "scale", "invert", "curve", "smoothing", "tap_ms", "repeat_ms", "comment"],
        "additionalProperties": False,
    }
    feedback = {
        "type": "object",
        "properties": {
            "when": {"type": "string"},
            "controller": {"type": "integer"},
            "rumble": {"type": "number"},
            "duration_ms": {"type": "integer"},
            "led": {"type": "array", "items": {"type": "integer"}},
            "led_duration_ms": {"type": "integer"},
            "threshold": {"type": "number"},
            "rumble_from": {"type": "string"},
            "comment": {"type": "string"},
        },
        "required": ["when", "controller", "rumble", "duration_ms", "led", "led_duration_ms", "threshold", "rumble_from", "comment"],
        "additionalProperties": False,
    }
    controller = {
        "type": "object",
        "properties": {
            "color": {"type": "array", "items": {"type": "integer"}},
            "role": {"type": "string"},
        },
        "required": ["color", "role"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "game": {"type": "string"},
            "description": {"type": "string"},
            "play_style": {"type": "string", "description": "2-4 sentences telling the player how to move to play"},
            "controllers": {"type": "array", "items": controller},
            "bindings": {"type": "array", "items": binding},
            "feedback": {"type": "array", "items": feedback},
            "gesture_sensitivity": {"type": "number", "description": "1.0 normal, 0.7 easier gestures, 1.5 needs harder swings"},
            "gesture_cooldown_ms": {"type": "integer", "description": "min ms between repeats of a gesture; 220 default, 80-100 for drumming/rhythm"},
            "notes": {"type": "string"},
        },
        "required": ["name", "game", "description", "play_style", "controllers", "bindings", "feedback", "gesture_sensitivity", "gesture_cooldown_ms", "notes"],
        "additionalProperties": False,
    }
