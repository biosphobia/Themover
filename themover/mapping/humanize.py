"""Plain-English descriptions of sources, targets and bindings for the simple UI."""
from __future__ import annotations

from themover.mapping.profile import Binding, FeedbackRule

HAND = {"c0": "right hand", "c1": "left hand"}
BUTTON = {
    "square": "□ Square", "triangle": "△ Triangle", "cross": "✕ Cross", "circle": "○ Circle",
    "move": "Move button", "start": "Start", "select": "Select", "ps": "PS button", "trigger_click": "trigger click",
}
GESTURE = {
    "swing_left": "Swing {hand} to the left", "swing_right": "Swing {hand} to the right",
    "swing_up": "Flick {hand} up", "swing_down": "Strike down with {hand}",
    "thrust": "Punch / thrust {hand} forward", "pull": "Pull {hand} back",
    "flick": "Wrist-flick {hand}", "shake": "Shake {hand}", "swing_any": "Any swing of {hand}",
}
ORIENT = {"roll": "Tilt {hand} sideways", "pitch": "Tilt {hand} forward / back", "yaw": "Turn {hand} left / right"}
TRACK = {
    "x": "Move {hand} left / right (camera)", "y": "Raise / lower {hand} (camera)",
    "depth": "Move {hand} closer / further (camera)", "vx": "{Hand} sideways speed (camera)",
    "vy": "{Hand} vertical speed (camera)", "tracked": "{Hand} visible to the camera",
}
GLOBAL = {
    "wheel.angle": "Turn the wheel (both hands)", "both.distance": "Distance between hands (camera)",
    "both.center_x": "Both hands left / right (camera)", "both.center_y": "Both hands up / down (camera)",
    "both.depth_avg": "Both hands closer / further (camera)", "both.height_diff": "Right hand higher than left (camera)",
}
KEY_LABEL = {
    "space": "Space", "enter": "Enter", "esc": "Esc", "tab": "Tab", "backspace": "Backspace", "delete": "Delete",
    "insert": "Insert", "home": "Home", "end": "End", "pageup": "Page Up", "pagedown": "Page Down",
    "up": "↑ arrow", "down": "↓ arrow", "left": "← arrow", "right": "→ arrow", "shift": "Shift", "lshift": "Left Shift",
    "rshift": "Right Shift", "ctrl": "Ctrl", "lctrl": "Left Ctrl", "rctrl": "Right Ctrl", "alt": "Alt", "lalt": "Left Alt",
    "ralt": "Right Alt", "capslock": "Caps Lock", "minus": "-", "equals": "=", "lbracket": "[", "rbracket": "]",
    "semicolon": ";", "apostrophe": "'", "grave": "`", "backslash": "\\\\", "comma": ",", "period": ".", "slash": "/",
    "numpad_plus": "Numpad +", "numpad_minus": "Numpad -", "numpad_multiply": "Numpad *", "numpad_divide": "Numpad /",
    "numpad_enter": "Numpad Enter",
}
MOUSE = {
    "left": "Left click", "right": "Right click", "middle": "Middle click", "x1": "Mouse button 4", "x2": "Mouse button 5",
    "move_x": "Mouse look left / right", "move_y": "Mouse look up / down", "wheel": "Scroll wheel",
    "abs_x": "Cursor position left / right", "abs_y": "Cursor position up / down",
}
GAMEPAD = {
    "a": "A", "b": "B", "x": "X", "y": "Y", "lb": "LB", "rb": "RB", "back": "Back", "start": "Start", "ls": "L3", "rs": "R3",
    "guide": "Guide", "dpad_up": "D-pad ↑", "dpad_down": "D-pad ↓", "dpad_left": "D-pad ←", "dpad_right": "D-pad →",
    "left_stick_x": "Left stick ← →", "left_stick_y": "Left stick ↑ ↓", "right_stick_x": "Right stick ← →",
    "right_stick_y": "Right stick ↑ ↓", "left_trigger": "Left trigger (LT)", "right_trigger": "Right trigger (RT)",
}
MODE = {
    "hold": "hold", "tap": "tap", "toggle": "toggle on / off", "repeat": "auto-repeat", "axis": "analog",
    "mouse": "mouse motion", "absolute": "cursor", "none": "",
}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def describe_source(source: str) -> str:
    if source in GLOBAL:
        return GLOBAL[source]
    parts = source.split(".")
    hand = HAND.get(parts[0], parts[0])
    group = parts[1] if len(parts) > 1 else ""
    member = parts[2] if len(parts) > 2 else ""
    fmt = lambda t: t.replace("{hand}", hand).replace("{Hand}", _cap(hand))  # noqa: E731
    if group == "button":
        return f"{BUTTON.get(member, member)} ({hand})"
    if group == "trigger":
        return f"Trigger ({hand})"
    if group == "gesture":
        return fmt(GESTURE.get(member, member))
    if group == "orient":
        return fmt(ORIENT.get(member, member))
    if group == "track":
        return fmt(TRACK.get(member, member))
    if group == "accel":
        return f"{_cap(hand)} acceleration {member.upper()}"
    if group == "gyro":
        return f"{_cap(hand)} rotation speed {member.upper()}"
    if group == "motion":
        return f"How hard {hand} moves" if member == "strength" else f"How fast {hand} turns"
    return source


def describe_target(target: str) -> str:
    family, _, name = target.partition(".")
    if family == "key":
        return "Key " + KEY_LABEL.get(name, name.upper() if len(name) == 1 else name.upper() if name.startswith("f") else name)
    if family == "mouse":
        return MOUSE.get(name, name)
    if family == "gamepad":
        return "Gamepad " + GAMEPAD.get(name, name)
    return "nothing"


def describe_binding(b: Binding) -> tuple[str, str, str]:
    """(what you do, what the game receives, how)."""
    action = describe_source(b.source)
    mode = b.effective_mode()
    how = MODE.get(mode, mode)
    unit = "°" if b.source.split(".")[1:2] == ["orient"] or b.source == "wheel.angle" else ""
    if mode in ("hold", "tap", "toggle", "repeat") and b.source.split(".")[1:2] not in (["button"], ["gesture"]):
        if b.compare == "<":
            action += f" (past {b.threshold:g}{unit} the other way)"
        elif b.compare == "abs>":
            action += f" (more than {b.threshold:g}{unit} either way)"
        elif b.source.endswith(".trigger") or b.source.endswith(".tracked"):
            pass
        else:
            action += f" (more than {b.threshold:g}{unit})"
    if b.invert:
        how += ", inverted"
    if not b.enabled:
        how = "off"
    return action, describe_target(b.target), how


def describe_feedback(f: FeedbackRule) -> str:
    who = HAND.get(f"c{f.controller}", f"controller {f.controller + 1}")
    if f.rumble_from:
        return f"{_cap(who)} rumbles along with {describe_source(f.rumble_from).lower()}"
    effects = []
    if f.rumble:
        effects.append(f"rumble {int(f.rumble * 100)}% for {f.duration_ms} ms")
    if f.led:
        effects.append("flash the sphere")
    return f"{_cap(who)}: {' and '.join(effects) or 'nothing'} when {describe_source(f.when).lower()}"
