"""Plugins: small Python files the coach (or the player) drops into ``<app data>/plugins/``.

They run inside the app with full access to the controllers, the camera
tracker, the mapping engine and the output sink, so anything the built-in
vocabulary cannot express (a combo, a sequence, a two-hand distance zoom, a
custom detector, a macro, a timer...) can be added without rebuilding the app.

A plugin is a module with any of these functions::

    NAME = "lasso"                       # optional; the file name is the default
    DESCRIPTION = "swing right hand in a circle twice to press R"

    def setup(api): ...                 # once, after loading
    def signals(api) -> dict: ...       # every tick: {"lasso": 1.0} -> usable as source "plugin.lasso"
    def on_tick(api, dt): ...           # every engine tick (100 Hz): read signals, press keys, move the mouse
    def on_frame(api, index, accel, gyro, t, trigger, move): ...   # every IMU frame (~400 Hz, controller thread)
    def on_hit(api, index, hit): ...    # drum hit detected (controller thread)
    def on_camera(api, frame): ...      # every camera frame (BGR numpy array, camera thread)
    def teardown(api): ...              # before unload / reload

``api`` is a :class:`PluginAPI` (see its docstring).  Errors are caught and
shown in the coach chat / Setup so a broken plugin never takes the app down.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import math
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from themover.config import app_data_dir

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 25
HOOKS = ("setup", "signals", "on_tick", "on_frame", "on_hit", "on_camera", "teardown")

PLUGIN_GUIDE = '''PLUGINS (full access - use them whenever the mapping vocabulary cannot express what the player wants)
A plugin is a small Python file that runs inside The Mover with access to everything. Write one with write_plugin(name, code);
it is loaded immediately (and reloaded on every write), saved in the player's plugins folder and survives restarts.
Module-level hooks (all optional):
  DESCRIPTION = "one line shown to the player"
  def setup(api)                       # once after loading
  def signals(api) -> dict[str, float] # every tick; {"lasso": 1.0} becomes source "plugin.lasso" for normal bindings
  def on_tick(api, dt)                 # every engine tick (~100 Hz): read signals, press keys, move the mouse, rumble
  def on_frame(api, index, accel, gyro, t, trigger, move)  # every IMU frame (~400 Hz, controller thread; keep it tiny)
  def on_hit(api, index, hit)          # a drum hit (hit.kind 'don'/'kat', hit.strength g, hit.t)
  def on_camera(api, frame)            # every camera frame, BGR numpy array (camera thread)
  def teardown(api)
api (PluginAPI):
  api.read("c0.orient.roll")           # any source from the vocabulary, plugin.* included -> float
  api.controller(0)                    # MoveState: .accel/.gyro (Vec3 x y z, g and rad/s), .roll/.pitch/.yaw (deg), .trigger 0..1,
                                       #   .buttons dict, .tracker (.tracked .in_zone .x .y .depth .vx .vy .radius)
  api.world                            # WorldState of the last tick (.controllers, .t, .dt); api.tracker is the camera tracker
  api.profile, api.settings, api.runtime, api.engine, api.devices   # the real objects, nothing is hidden
  api.press("key.w") / api.release("key.w") / api.tap("key.r", ms=60)      # targets: key.*, mouse.left/right/middle, gamepad.a ...
  api.mouse_move(dx, dy) / api.mouse_scroll(ticks) / api.axis("left_stick_x", -1..1)
  api.rumble(0, 0.8, ms=200) / api.led(0, (255, 0, 0), ms=300)
  api.now() (monotonic seconds), api.store (dict that survives ticks), api.log("text") (last 200 lines shown by plugin_status)
Outputs only reach the game while the player has pressed PLAY (otherwise they are recorded, so testing is safe).
Rules: keep on_frame cheap, never sleep or block, catch nothing (errors are reported to you), use api.store for state, and
after writing call plugin_status (and read_live_signals) to verify it loaded and behaves; then explain it to the player in
one or two plain sentences. Bind plugin signals with add_bindings (source "plugin.<name>", mode tap/hold/axis) when a normal
binding is the cleanest way to reach the game. read_app_file lets you read the app's own source when you need exact details.
'''

EXAMPLE_PLUGIN = '''DESCRIPTION = "Shake the left controller twice within a second to press R (reload)"

def setup(api):
    api.store["times"] = []

def on_tick(api, dt):
    if api.read("c1.gesture.shake") > 0.5 and (not api.store["times"] or api.now() - api.store["times"][-1] > 0.15):
        api.store["times"].append(api.now())
    api.store["times"] = [t for t in api.store["times"] if api.now() - t < 1.0]
    if len(api.store["times"]) >= 2:
        api.store["times"].clear()
        api.tap("key.r", ms=60)
        api.rumble(1, 0.6, ms=120)

def signals(api):
    return {"reload_armed": 1.0 if api.store["times"] else 0.0}
'''


def plugins_dir() -> Path:
    d = app_data_dir() / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    if not n:
        raise ValueError("plugin name must contain letters or digits")
    return n[:40]


class PluginAPI:
    """What a plugin sees.  Everything is the live object; nothing is copied."""

    def __init__(self, runtime, name: str, manager: "PluginManager") -> None:
        self.runtime = runtime
        self.name = name
        self.store: dict[str, Any] = {}
        self._manager = manager

    # ---------------------------------------------------------- reading
    @property
    def world(self):
        return self.runtime.world

    @property
    def engine(self):
        return self.runtime.engine

    @property
    def devices(self):
        return self.runtime.devices

    @property
    def tracker(self):
        return self.runtime.devices.tracker

    @property
    def profile(self):
        return self.runtime.profile

    @property
    def settings(self):
        return self.runtime.settings

    def controller(self, index: int):
        ctrls = self.runtime.devices.controllers
        return ctrls[index].state if index < len(ctrls) else None

    def read(self, source: str) -> float:
        try:
            return float(self.runtime.read_signal(source))
        except Exception:
            return 0.0

    @staticmethod
    def now() -> float:
        return time.monotonic()

    # ---------------------------------------------------------- output
    def press(self, target: str) -> None:
        self.runtime.engine.hold(target)

    def release(self, target: str) -> None:
        self.runtime.engine.unhold(target)

    def tap(self, target: str, ms: int = 60) -> None:
        self.runtime.engine.fast_tap(target, int(ms))

    def mouse_move(self, dx: float, dy: float) -> None:
        eng = self.runtime.engine
        with eng._lock:
            eng.sink.mouse_move(int(round(dx)), int(round(dy)))
            eng.sink.flush()

    def mouse_scroll(self, ticks: int) -> None:
        eng = self.runtime.engine
        with eng._lock:
            eng.sink.mouse_scroll(int(ticks))
            eng.sink.flush()

    def axis(self, name: str, value: float) -> None:
        eng = self.runtime.engine
        with eng._lock:
            eng.sink.gamepad_axis(name, max(-1.0, min(1.0, float(value))))
            eng.sink.flush()

    def rumble(self, index: int, strength: float = 0.8, ms: int = 200) -> None:
        self.runtime.buzz(int(index), float(strength), None, int(ms))

    def led(self, index: int, rgb, ms: int = 300) -> None:
        self.runtime.buzz(int(index), 0.0, tuple(int(c) for c in rgb), int(ms))

    def log(self, message: str) -> None:
        self._manager.add_log(self.name, str(message))


@dataclass
class LoadedPlugin:
    name: str
    path: Path
    module: Any = None
    api: Optional[PluginAPI] = None
    description: str = ""
    error: str = ""
    consecutive_errors: int = 0
    disabled: bool = False
    signals: dict[str, float] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)
    calls: int = 0

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "hooks": self.hooks, "ok": not self.error and not self.disabled,
                "disabled": self.disabled, "error": self.error, "signals": {f"plugin.{k}": round(v, 3) for k, v in self.signals.items()}, "calls": self.calls}


class PluginManager:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.plugins: dict[str, LoadedPlugin] = {}
        self.values: dict[str, float] = {}
        self.logs: list[tuple[float, str, str]] = []
        self._lock = threading.RLock()
        self._taps_installed = False

    # ---------------------------------------------------------- files
    @staticmethod
    def directory() -> Path:
        return plugins_dir()

    def path_for(self, name: str) -> Path:
        return self.directory() / f"{safe_name(name)}.py"

    def load_all(self) -> list[str]:
        reports = []
        for path in sorted(self.directory().glob("*.py")):
            if path.name.startswith("_"):
                continue
            reports.append(self.load(path.stem))
        self._install_taps()
        return reports

    def unload_all(self) -> None:
        for name in list(self.plugins):
            self.unload(name)

    def write(self, name: str, code: str, description: str = "") -> str:
        """Save (or overwrite) a plugin file and (re)load it. Returns a report incl. any error."""
        name = safe_name(name)
        if description and "DESCRIPTION" not in code:
            code = f"DESCRIPTION = {description!r}\n\n" + code
        try:
            compile(code, f"{name}.py", "exec")
        except SyntaxError as exc:
            return f"Not saved: syntax error in {name}.py line {exc.lineno}: {exc.msg}"
        self.path_for(name).write_text(code, encoding="utf-8")
        return self.load(name)

    def delete(self, name: str) -> str:
        name = safe_name(name)
        self.unload(name)
        p = self.path_for(name)
        if p.exists():
            p.unlink()
            return f"Deleted plugin {name}."
        return f"No plugin named {name}."

    def read(self, name: str) -> str:
        p = self.path_for(name)
        if not p.exists():
            raise FileNotFoundError(f"no plugin named {safe_name(name)}")
        return p.read_text(encoding="utf-8")

    # ---------------------------------------------------------- loading
    def load(self, name: str) -> str:
        name = safe_name(name)
        self.unload(name)
        path = self.path_for(name)
        plugin = LoadedPlugin(name=name, path=path)
        with self._lock:
            self.plugins[name] = plugin
        if not path.exists():
            plugin.error = "file not found"
            return f"{name}: file not found"
        try:
            spec = importlib.util.spec_from_file_location(f"themover_plugin_{name}", path)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            plugin.error = "import failed: " + traceback.format_exc(limit=3).strip().splitlines()[-1]
            plugin.disabled = True
            self.add_log(name, plugin.error)
            return f"{name}: {plugin.error}"
        plugin.module = module
        plugin.description = str(getattr(module, "DESCRIPTION", "") or "")
        plugin.hooks = [h for h in HOOKS if callable(getattr(module, h, None))]
        plugin.api = PluginAPI(self.runtime, name, self)
        if "setup" in plugin.hooks:
            self._call(plugin, "setup", plugin.api)
        self._install_taps()
        self.add_log(name, "loaded" + (f" ({plugin.error})" if plugin.error else ""))
        return f"{name}: loaded, hooks {plugin.hooks or 'none'}" + (f". Error in setup: {plugin.error}" if plugin.error else "") + (f". {plugin.description}" if plugin.description else "")

    def unload(self, name: str) -> None:
        with self._lock:
            plugin = self.plugins.pop(name, None)
        if plugin is None:
            return
        if plugin.module is not None and "teardown" in plugin.hooks:
            self._call(plugin, "teardown", plugin.api)
        with self._lock:
            for key in list(self.values):
                if key in {f"plugin.{k}" for k in plugin.signals}:
                    self.values.pop(key, None)

    # ---------------------------------------------------------- running
    def _call(self, plugin: LoadedPlugin, hook: str, *args) -> Any:
        if plugin.disabled or plugin.module is None:
            return None
        fn = getattr(plugin.module, hook, None)
        if fn is None:
            return None
        try:
            out = fn(*args)
            plugin.consecutive_errors = 0
            plugin.calls += 1
            if plugin.error and hook != "setup":
                plugin.error = ""
            return out
        except Exception:
            tb = traceback.format_exc(limit=4).strip().splitlines()
            plugin.error = f"{hook}: " + " | ".join(tb[-2:])
            plugin.consecutive_errors += 1
            if plugin.consecutive_errors == 1 or plugin.consecutive_errors % 10 == 0:
                self.add_log(plugin.name, plugin.error)
            if plugin.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                plugin.disabled = True
                self.add_log(plugin.name, f"disabled after {MAX_CONSECUTIVE_ERRORS} consecutive errors; fix and reload it")
            return None

    def tick(self, dt: float) -> None:
        if not self.plugins:
            return
        with self._lock:
            plugins = list(self.plugins.values())
        for plugin in plugins:
            if "on_tick" in plugin.hooks:
                self._call(plugin, "on_tick", plugin.api, dt)
            if "signals" in plugin.hooks:  # after on_tick so published values reflect this tick's state
                out = self._call(plugin, "signals", plugin.api)
                if isinstance(out, dict):
                    clean = {}
                    for k, v in out.items():
                        try:
                            clean[str(k)] = float(v)
                        except (TypeError, ValueError):
                            pass
                    plugin.signals = clean
                    with self._lock:
                        for k, v in clean.items():
                            self.values[f"plugin.{k}"] = v

    def value(self, source: str) -> float:
        return self.values.get(source, 0.0)

    def _install_taps(self) -> None:
        if self._taps_installed:
            return
        dev = self.runtime.devices
        dev.frame_taps.append(self._frame_tap)
        dev.hit_taps.append(self._hit_tap)
        dev.camera_taps.append(self._camera_tap)
        self._taps_installed = True

    def _frame_tap(self, index, accel, gyro, t, trigger, move) -> None:
        for plugin in list(self.plugins.values()):
            if "on_frame" in plugin.hooks:
                self._call(plugin, "on_frame", plugin.api, index, accel, gyro, t, trigger, move)

    def _hit_tap(self, index, hit) -> None:
        for plugin in list(self.plugins.values()):
            if "on_hit" in plugin.hooks:
                self._call(plugin, "on_hit", plugin.api, index, hit)

    def _camera_tap(self, frame) -> None:
        for plugin in list(self.plugins.values()):
            if "on_camera" in plugin.hooks:
                self._call(plugin, "on_camera", plugin.api, frame)

    # ---------------------------------------------------------- reporting
    def add_log(self, name: str, message: str) -> None:
        with self._lock:
            self.logs.append((time.time(), name, message))
            del self.logs[:-200]
        log.info("plugin %s: %s", name, message)

    def status(self) -> dict[str, Any]:
        with self._lock:
            plugins = [p.status() for p in self.plugins.values()]
            logs = [f"{time.strftime('%H:%M:%S', time.localtime(t))} {n}: {m}" for t, n, m in self.logs[-30:]]
        return {"folder": str(self.directory()), "plugins": plugins, "recent_log": logs}

    def summary(self) -> str:
        if not self.plugins:
            return ""
        parts = []
        for p in self.plugins.values():
            state = "disabled" if p.disabled else ("error" if p.error else "ok")
            parts.append(f"{p.name} ({state})" + (f": {p.description}" if p.description else ""))
        return "Plugins: " + "; ".join(parts)

    # ---------------------------------------------------------- console
    def run_python(self, code: str, timeout_note: bool = True) -> str:
        """Execute code inside the app (api / runtime in scope); returns printed output and the value of `result`."""
        api = PluginAPI(self.runtime, "console", self)
        ns: dict[str, Any] = {"api": api, "runtime": self.runtime, "engine": self.runtime.engine, "devices": self.runtime.devices,
                              "plugins": self, "math": math, "time": time}
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<coach>", "exec"), ns)
        except Exception:
            return buf.getvalue() + "Error: " + traceback.format_exc(limit=3).strip().splitlines()[-1]
        out = buf.getvalue()
        if "result" in ns:
            out += ("\n" if out else "") + f"result = {ns['result']!r}"
        return out[:20000] or "(no output; assign to `result` or print something)"
