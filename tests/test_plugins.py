"""Plugins: coach-written Python that runs inside the app with full access."""
import json
import time

import pytest

from themover.config import Settings
from themover.mapping.profile import Binding, Profile
from themover.mapping.runtime import Runtime
from themover.mapping.templates import load_template
from themover.outputs.sink import RecordingSink
from themover import plugins as P


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "plugins_dir", lambda: tmp_path / "plugins")
    (tmp_path / "plugins").mkdir()
    rt = Runtime(Settings(camera_backend="synthetic", controller_backend="simulated"), sink=RecordingSink())
    rt.set_profile(load_template("generic_gamepad"))
    rt.start_devices()
    try:
        yield rt
    finally:
        rt.plugins.unload_all()
        rt.stop_devices()


PLUGIN = '''
DESCRIPTION = "test plugin"

def setup(api):
    api.store["ticks"] = 0
    api.store["frames"] = 0

def signals(api):
    return {"ticks": api.store["ticks"], "roll": api.read("c0.orient.roll"), "junk": "not a number"}

def on_tick(api, dt):
    api.store["ticks"] += 1
    if api.store["ticks"] == 3:
        api.tap("key.r", ms=20)
        api.press("key.w")
    if api.store["ticks"] == 5:
        api.release("key.w")
        api.rumble(0, 0.5, ms=100)
        api.log("done")

def on_frame(api, index, accel, gyro, t, trigger, move):
    api.store["frames"] += 1

def teardown(api):
    api.store["torn"] = True
'''


def test_plugin_signals_outputs_and_lifecycle(runtime):
    report = runtime.plugins.write("Combo Test", PLUGIN)
    assert report.startswith("combo_test: loaded") and "on_tick" in report and "test plugin" in report
    assert (runtime.plugins.directory() / "combo_test.py").exists()
    for _ in range(6):
        runtime.step(0.01)
    assert runtime.plugins.value("plugin.ticks") == 6.0 and "plugin.junk" not in runtime.plugins.values
    assert runtime.read_signal("plugin.ticks") == 6.0 and runtime.signal_snapshot()["plugin.ticks"] == 6.0
    events = [e for e in runtime.sink.events if e[0] in ("key_down", "key_up")]
    assert ("key_down", "r") in events and ("key_down", "w") in events and ("key_up", "w") in events
    status = runtime.plugins.status()
    plug = status["plugins"][0]
    assert plug["ok"] and plug["signals"]["plugin.ticks"] == 6.0 and "signals" in plug["hooks"]
    assert any("done" in line for line in status["recent_log"])
    assert runtime.plugins.plugins["combo_test"].api.store["frames"] > 0  # on_frame received IMU frames
    assert "combo_test (ok): test plugin" in runtime.plugins.summary()
    # A binding can use the plugin signal like any other source.
    p = Profile(name="p", bindings=[Binding("plugin.ticks", "key.space", mode="hold", threshold=5.5)])
    assert not p.validate()
    runtime.set_profile(p)
    runtime.step(0.01)
    assert ("key_down", "space") in runtime.sink.events[-3:] or runtime.engine._pressed_buttons.get("key.space")
    api = runtime.plugins.plugins["combo_test"].api
    assert runtime.plugins.read("combo_test").startswith("\nDESCRIPTION")
    assert "Deleted" in runtime.plugins.delete("combo_test")
    assert api.store.get("torn") and not runtime.plugins.plugins and "plugin.ticks" not in runtime.plugins.values


def test_broken_plugins_are_reported_not_fatal(runtime):
    assert "syntax error" in runtime.plugins.write("bad", "def on_tick(api, dt):\n    return (")
    assert not (runtime.plugins.directory() / "bad.py").exists()
    report = runtime.plugins.write("boom", "import nonexistent_module_xyz\n")
    assert "import failed" in report and runtime.plugins.plugins["boom"].disabled
    runtime.plugins.write("crashy", "def on_tick(api, dt):\n    raise ValueError('nope')\n")
    for _ in range(P.MAX_CONSECUTIVE_ERRORS + 1):
        runtime.step(0.01)
    plug = runtime.plugins.plugins["crashy"]
    assert plug.disabled and "nope" in plug.error
    assert any("disabled after" in line for line in runtime.plugins.status()["recent_log"])
    # Reloading after a fix clears the error.
    assert "loaded" in runtime.plugins.write("crashy", "def on_tick(api, dt):\n    pass\n")
    runtime.step(0.01)
    assert runtime.plugins.plugins["crashy"].status()["ok"]
    with pytest.raises(ValueError):
        P.safe_name("!!!")


def test_run_python_and_load_all(runtime):
    out = runtime.plugins.run_python("print('hi')\nresult = api.read('c0.trigger') + len(devices.controllers)")
    assert out.startswith("hi") and "result = 2.0" in out
    assert "Error" in runtime.plugins.run_python("1/0")
    (runtime.plugins.directory() / "auto.py").write_text("def signals(api):\n    return {'auto': 1}\n")
    (runtime.plugins.directory() / "_private.py").write_text("raise RuntimeError('never loaded')\n")
    reports = runtime.plugins.load_all()
    assert len(reports) == 1 and "auto" in runtime.plugins.plugins
    runtime.step(0.01)
    assert runtime.plugins.value("plugin.auto") == 1.0
    assert runtime.devices.frame_taps and runtime.devices.camera_taps


def test_chat_developer_tools(runtime, tmp_path):
    from themover.ai.chat import CoachChat, DEV_TOOLS, TOOLS, app_source_root
    from themover.ai.client import ClaudeClient

    class Host:
        plugins = runtime.plugins

        def get_profile(self):
            return runtime.profile

        def apply_profile(self, p):
            runtime.set_profile(p)

        def live_signals(self):
            return runtime.signal_snapshot()

        def buzz(self, *a):
            pass

        def save_profile(self, n):
            return n

    chat = CoachChat(ClaudeClient(Settings(api_key="k"), client=object()), Host())
    assert len(chat._tools()) == len(TOOLS) + len(DEV_TOOLS)
    out = chat.execute_tool("write_plugin", {"name": "lasso", "code": "def signals(api):\n    return {'lasso': 1.0}\n", "description": "lasso gesture"})
    assert "lasso: loaded" in out and "lasso gesture" in out
    assert "DESCRIPTION = 'lasso gesture'" in chat.execute_tool("read_plugin", {"name": "lasso"})
    runtime.step(0.01)
    assert json.loads(chat.execute_tool("list_plugins", {}))[0]["signals"]["plugin.lasso"] == 1.0
    assert "recent_log" in json.loads(chat.execute_tool("plugin_status", {}))
    assert "result = 3" in chat.execute_tool("run_python", {"code": "result = 1 + 2"})
    files = json.loads(chat.execute_tool("list_app_files", {}))
    assert any(f["path"].endswith("mapping/engine.py") for f in files) and app_source_root() is not None
    text = chat.execute_tool("read_app_file", {"path": "themover/mapping/engine.py", "start_line": 1, "max_lines": 5})
    assert text.startswith("themover/mapping/engine.py lines 1-5") and "1: " in text
    with pytest.raises(ValueError):
        chat.execute_tool("read_app_file", {"path": "../../etc/passwd"})
    assert "Deleted" in chat.execute_tool("delete_plugin", {"name": "lasso"})

    class NoDev(Host):
        plugins = None

    chat2 = CoachChat(ClaudeClient(Settings(api_key="k"), client=object()), NoDev())
    assert chat2._tools() is TOOLS
    with pytest.raises(RuntimeError):
        chat2.execute_tool("run_python", {"code": "1"})
