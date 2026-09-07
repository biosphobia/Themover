"""Names of every signal a controller can produce and every action a game can receive.

This vocabulary is shared by the mapping editor, the engine and the prompt sent
to Claude, so all three always agree on what exists.
"""
from __future__ import annotations

from themover.core.gestures import GESTURE_NAMES
from themover.core.state import BUTTON_NAMES

NUM_CONTROLLERS = 2

# ----------------------------------------------------------------- sources
SOURCE_GROUPS: dict[str, tuple[str, str, list[str]]] = {
    # group: (description, value range, members)
    "button": ("Physical buttons (1 while held)", "0..1", list(BUTTON_NAMES)),
    "trigger": ("Analog trigger", "0..1", []),
    "gesture": ("Motion gestures, pulse to 1 for ~120ms when detected", "0..1", list(GESTURE_NAMES)),
    "orient": ("Orientation in degrees (roll about handle, pitch above horizon, relative yaw)", "-180..180", ["roll", "pitch", "yaw"]),
    "accel": ("Acceleration in g including gravity", "-4..4", ["x", "y", "z"]),
    "gyro": ("Angular speed in degrees/second", "-2000..2000", ["x", "y", "z"]),
    "track": ("Camera tracking of the glowing sphere: x/y -1..1, depth 0 (far)..1 (near), vx/vy velocity, tracked 0/1", "-1..1", ["x", "y", "depth", "vx", "vy", "tracked"]),
    "motion": ("Continuous motion energy: strength (g without gravity), angular_speed (deg/s)", "0..", ["strength", "angular_speed"]),
}

GLOBAL_SOURCES: dict[str, str] = {
    "wheel.angle": "Steering-wheel angle in degrees: tilt of the line between the two spheres (camera), or roll of controller 1 if only one is tracked. 0 = level, positive = clockwise.",
    "both.distance": "Distance between the two spheres in the camera image (0..2)",
    "both.center_x": "Horizontal centre of both spheres (-1..1)",
    "both.center_y": "Vertical centre of both spheres (-1..1)",
    "both.depth_avg": "Average depth of both spheres (0..1)",
    "both.height_diff": "Sphere 1 height minus sphere 2 height (-2..2), e.g. for flapping or balancing",
}


def all_sources() -> list[str]:
    names: list[str] = []
    for c in range(NUM_CONTROLLERS):
        for group, (_d, _r, members) in SOURCE_GROUPS.items():
            if not members:
                names.append(f"c{c}.{group}")
            else:
                names.extend(f"c{c}.{group}.{m}" for m in members)
    names.extend(GLOBAL_SOURCES.keys())
    return names


# ----------------------------------------------------------------- targets
KEY_NAMES = (
    [chr(c) for c in range(ord("a"), ord("z") + 1)]
    + [str(d) for d in range(10)]
    + [f"f{i}" for i in range(1, 13)]
    + [
        "space", "enter", "esc", "tab", "backspace", "delete", "insert", "home", "end",
        "pageup", "pagedown", "up", "down", "left", "right", "shift", "ctrl", "alt",
        "lshift", "rshift", "lctrl", "rctrl", "lalt", "ralt", "capslock", "minus",
        "equals", "lbracket", "rbracket", "semicolon", "apostrophe", "grave",
        "backslash", "comma", "period", "slash", "numpad0", "numpad1", "numpad2",
        "numpad3", "numpad4", "numpad5", "numpad6", "numpad7", "numpad8", "numpad9",
        "numpad_plus", "numpad_minus", "numpad_multiply", "numpad_divide", "numpad_enter",
    ]
)
MOUSE_BUTTONS = ("left", "right", "middle", "x1", "x2")
MOUSE_AXES = ("move_x", "move_y", "wheel", "abs_x", "abs_y")
GAMEPAD_BUTTONS = (
    "a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs", "guide",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
)
GAMEPAD_AXES = ("left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y", "left_trigger", "right_trigger")


def all_targets() -> list[str]:
    names = ["none"]
    names += [f"key.{k}" for k in KEY_NAMES]
    names += [f"mouse.{b}" for b in MOUSE_BUTTONS]
    names += [f"mouse.{a}" for a in MOUSE_AXES]
    names += [f"gamepad.{b}" for b in GAMEPAD_BUTTONS]
    names += [f"gamepad.{a}" for a in GAMEPAD_AXES]
    return names


def target_kind(target: str) -> str:
    """'button' (press/release), 'axis' (continuous) or 'none'."""
    if not target or target == "none":
        return "none"
    family, _, name = target.partition(".")
    if family == "key":
        return "button"
    if family == "mouse":
        return "axis" if name in MOUSE_AXES else "button"
    if family == "gamepad":
        return "axis" if name in GAMEPAD_AXES else "button"
    return "none"


def is_valid_source(source: str) -> bool:
    if source in GLOBAL_SOURCES:
        return True
    parts = source.split(".")
    if len(parts) < 2 or not parts[0].startswith("c"):
        return False
    try:
        idx = int(parts[0][1:])
    except ValueError:
        return False
    if idx < 0 or idx >= NUM_CONTROLLERS:
        return False
    group = parts[1]
    if group not in SOURCE_GROUPS:
        return False
    members = SOURCE_GROUPS[group][2]
    if not members:
        return len(parts) == 2
    return len(parts) == 3 and parts[2] in members


def is_valid_target(target: str) -> bool:
    return target in set(all_targets())


MODES = ("auto", "hold", "tap", "toggle", "axis", "mouse", "absolute", "repeat")
CURVES = ("linear", "squared", "cubic", "step")
COMPARES = (">", "<", "abs>")


def vocabulary_text() -> str:
    """Human/Claude readable description of the vocabulary."""
    lines = ["SOURCES (replace cN with c0 = controller in the right hand, c1 = left hand):"]
    for group, (desc, rng, members) in SOURCE_GROUPS.items():
        if members:
            lines.append(f"  cN.{group}.<{'|'.join(members)}>  - {desc} [{rng}]")
        else:
            lines.append(f"  cN.{group}  - {desc} [{rng}]")
    for name, desc in GLOBAL_SOURCES.items():
        lines.append(f"  {name}  - {desc}")
    lines.append("")
    lines.append("TARGETS:")
    lines.append("  key.<name>  - keyboard keys: " + ", ".join(KEY_NAMES))
    lines.append("  mouse.<left|right|middle|x1|x2>  - mouse buttons")
    lines.append("  mouse.move_x / mouse.move_y  - relative mouse motion (mode 'mouse'; scale is a sensitivity multiplier, 1.0 = 600 px/s at full deflection)")
    lines.append("  mouse.abs_x / mouse.abs_y  - absolute cursor position from -1..1 (mode 'absolute')")
    lines.append("  mouse.wheel  - scroll (axis)")
    lines.append("  gamepad.<" + "|".join(GAMEPAD_BUTTONS) + ">  - virtual Xbox 360 buttons")
    lines.append("  gamepad.<" + "|".join(GAMEPAD_AXES) + ">  - virtual Xbox 360 axes (-1..1, triggers 0..1)")
    lines.append("")
    lines.append("MODES: hold (press while source above threshold), tap (short press on rising edge), toggle, repeat (auto-repeat taps while held),")
    lines.append("       axis (continuous to gamepad axis), mouse (source drives relative mouse speed), absolute (source drives cursor position). 'auto' picks hold for button targets and axis/mouse for axis targets.")
    return "\n".join(lines)
