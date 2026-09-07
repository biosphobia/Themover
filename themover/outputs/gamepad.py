"""Virtual Xbox 360 controller via ViGEmBus (``vgamepad``).

If the driver or module is missing a :class:`NullGamepad` is used, so the rest
of The Mover keeps working (keyboard and mouse targets are unaffected).
"""
from __future__ import annotations

import logging

from themover.outputs.sink import OutputSink

log = logging.getLogger(__name__)


class NullGamepad(OutputSink):
    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        self.buttons: dict[str, bool] = {}
        self.axes: dict[str, float] = {}

    def gamepad_button(self, name: str, pressed: bool) -> None:
        self.buttons[name] = pressed

    def gamepad_axis(self, name: str, value: float) -> None:
        self.axes[name] = value

    def release_all(self) -> None:
        self.buttons.clear()
        self.axes.clear()

    @property
    def description(self) -> str:
        return f"no virtual gamepad ({self.reason})" if self.reason else "no virtual gamepad"


class ViGEmGamepad(OutputSink):  # pragma: no cover - needs the Windows driver
    def __init__(self) -> None:
        import vgamepad as vg  # type: ignore

        self.vg = vg
        self.pad = vg.VX360Gamepad()
        B = vg.XUSB_BUTTON
        self._buttons = {
            "a": B.XUSB_GAMEPAD_A, "b": B.XUSB_GAMEPAD_B, "x": B.XUSB_GAMEPAD_X, "y": B.XUSB_GAMEPAD_Y,
            "lb": B.XUSB_GAMEPAD_LEFT_SHOULDER, "rb": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "back": B.XUSB_GAMEPAD_BACK, "start": B.XUSB_GAMEPAD_START, "guide": B.XUSB_GAMEPAD_GUIDE,
            "ls": B.XUSB_GAMEPAD_LEFT_THUMB, "rs": B.XUSB_GAMEPAD_RIGHT_THUMB,
            "dpad_up": B.XUSB_GAMEPAD_DPAD_UP, "dpad_down": B.XUSB_GAMEPAD_DPAD_DOWN,
            "dpad_left": B.XUSB_GAMEPAD_DPAD_LEFT, "dpad_right": B.XUSB_GAMEPAD_DPAD_RIGHT,
        }
        self.axes = {"left_stick_x": 0.0, "left_stick_y": 0.0, "right_stick_x": 0.0, "right_stick_y": 0.0, "left_trigger": 0.0, "right_trigger": 0.0}
        self._dirty = True

    def gamepad_button(self, name: str, pressed: bool) -> None:
        btn = self._buttons.get(name)
        if btn is None:
            return
        if pressed:
            self.pad.press_button(btn)
        else:
            self.pad.release_button(btn)
        self._dirty = True

    def gamepad_axis(self, name: str, value: float) -> None:
        if name in self.axes:
            self.axes[name] = max(-1.0, min(1.0, value))
            self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        a = self.axes
        self.pad.left_joystick_float(a["left_stick_x"], a["left_stick_y"])
        self.pad.right_joystick_float(a["right_stick_x"], a["right_stick_y"])
        self.pad.left_trigger_float(max(0.0, a["left_trigger"]))
        self.pad.right_trigger_float(max(0.0, a["right_trigger"]))
        self.pad.update()
        self._dirty = False

    def release_all(self) -> None:
        self.pad.reset()
        for k in self.axes:
            self.axes[k] = 0.0
        self._dirty = True
        self.flush()

    def close(self) -> None:
        try:
            self.release_all()
        except Exception:
            pass

    @property
    def description(self) -> str:
        return "virtual Xbox 360 pad (ViGEmBus)"


def create_gamepad_sink(enabled: bool = True) -> OutputSink:
    if not enabled:
        return NullGamepad("disabled in settings")
    try:
        return ViGEmGamepad()
    except Exception as exc:
        log.warning("virtual gamepad unavailable: %s", exc)
        return NullGamepad("install ViGEmBus + `pip install vgamepad`")
