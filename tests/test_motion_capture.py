"""Motion recording sessions: replay evaluation, auto-fit, tags, coach tools, recorder."""
import json
import math
import time

import numpy as np
import pytest

from themover.ai import motion_capture as M
from themover.ai.finetune import FINETUNE_INSTRUCTIONS, FinetuneTools, build_finetune_content
from themover.core.hits import HitConfig, hit_config_to_dict
from themover.core.state import Vec3

RATE = 200.0
DT = 1.0 / RATE


def synth_session(tmp_path, strokes, duration=6.0, noise=0.02):
    """A session whose right hand performs strokes at given (time, kind) with a stop at exactly t."""
    s = M.MotionSession(tmp_path / "motion-test")
    n = int(duration * RATE)
    t = np.arange(n) * DT
    d = np.zeros((n, len(M.COLUMNS)), np.float32)
    d[:, 2] = 1.0  # gravity on +z
    rng = np.random.default_rng(3)
    d[:, :3] += rng.normal(0, noise, (n, 3)).astype(np.float32)
    for stop_t, kind in strokes:
        direction = (0.0, 0.0, -1.0) if kind == "don" else (math.sin(math.radians(60)), 0.0, -math.cos(math.radians(60)))
        onset_i = int((stop_t - 0.085) * RATE)
        for k in range(int(0.06 * RATE)):  # onset lobe
            amp = 1.6 * math.sin(math.pi * (k + 1) / (int(0.06 * RATE) + 1))
            d[onset_i + k, :3] += np.array(direction) * amp
        stop_i = int(stop_t * RATE)
        for k in range(3):  # stop spike opposite to motion
            d[stop_i + k, :3] -= np.array(direction) * 3.5 * 0.5 ** k
    s.t = [t, np.zeros(0)]
    s.data = [d, np.zeros((0, len(M.COLUMNS)), np.float32)]
    s.meta = {"duration": duration, "hit_config": hit_config_to_dict(HitConfig()), "profile": "test"}
    return s


def test_replay_matches_tags_and_scores(tmp_path):
    strokes = [(1.0, "don"), (1.5, "don"), (2.2, "kat"), (3.0, "don"), (3.4, "kat")]
    s = synth_session(tmp_path, strokes)
    for t, kind in strokes:
        s.add_tag(t, kind, 0)
    res = s.evaluate(None, complete=True)
    assert res.tags == 5 and res.matched == 5 and res.missed == 0 and res.wrong_kind == 0 and res.false_positives == 0
    assert abs(res.mean_offset_ms) < 8 and res.std_offset_ms < 5
    assert "5/5 tags matched" in res.text()
    # A far too high stop threshold misses everything; the score says so.
    bad = s.evaluate({"stop_g": 7.5}, complete=True)
    assert bad.missed == 5 and bad.score < res.score


def test_auto_fit_recovers_from_bad_settings(tmp_path):
    strokes = [(1.0, "don"), (1.6, "kat"), (2.4, "don"), (3.1, "kat"), (3.8, "don")]
    s = synth_session(tmp_path, strokes)
    for t, kind in strokes:
        s.add_tag(t, kind, 0)
    bad = {"stop_g": 6.0, "onset_g": 1.5, "kat_angle_deg": 85.0}
    assert s.evaluate(bad, True).matched < 5
    cfg, res = M.auto_fit(s, bad, complete=True)
    assert res.matched == 5 and res.wrong_kind == 0
    assert cfg["stop_g"] < 6.0 and cfg["kat_angle_deg"] < 85.0


def test_session_roundtrip_tags_and_windows(tmp_path):
    s = synth_session(tmp_path, [(1.0, "don")])
    s.hits = [{"t": 1.004, "hand": 0, "kind": "don", "strength": 3.4, "stroke_ms": 80.0, "modifier": ""}]
    s.add_tag(1.0, "don", 0, "first hit")
    s.add_tag(0.5, "other", -1, "warm-up")
    folder = s.save()
    loaded = M.MotionSession.load(folder)
    assert loaded.duration == 6.0 and len(loaded.tags) == 2 and loaded.tags[0].note == "warm-up"  # sorted by time
    assert loaded.rate(0) == pytest.approx(RATE, rel=0.01) and len(loaded.hits) == 1
    win = loaded.tag_window(1, half_ms=100)
    assert win["tag"].startswith("don") and "c0" in win["hands"] and len(win["hands"]["c0"]["rows"]) >= 10
    assert win["hands"]["c0"]["columns"][0] == "ms" and win["detected_hits_nearby"]
    assert "Motion recording" in loaded.summary() and "tags: 2" in loaded.summary()
    loaded.remove_tag(0)
    loaded.save_tags()
    assert len(json.loads((folder / "tags.json").read_text())) == 1
    assert loaded.frame_at("camera", 1.0) is None and loaded.thumbnail("camera", 1.0) is None


def test_finetune_tools_and_content(tmp_path):
    strokes = [(1.0, "don"), (1.7, "kat")]
    s = synth_session(tmp_path, strokes)
    for t, kind in strokes:
        s.add_tag(t, kind, 0)
    settings = {"cfg": hit_config_to_dict(HitConfig(stop_g=6.0))}
    applied = []

    def apply(new):
        settings["cfg"] = dict(new)
        applied.append(dict(new))
        return dict(new)

    tools = FinetuneTools(s, apply_settings=apply, get_settings=lambda: settings["cfg"], complete=True)
    assert all(tools.handles(t["name"]) for t in tools.tools)
    summary = tools.execute("recording_summary", {})
    assert "0/2 tags matched" in summary and '"index": 1' in summary
    win = json.loads(tools.execute("tag_window", {"index": 0}))
    assert win["tag"].startswith("don")
    assert "no tag" in tools.execute("tag_window", {"index": 9})
    ev = json.loads(tools.execute("evaluate_hit_settings", {"settings": {"stop_g": 1.3}}))
    assert "2/2" in ev["summary"] and ev["settings"]["stop_g"] == 1.3 and not applied
    fit = json.loads(tools.execute("auto_fit_hit_settings", {}))
    assert fit["best_settings"]["stop_g"] < 6.0
    out = tools.execute("apply_hit_settings", {"settings": fit["best_settings"]})
    assert "Applied" in out and applied and applied[-1]["stop_g"] == fit["best_settings"]["stop_g"]
    assert json.loads(tools.execute("get_hit_settings", {}))["stop_g"] == fit["best_settings"]["stop_g"]
    with pytest.raises(ValueError):
        tools.execute("nope", {})
    content = build_finetune_content(s, "second one is a kat", complete=True)
    assert content[0]["type"] == "text" and "second one is a kat" in content[0]["text"]
    assert FINETUNE_INSTRUCTIONS.strip() in content[0]["text"] and "Per tag" in content[0]["text"]


def test_chat_attach_routes_finetune_tools(tmp_path):
    import types

    from themover.ai.chat import CoachChat, TOOLS
    from themover.ai.client import ClaudeClient
    from themover.config import Settings

    class Block(types.SimpleNamespace):
        def model_dump(self, exclude_none=True):
            return dict(self.__dict__)

    class FakeStream:
        def __init__(self, m):
            self.m = m

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            return iter(())

        def get_final_message(self):
            return self.m

    calls = []

    class Fake:
        def __init__(self):
            self.messages = self
            self.beta = types.SimpleNamespace(messages=self)
            self.script = [
                types.SimpleNamespace(content=[Block(type="tool_use", id="t1", name="evaluate_hit_settings", input={"settings": {"stop_g": 1.3}})], stop_reason="tool_use", model="m", usage=None),
                types.SimpleNamespace(content=[Block(type="text", text="Fitted.")], stop_reason="end_turn", model="m", usage=None),
            ]

        def stream(self, **kw):
            calls.append(dict(kw, messages=[dict(m) for m in kw["messages"]]))
            return FakeStream(self.script.pop(0))

    s = synth_session(tmp_path, [(1.0, "don")])
    s.add_tag(1.0, "don", 0)
    tools = FinetuneTools(s, apply_settings=lambda x: x, get_settings=lambda: {}, complete=True)

    class Host:
        def get_profile(self):
            from themover.mapping.profile import Profile

            return Profile(name="p")

        def apply_profile(self, p):
            pass

        def live_signals(self):
            return {}

        def buzz(self, *a):
            pass

        def save_profile(self, n):
            return n

    chat = CoachChat(ClaudeClient(Settings(api_key="k"), client=Fake()), Host())
    chat.attach(tools, "FT")
    out = chat.send([{"type": "text", "text": "fit please"}])
    assert out == "Fitted."
    assert len(calls[0]["tools"]) == len(TOOLS) + len(tools.tools) and calls[0]["system"][0]["text"].endswith("FT")
    result = calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "1/1 tags matched" in result["content"]
    chat.detach()
    assert chat._tools() == TOOLS


def test_recorder_captures_simulated_controllers_and_synthetic_camera(tmp_path, monkeypatch):
    from themover.config import Settings
    from themover.mapping.runtime import Runtime
    from themover.mapping.templates import load_template
    from themover.outputs.sink import RecordingSink

    monkeypatch.setattr(M, "sessions_dir", lambda: tmp_path / "rec")
    rt = Runtime(Settings(camera_backend="synthetic", controller_backend="simulated"), sink=RecordingSink())
    rt.set_profile(load_template("osu_taiko"))
    rt.start()
    try:
        rec = M.MotionRecorder(rt, seconds=1.2, camera=True, screen=False, inputs=False, explanation="test run")
        done = []
        rec.on_done = done.append
        rec.start()
        sim = rt.devices.controllers[0]
        time.sleep(0.3)
        sim.simulate_motion(Vec3(0, 0, 1.0), Vec3()); time.sleep(0.05)
        sim.simulate_motion(Vec3(0, 0, -1.2), Vec3()); time.sleep(0.03)
        sim.simulate_motion(Vec3(0, 0, 4.5), Vec3()); time.sleep(0.03)
        sim.simulate_motion(Vec3(0, 0, 1.0), Vec3())
        deadline = time.monotonic() + 5
        while not done and time.monotonic() < deadline:
            time.sleep(0.05)
        assert done, "recorder did not finish"
        session = done[0]
        assert 1.0 <= session.duration <= 2.5
        assert len(session.t[0]) > 50 and len(session.t[1]) > 50 and session.rate(0) > 40
        assert session.hits and session.hits[0]["kind"] == "don" and session.hits[0]["hand"] == 0
        assert (session.folder / "motion.npz").exists() and (session.folder / "meta.json").exists()
        assert session.meta["explanation"] == "test run" and session.meta["sources"]["camera"] is True
        assert session.camera_video is not None and len(session.camera_ts) >= 5
        frame = session.frame_at("camera", 0.5)
        assert frame is not None and frame.shape[1] == M.CAMERA_SIZE[0]
        assert session.thumbnail("camera", 0.5)
        assert (session.camera_video.stat().st_size) < 2_000_000
        # Camera callback for the tracker was restored after recording.
        assert rt.devices.camera.on_frame == rt.devices._on_frame
        assert not rt.devices.frame_taps and not rt.devices.hit_taps
        session.close()
    finally:
        rt.stop()
