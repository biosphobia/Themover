"""Keyboard and mouse injection.

On Windows we call ``SendInput`` directly with hardware scan-codes, which is what
games using DirectInput/RawInput expect (plain virtual-key events are often
ignored by them).  Elsewhere ``pynput`` is used.
"""
from __future__ import annotations

import logging
import sys

from themover.outputs.sink import OutputSink

log = logging.getLogger(__name__)

# Virtual-key codes for the names in vocabulary.KEY_NAMES (Windows).
_VK: dict[str, int] = {
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": 0xA0, "lshift": 0xA0, "rshift": 0xA1, "ctrl": 0xA2, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0xA4, "lalt": 0xA4, "ralt": 0xA5, "capslock": 0x14,
    "minus": 0xBD, "equals": 0xBB, "lbracket": 0xDB, "rbracket": 0xDD, "semicolon": 0xBA,
    "apostrophe": 0xDE, "grave": 0xC0, "backslash": 0xDC, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "numpad_plus": 0x6B, "numpad_minus": 0x6D, "numpad_multiply": 0x6A, "numpad_divide": 0x6F, "numpad_enter": 0x0D,
}
for _i in range(10):
    _VK[str(_i)] = 0x30 + _i
    _VK[f"numpad{_i}"] = 0x60 + _i
for _i in range(26):
    _VK[chr(ord("a") + _i)] = 0x41 + _i
for _i in range(1, 13):
    _VK[f"f{_i}"] = 0x70 + _i - 1

_EXTENDED = {"insert", "delete", "home", "end", "pageup", "pagedown", "up", "down", "left", "right", "rctrl", "ralt", "numpad_divide", "numpad_enter"}


def vk_code(key: str) -> int | None:
    return _VK.get(key.lower())


class WindowsSendInput(OutputSink):  # pragma: no cover - Windows only
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.user32 = ctypes.windll.user32
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.INPUT = INPUT
        self.MOUSEINPUT = MOUSEINPUT
        self.KEYBDINPUT = KEYBDINPUT
        self._down: set[str] = set()
        self._mouse_down: set[str] = set()
        self.screen_w = self.user32.GetSystemMetrics(0)
        self.screen_h = self.user32.GetSystemMetrics(1)

    def _send(self, inp) -> None:
        self.user32.SendInput(1, self.ctypes.byref(inp), self.ctypes.sizeof(inp))

    def _key(self, key: str, up: bool) -> None:
        vk = vk_code(key)
        if vk is None:
            return
        scan = self.user32.MapVirtualKeyW(vk, 0)
        flags = 0x0008  # KEYEVENTF_SCANCODE
        if key.lower() in _EXTENDED:
            flags |= 0x0001  # KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= 0x0002  # KEYEVENTF_KEYUP
        inp = self.INPUT(type=1)
        inp.ki = self.KEYBDINPUT(0, scan, flags, 0, 0)
        self._send(inp)

    def key_down(self, key: str) -> None:
        self._key(key, False)
        self._down.add(key)

    def key_up(self, key: str) -> None:
        self._key(key, True)
        self._down.discard(key)

    _MOUSE_FLAGS = {
        "left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040),
        "x1": (0x0080, 0x0100), "x2": (0x0080, 0x0100),
    }

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        inp = self.INPUT(type=0)
        inp.mi = self.MOUSEINPUT(dx, dy, data, flags, 0, 0)
        self._send(inp)

    def mouse_down(self, button: str) -> None:
        down, _ = self._MOUSE_FLAGS.get(button, (0, 0))
        if down:
            self._mouse(down, data=1 if button == "x1" else 2 if button == "x2" else 0)
            self._mouse_down.add(button)

    def mouse_up(self, button: str) -> None:
        _, up = self._MOUSE_FLAGS.get(button, (0, 0))
        if up:
            self._mouse(up, data=1 if button == "x1" else 2 if button == "x2" else 0)
            self._mouse_down.discard(button)

    def mouse_move(self, dx: int, dy: int) -> None:
        if dx or dy:
            self._mouse(0x0001, int(dx), int(dy))  # MOUSEEVENTF_MOVE

    def mouse_move_abs(self, x: float, y: float) -> None:
        ax = int(max(0.0, min(1.0, x)) * 65535)
        ay = int(max(0.0, min(1.0, y)) * 65535)
        self._mouse(0x0001 | 0x8000, ax, ay)  # MOVE | ABSOLUTE

    def mouse_scroll(self, ticks: int) -> None:
        if ticks:
            self._mouse(0x0800, data=int(ticks) * 120)  # MOUSEEVENTF_WHEEL

    def release_all(self) -> None:
        for k in list(self._down):
            self.key_up(k)
        for b in list(self._mouse_down):
            self.mouse_up(b)

    @property
    def description(self) -> str:
        return "Windows SendInput (scan-codes)"


class PynputSink(OutputSink):
    """Cross-platform fallback built on pynput."""

    def __init__(self) -> None:
        from pynput import keyboard, mouse  # type: ignore

        self._kb = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._Key = keyboard.Key
        self._Button = mouse.Button
        self._down: set[str] = set()
        self._mouse_down: set[str] = set()
        self.screen_w, self.screen_h = _screen_size()

    def _resolve(self, key: str):
        k = key.lower()
        special = {
            "space": self._Key.space, "enter": self._Key.enter, "esc": self._Key.esc, "tab": self._Key.tab,
            "backspace": self._Key.backspace, "delete": self._Key.delete, "insert": self._Key.insert,
            "home": self._Key.home, "end": self._Key.end, "pageup": self._Key.page_up, "pagedown": self._Key.page_down,
            "up": self._Key.up, "down": self._Key.down, "left": self._Key.left, "right": self._Key.right,
            "shift": self._Key.shift, "lshift": self._Key.shift_l, "rshift": self._Key.shift_r,
            "ctrl": self._Key.ctrl, "lctrl": self._Key.ctrl_l, "rctrl": self._Key.ctrl_r,
            "alt": self._Key.alt, "lalt": self._Key.alt_l, "ralt": self._Key.alt_r, "capslock": self._Key.caps_lock,
            "minus": "-", "equals": "=", "lbracket": "[", "rbracket": "]", "semicolon": ";", "apostrophe": "'",
            "grave": "`", "backslash": "\\", "comma": ",", "period": ".", "slash": "/",
            "numpad_plus": "+", "numpad_minus": "-", "numpad_multiply": "*", "numpad_divide": "/", "numpad_enter": self._Key.enter,
        }
        if k in special:
            return special[k]
        if k.startswith("f") and k[1:].isdigit():
            return getattr(self._Key, k)
        if k.startswith("numpad") and k[6:].isdigit():
            return k[6:]
        return k

    def key_down(self, key: str) -> None:
        try:
            self._kb.press(self._resolve(key))
            self._down.add(key)
        except Exception as exc:
            log.debug("key_down %s failed: %s", key, exc)

    def key_up(self, key: str) -> None:
        try:
            self._kb.release(self._resolve(key))
        except Exception as exc:
            log.debug("key_up %s failed: %s", key, exc)
        self._down.discard(key)

    def _button(self, name: str):
        return {"left": self._Button.left, "right": self._Button.right, "middle": self._Button.middle}.get(
            name, getattr(self._Button, name, self._Button.left)
        )

    def mouse_down(self, button: str) -> None:
        self._mouse.press(self._button(button))
        self._mouse_down.add(button)

    def mouse_up(self, button: str) -> None:
        self._mouse.release(self._button(button))
        self._mouse_down.discard(button)

    def mouse_move(self, dx: int, dy: int) -> None:
        if dx or dy:
            self._mouse.move(int(dx), int(dy))

    def mouse_move_abs(self, x: float, y: float) -> None:
        self._mouse.position = (int(x * self.screen_w), int(y * self.screen_h))

    def mouse_scroll(self, ticks: int) -> None:
        if ticks:
            self._mouse.scroll(0, int(ticks))

    def release_all(self) -> None:
        for k in list(self._down):
            self.key_up(k)
        for b in list(self._mouse_down):
            self.mouse_up(b)

    @property
    def description(self) -> str:
        return "pynput keyboard/mouse"


def _screen_size() -> tuple[int, int]:
    try:
        if sys.platform.startswith("win"):
            import ctypes

            return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    except Exception:
        pass
    return 1920, 1080


def create_keyboard_mouse_sink(backend: str = "auto") -> OutputSink:
    if backend in ("auto", "sendinput") and sys.platform.startswith("win"):
        try:
            return WindowsSendInput()
        except Exception as exc:  # pragma: no cover
            log.warning("SendInput backend unavailable: %s", exc)
            if backend == "sendinput":
                raise
    try:
        return PynputSink()
    except Exception as exc:
        log.warning("pynput backend unavailable (%s); keyboard/mouse output disabled", exc)
        from themover.outputs.sink import RecordingSink

        return RecordingSink()
