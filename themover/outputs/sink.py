"""Abstract output interface plus a recording sink used in tests."""
from __future__ import annotations

from dataclasses import dataclass, field


class OutputSink:
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def mouse_down(self, button: str) -> None: ...
    def mouse_up(self, button: str) -> None: ...
    def mouse_move(self, dx: int, dy: int) -> None: ...
    def mouse_move_abs(self, x: float, y: float) -> None:
        """x, y in 0..1 of the primary screen."""
    def mouse_scroll(self, ticks: int) -> None: ...
    def gamepad_button(self, name: str, pressed: bool) -> None: ...
    def gamepad_axis(self, name: str, value: float) -> None: ...
    def flush(self) -> None: ...
    def release_all(self) -> None: ...
    def close(self) -> None: ...

    @property
    def description(self) -> str:
        return type(self).__name__

    def status(self) -> str:
        """Human-readable health line ('' when there is nothing to say)."""
        return ""


@dataclass
class RecordingSink(OutputSink):
    """Remembers everything it was told - handy for tests and the UI 'dry run'."""

    events: list[tuple] = field(default_factory=list)
    keys_down: set[str] = field(default_factory=set)
    mouse_down_buttons: set[str] = field(default_factory=set)
    gamepad_buttons: dict[str, bool] = field(default_factory=dict)
    gamepad_axes: dict[str, float] = field(default_factory=dict)
    mouse_delta: list[float] = field(default_factory=lambda: [0.0, 0.0])
    mouse_abs: list[float] = field(default_factory=lambda: [0.5, 0.5])
    scroll: int = 0

    def key_down(self, key: str) -> None:
        self.events.append(("key_down", key))
        self.keys_down.add(key)

    def key_up(self, key: str) -> None:
        self.events.append(("key_up", key))
        self.keys_down.discard(key)

    def mouse_down(self, button: str) -> None:
        self.events.append(("mouse_down", button))
        self.mouse_down_buttons.add(button)

    def mouse_up(self, button: str) -> None:
        self.events.append(("mouse_up", button))
        self.mouse_down_buttons.discard(button)

    def mouse_move(self, dx: int, dy: int) -> None:
        if dx or dy:
            self.events.append(("mouse_move", dx, dy))
            self.mouse_delta[0] += dx
            self.mouse_delta[1] += dy

    def mouse_move_abs(self, x: float, y: float) -> None:
        self.events.append(("mouse_abs", x, y))
        self.mouse_abs = [x, y]

    def mouse_scroll(self, ticks: int) -> None:
        if ticks:
            self.events.append(("scroll", ticks))
            self.scroll += ticks

    def gamepad_button(self, name: str, pressed: bool) -> None:
        self.events.append(("gamepad_button", name, pressed))
        self.gamepad_buttons[name] = pressed

    def gamepad_axis(self, name: str, value: float) -> None:
        self.gamepad_axes[name] = value

    def release_all(self) -> None:
        for k in list(self.keys_down):
            self.key_up(k)
        for b in list(self.mouse_down_buttons):
            self.mouse_up(b)
        for n in list(self.gamepad_buttons):
            self.gamepad_button(n, False)
        self.gamepad_axes = {}

    @property
    def description(self) -> str:
        return "dry run (nothing is sent to the game)"

    def status(self) -> str:
        return f"dry run: {len(self.events)} events recorded"


class CompositeSink(OutputSink):
    """Route keyboard/mouse to one backend and gamepad to another."""

    def __init__(self, keyboard_mouse: OutputSink, gamepad: OutputSink) -> None:
        self.km = keyboard_mouse
        self.pad = gamepad

    def key_down(self, key: str) -> None:
        self.km.key_down(key)

    def key_up(self, key: str) -> None:
        self.km.key_up(key)

    def mouse_down(self, button: str) -> None:
        self.km.mouse_down(button)

    def mouse_up(self, button: str) -> None:
        self.km.mouse_up(button)

    def mouse_move(self, dx: int, dy: int) -> None:
        self.km.mouse_move(dx, dy)

    def mouse_move_abs(self, x: float, y: float) -> None:
        self.km.mouse_move_abs(x, y)

    def mouse_scroll(self, ticks: int) -> None:
        self.km.mouse_scroll(ticks)

    def gamepad_button(self, name: str, pressed: bool) -> None:
        self.pad.gamepad_button(name, pressed)

    def gamepad_axis(self, name: str, value: float) -> None:
        self.pad.gamepad_axis(name, value)

    def flush(self) -> None:
        self.km.flush()
        self.pad.flush()

    def release_all(self) -> None:
        self.km.release_all()
        self.pad.release_all()

    def close(self) -> None:
        self.release_all()
        self.km.close()
        self.pad.close()

    @property
    def description(self) -> str:
        return f"{self.km.description} + {self.pad.description}"

    def status(self) -> str:
        return " · ".join(t for t in (self.km.status(), self.pad.status()) if t)
