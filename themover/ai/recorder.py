"""Record how the player currently plays: screenshots + keyboard/mouse activity.

The recording is summarised into compact statistics and a handful of frames so
Claude can understand the game without us shipping a video.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class InputEvent:
    t: float
    kind: str  # key_down | key_up | mouse_down | mouse_up | mouse_move | scroll
    name: str = ""
    x: float = 0.0
    y: float = 0.0


@dataclass
class Frame:
    t: float
    jpeg: bytes
    width: int
    height: int


@dataclass
class Recording:
    started_at: float = 0.0
    duration: float = 0.0
    frames: list[Frame] = field(default_factory=list)
    events: list[InputEvent] = field(default_factory=list)
    game_hint: str = ""
    notes: str = ""

    # ------------------------------------------------------------ stats
    def stats(self) -> dict[str, Any]:
        presses: Counter[str] = Counter()
        hold_total: dict[str, float] = defaultdict(float)
        down_since: dict[str, float] = {}
        combos: Counter[str] = Counter()
        clicks: Counter[str] = Counter()
        scroll_ticks = 0
        mouse_path = 0.0
        last_pos: Optional[tuple[float, float]] = None
        move_seconds: set[int] = set()
        for ev in self.events:
            if ev.kind == "key_down":
                if ev.name not in down_since:
                    down_since[ev.name] = ev.t
                    presses[ev.name] += 1
                    held = sorted(down_since)
                    if len(held) > 1:
                        combos["+".join(held)] += 1
            elif ev.kind == "key_up":
                t0 = down_since.pop(ev.name, None)
                if t0 is not None:
                    hold_total[ev.name] += ev.t - t0
            elif ev.kind == "mouse_down":
                clicks[ev.name] += 1
                presses[f"mouse.{ev.name}"] += 1
                down_since[f"mouse.{ev.name}"] = ev.t
            elif ev.kind == "mouse_up":
                t0 = down_since.pop(f"mouse.{ev.name}", None)
                if t0 is not None:
                    hold_total[f"mouse.{ev.name}"] += ev.t - t0
            elif ev.kind == "mouse_move":
                if last_pos is not None:
                    d = ((ev.x - last_pos[0]) ** 2 + (ev.y - last_pos[1]) ** 2) ** 0.5
                    mouse_path += d
                    if d > 0:
                        move_seconds.add(int(ev.t))
                last_pos = (ev.x, ev.y)
            elif ev.kind == "scroll":
                scroll_ticks += int(ev.y)
        end = self.duration or (self.events[-1].t if self.events else 0.0)
        for name, t0 in down_since.items():
            hold_total[name] += max(0.0, end - t0)
        keys = []
        for name, count in presses.most_common():
            total = hold_total.get(name, 0.0)
            keys.append(
                {
                    "input": name,
                    "presses": count,
                    "held_seconds": round(total, 2),
                    "avg_hold_ms": int(1000 * total / count) if count else 0,
                    "style": "held" if count and total / count > 0.4 else "tapped",
                }
            )
        return {
            "duration_seconds": round(end, 1),
            "inputs": keys,
            "combos": [{"keys": k, "count": c} for k, c in combos.most_common(8)],
            "mouse": {
                "clicks": dict(clicks),
                "path_pixels": int(mouse_path),
                "seconds_moving": len(move_seconds),
                "fraction_moving": round(len(move_seconds) / max(1.0, end), 2),
                "scroll_ticks": scroll_ticks,
                "looks_like": _mouse_style(mouse_path, len(move_seconds), end, clicks),
            },
            "frames": len(self.frames),
        }

    def summary_text(self) -> str:
        st = self.stats()
        lines = [f"Recording length: {st['duration_seconds']} s, {st['frames']} screenshots."]
        if self.game_hint:
            lines.append(f"Player says the game is: {self.game_hint}")
        if self.notes:
            lines.append(f"Player notes: {self.notes}")
        lines.append("Inputs used (most frequent first):")
        if not st["inputs"]:
            lines.append("  (no keyboard/mouse input was captured)")
        for k in st["inputs"][:30]:
            lines.append(f"  {k['input']}: {k['presses']} presses, held {k['held_seconds']}s total, avg {k['avg_hold_ms']}ms -> {k['style']}")
        if st["combos"]:
            lines.append("Common simultaneous keys: " + ", ".join(f"{c['keys']} x{c['count']}" for c in st["combos"]))
        m = st["mouse"]
        lines.append(
            f"Mouse: {m['path_pixels']} px travelled, moving {int(m['fraction_moving'] * 100)}% of the time, "
            f"clicks {m['clicks']}, scroll {m['scroll_ticks']} -> {m['looks_like']}"
        )
        return "\n".join(lines)

    def pick_frames(self, max_frames: int) -> list[Frame]:
        if len(self.frames) <= max_frames:
            return list(self.frames)
        step = len(self.frames) / max_frames
        return [self.frames[int(i * step)] for i in range(max_frames)]

    def save(self, folder: Path) -> Path:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(self.frames):
            (folder / f"frame_{i:03d}.jpg").write_bytes(f.jpeg)
        (folder / "events.json").write_text(
            json.dumps({"duration": self.duration, "game_hint": self.game_hint, "notes": self.notes,
                        "events": [e.__dict__ for e in self.events], "stats": self.stats()}, indent=1),
            encoding="utf-8",
        )
        return folder


def _mouse_style(path: float, moving_seconds: int, duration: float, clicks: Counter) -> str:
    if duration <= 0 or path < 200:
        return "mouse barely used (keyboard or gamepad game)"
    frac = moving_seconds / duration
    if frac > 0.6 and path / duration > 400:
        return "continuous camera/aim control (first- or third-person)"
    if sum(clicks.values()) > duration * 0.5:
        return "click-heavy (point-and-click / RTS / menu driven)"
    return "occasional pointing (menus, strategy or cursor-based)"


class ScreenGrabber:
    """Screenshots via ``mss`` (falls back to a blank frame when unavailable)."""

    def __init__(self, max_width: int = 1024, quality: int = 70) -> None:
        self.max_width = max_width
        self.quality = quality
        self._mss = None
        try:
            import mss  # type: ignore

            self._mss = mss.mss()
        except Exception as exc:  # pragma: no cover
            log.warning("screen capture unavailable: %s", exc)

    def grab(self) -> Optional[Frame]:
        from PIL import Image

        if self._mss is None:
            return None
        try:
            monitor = self._mss.monitors[1] if len(self._mss.monitors) > 1 else self._mss.monitors[0]
            shot = self._mss.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as exc:  # pragma: no cover
            log.debug("grab failed: %s", exc)
            return None
        return frame_from_image(img, self.max_width, self.quality)

    def close(self) -> None:
        if self._mss is not None:
            try:
                self._mss.close()
            except Exception:
                pass


def frame_from_image(img, max_width: int = 1024, quality: int = 70, t: float = 0.0) -> Frame:
    from PIL import Image

    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, max(1, int(img.height * ratio))), Image.BILINEAR)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return Frame(t=t, jpeg=buf.getvalue(), width=img.width, height=img.height)


class InputListener:
    """pynput keyboard/mouse hooks feeding :class:`InputEvent` objects."""

    def __init__(self, sink: Callable[[InputEvent], None], t0: float) -> None:
        self.sink = sink
        self.t0 = t0
        self._kb = None
        self._mouse = None
        self._last_move = 0.0

    def start(self) -> bool:
        try:
            from pynput import keyboard, mouse  # type: ignore
        except Exception as exc:
            log.warning("input capture unavailable: %s", exc)
            return False

        def key_name(key) -> str:
            try:
                if getattr(key, "char", None):
                    return key.char.lower()
            except Exception:
                pass
            name = getattr(key, "name", None) or str(key)
            return {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "shift_l": "shift", "shift_r": "shift", "alt_l": "alt", "alt_r": "alt"}.get(name, name)

        def now() -> float:
            return time.monotonic() - self.t0

        self._kb = keyboard.Listener(
            on_press=lambda k: self.sink(InputEvent(now(), "key_down", key_name(k))),
            on_release=lambda k: self.sink(InputEvent(now(), "key_up", key_name(k))),
        )

        def on_move(x, y):
            t = now()
            if t - self._last_move >= 0.05:  # 20 Hz is plenty for statistics
                self._last_move = t
                self.sink(InputEvent(t, "mouse_move", "", float(x), float(y)))

        def on_click(x, y, button, pressed):
            self.sink(InputEvent(now(), "mouse_down" if pressed else "mouse_up", button.name, float(x), float(y)))

        def on_scroll(x, y, dx, dy):
            self.sink(InputEvent(now(), "scroll", "", float(dx), float(dy)))

        self._mouse = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        try:
            self._kb.start()
            self._mouse.start()
        except Exception as exc:  # pragma: no cover
            log.warning("input listeners failed to start: %s", exc)
            return False
        return True

    def stop(self) -> None:
        for l in (self._kb, self._mouse):
            if l is not None:
                try:
                    l.stop()
                except Exception:
                    pass


class SessionRecorder:
    """Runs a timed recording on a background thread."""

    def __init__(self, seconds: float = 45.0, fps: float = 1.0, grabber: Optional[ScreenGrabber] = None) -> None:
        self.seconds = seconds
        self.fps = fps
        self.grabber = grabber
        self.recording = Recording()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._listener: Optional[InputListener] = None
        self.on_progress: Optional[Callable[[float, float], None]] = None  # (elapsed, total)
        self.on_done: Optional[Callable[[Recording], None]] = None
        self.input_capture_ok = False
        self.active = False

    def start(self) -> None:
        self.recording = Recording(started_at=time.time())
        self._stop.clear()
        self.active = True
        t0 = time.monotonic()
        self._listener = InputListener(self.recording.events.append, t0)
        self.input_capture_ok = self._listener.start()
        if self.grabber is None:
            self.grabber = ScreenGrabber()
        self._thread = threading.Thread(target=self._run, args=(t0,), name="recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, t0: float) -> None:
        interval = 1.0 / max(0.1, self.fps)
        next_frame = t0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                elapsed = now - t0
                if elapsed >= self.seconds:
                    break
                if now >= next_frame:
                    frame = self.grabber.grab() if self.grabber else None
                    if frame is not None:
                        frame.t = elapsed
                        self.recording.frames.append(frame)
                    next_frame += interval
                if self.on_progress:
                    try:
                        self.on_progress(elapsed, self.seconds)
                    except Exception:
                        pass
                time.sleep(0.05)
        finally:
            self.recording.duration = time.monotonic() - t0
            if self._listener:
                self._listener.stop()
            self.active = False
            if self.on_done:
                try:
                    self.on_done(self.recording)
                except Exception as exc:
                    log.exception("on_done failed: %s", exc)
