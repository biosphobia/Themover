from themover.config import Settings, app_data_dir, load_settings, save_settings
from themover.outputs.gamepad import NullGamepad, create_gamepad_sink
from themover.outputs.keyboard_mouse import vk_code
from themover.outputs.sink import CompositeSink, RecordingSink


def test_settings_roundtrip(tmp_path):
    s = Settings(api_key="sk-test", model="claude-opus-5", controller_colors=[[1, 2, 3], [4, 5, 6]])
    path = save_settings(s)
    assert path.parent == app_data_dir()
    loaded = load_settings()
    assert loaded.api_key == "sk-test" and loaded.controller_colors == [[1, 2, 3], [4, 5, 6]]
    assert Settings.from_dict({"api_key": "x", "unknown": 1}).api_key == "x"


def test_effective_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert Settings().effective_api_key == "env-key"
    assert Settings(api_key="mine").effective_api_key == "mine"


def test_vk_codes():
    assert vk_code("a") == 0x41 and vk_code("space") == 0x20 and vk_code("f5") == 0x74 and vk_code("nope") is None


def test_composite_sink_routes():
    km, pad = RecordingSink(), RecordingSink()
    c = CompositeSink(km, pad)
    c.key_down("w"); c.gamepad_button("a", True); c.gamepad_axis("left_stick_x", 0.5); c.mouse_move(3, 4)
    assert km.keys_down == {"w"} and pad.gamepad_buttons == {"a": True} and km.mouse_delta == [3, 4]
    c.release_all()
    assert not km.keys_down and pad.gamepad_buttons == {"a": False}
    assert "dry run" in c.description


def test_gamepad_fallback():
    pad = create_gamepad_sink(enabled=False)
    assert isinstance(pad, NullGamepad)
    pad.gamepad_axis("left_stick_x", 1.0)
    assert pad.axes["left_stick_x"] == 1.0
