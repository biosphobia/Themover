import json
import types

import pytest
from PIL import Image

from themover.ai.analyzer import GameAnalyzer, build_user_content
from themover.ai.chat import CoachChat, TOOLS
from themover.ai.client import ClaudeClient, extract_json_object, content_to_dicts
from themover.ai.prompts import system_prompt
from themover.ai.recorder import InputEvent, Recording, frame_from_image
from themover.config import Settings
from themover.mapping.profile import Profile
from themover.mapping.templates import load_template


class Block(types.SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return dict(self.__dict__)


class Usage(types.SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


def msg(blocks, stop="end_turn"):
    return types.SimpleNamespace(content=blocks, stop_reason=stop, model="claude-opus-5", usage=Usage(input_tokens=1, output_tokens=1))


class FakeStream:
    def __init__(self, m):
        self.m = m

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        for b in self.m.content:
            if b.type == "text":
                yield b.text

    def get_final_message(self):
        return self.m


class FakeClient:
    def __init__(self, script, fail_first_with=None):
        self.script = list(script)
        self.calls = []
        self.fail_first_with = fail_first_with
        self.messages = self
        self.beta = types.SimpleNamespace(messages=self)

    def stream(self, **kw):
        kw = dict(kw, messages=[dict(m) for m in kw["messages"]])
        self.calls.append(kw)
        if self.fail_first_with is not None:
            exc, self.fail_first_with = self.fail_first_with, None
            raise exc
        return FakeStream(self.script.pop(0))

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def recording():
    rec = Recording(duration=20.0, game_hint="Test Racer")
    rec.frames.append(frame_from_image(Image.new("RGB", (1400, 800), (10, 20, 30))))
    rec.events += [InputEvent(0.0, "key_down", "w"), InputEvent(5.0, "key_up", "w"), InputEvent(1.0, "key_down", "a"), InputEvent(1.2, "key_up", "a"),
                   InputEvent(2.0, "mouse_move", "", 0, 0), InputEvent(2.1, "mouse_move", "", 300, 0), InputEvent(3.0, "mouse_down", "left"), InputEvent(3.1, "mouse_up", "left")]
    return rec


def test_recording_stats_and_frames():
    rec = recording()
    st = rec.stats()
    inputs = {k["input"]: k for k in st["inputs"]}
    assert inputs["w"]["style"] == "held" and inputs["a"]["style"] == "tapped"
    assert st["mouse"]["clicks"] == {"left": 1} and st["mouse"]["path_pixels"] == 300
    assert "Test Racer" in rec.summary_text()
    assert rec.frames[0].width <= 1024
    for _ in range(30):
        rec.frames.append(rec.frames[0])
    assert len(rec.pick_frames(8)) == 8


def test_extract_json_object():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Here you go: {"a": {"b": [1, 2]}} done') == {"a": {"b": [1, 2]}}
    with pytest.raises(ValueError):
        extract_json_object("nothing here")


def test_analyzer_uses_structured_output_and_fallbacks():
    prof = load_template("driving_wheel").to_dict()
    client = FakeClient([msg([Block(type="text", text=json.dumps(prof))])])
    cc = ClaudeClient(Settings(api_key="k", model="claude-opus-5"), client=client)
    result = GameAnalyzer(cc).analyze(recording(), max_frames=4)
    assert result.profile.name == "Driving wheel" and result.problems == []
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5" and kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert set(kw["output_config"]["format"]["schema"]["properties"]) == {"analysis", "profile"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = kw["messages"][0]["content"]
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/jpeg"
    assert "INPUT SUMMARY" in content[-1]["text"]


def test_analyzer_without_fallback_uses_plain_messages():
    prof = load_template("boxing").to_dict()
    client = FakeClient([msg([Block(type="text", text=json.dumps(prof))])])
    cc = ClaudeClient(Settings(api_key="k", model="claude-sonnet-5"), client=client)
    GameAnalyzer(cc).analyze(recording())
    assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]


def test_analyzer_retries_without_structured_output_on_bad_request():
    import anthropic

    prof = load_template("platformer").to_dict()
    import httpx2 as httpx

    response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    exc = anthropic.BadRequestError("nope", response=response, body=None)
    client = FakeClient([msg([Block(type="text", text="Sure:\n```json\n" + json.dumps(prof) + "\n```")])], fail_first_with=exc)
    cc = ClaudeClient(Settings(api_key="k"), client=client)
    result = GameAnalyzer(cc).analyze(recording())
    assert result.profile.name == "Platformer"
    assert "format" not in client.calls[1]["output_config"]


def test_analyzer_drops_invalid_bindings():
    data = {"name": "x", "bindings": [{"source": "c0.trigger", "target": "key.a"}, {"source": "bad", "target": "key.b"}]}
    client = FakeClient([msg([Block(type="text", text=json.dumps(data))])])
    result = GameAnalyzer(ClaudeClient(Settings(api_key="k"), client=client)).analyze(recording())
    assert len(result.profile.bindings) == 1 and result.problems


def test_refusal_is_reported():
    client = FakeClient([msg([Block(type="text", text="")], stop="refusal")])
    with pytest.raises(RuntimeError):
        GameAnalyzer(ClaudeClient(Settings(api_key="k"), client=client)).analyze(recording())


class Host:
    def __init__(self):
        self.profile = load_template("driving_wheel")
        self.buzzes = []
        self.saved = []

    def get_profile(self):
        return self.profile

    def apply_profile(self, p):
        self.profile = p

    def live_signals(self):
        return {"c0.trigger": 0.25}

    def buzz(self, controller, rumble, led, duration_ms):
        self.buzzes.append((controller, rumble, led, duration_ms))

    def save_profile(self, name):
        self.saved.append(name)
        return f"saved {name}"


def test_chat_tool_loop_edits_profile():
    tool_turn = msg([Block(type="text", text="On it."), Block(type="tool_use", id="t1", name="modify_binding", input={"index": 0, "changes": {"input_range": [-90, 90]}}),
                     Block(type="tool_use", id="t2", name="buzz_controller", input={"controller": 1, "rumble": 0.5})], stop="tool_use")
    final = msg([Block(type="text", text="Steering softened.")])
    client = FakeClient([tool_turn, final])
    host = Host()
    chat = CoachChat(ClaudeClient(Settings(api_key="k"), client=client), host)
    texts = []
    out = chat.send("steering too sensitive", on_text=texts.append)
    assert "Steering softened." in out and texts
    assert host.profile.bindings[0].input_range == [-90.0, 90.0]
    assert host.buzzes == [(1, 0.5, None, 300)]
    # Both tool results were returned in one user message, in order.
    results = client.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and all(r["type"] == "tool_result" for r in results)
    assert client.calls[0]["tools"] is TOOLS and "tool_choice" not in client.calls[0]
    assert chat.messages[1]["content"][1]["type"] == "tool_use"


def test_chat_tools_cover_profile_operations():
    host = Host()
    chat = CoachChat(ClaudeClient(Settings(api_key="k"), client=FakeClient([])), host)
    n = len(host.profile.bindings)
    chat.execute_tool("add_bindings", {"bindings": [{"source": "c0.gesture.shake", "target": "key.r", "mode": "tap"}]})
    assert len(host.profile.bindings) == n + 1
    chat.execute_tool("remove_bindings", {"sources": ["c0.gesture.shake"]})
    assert len(host.profile.bindings) == n
    chat.execute_tool("set_feedback", {"feedback": [{"when": "c0.trigger", "controller": 0, "rumble": 1.0}]})
    assert len(host.profile.feedback) == 1
    chat.execute_tool("set_profile_meta", {"name": "Zoom", "gesture_sensitivity": 0.7, "controller_colors": [[1, 2, 3], [4, 5, 6]]})
    assert host.profile.name == "Zoom" and host.profile.controllers[1].color == [4, 5, 6]
    out = chat.execute_tool("replace_profile", {"profile": {"name": "new", "bindings": [{"source": "c0.trigger", "target": "key.a"}, {"source": "zzz", "target": "key.b"}]}})
    assert "Dropped invalid" in out and len(host.profile.bindings) == 1
    assert json.loads(chat.execute_tool("read_live_signals", {}))["c0.trigger"] == 0.25
    assert json.loads(chat.execute_tool("get_profile", {}))["bindings"][0]["index"] == 0
    assert chat.execute_tool("save_profile", {"name": "keep"}) == "saved keep" and host.saved == ["keep"]
    with pytest.raises(IndexError):
        chat.execute_tool("modify_binding", {"index": 99, "changes": {}})
    with pytest.raises(ValueError):
        chat.execute_tool("nope", {})


def test_tool_error_is_returned_not_raised():
    tool_turn = msg([Block(type="tool_use", id="t1", name="modify_binding", input={"index": 42, "changes": {}})], stop="tool_use")
    final = msg([Block(type="text", text="That index does not exist.")])
    client = FakeClient([tool_turn, final])
    chat = CoachChat(ClaudeClient(Settings(api_key="k"), client=client), Host())
    chat.send("change binding 42")
    result = client.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "42" in result["content"]


def test_system_prompt_mentions_vocabulary_and_examples():
    text = system_prompt()
    assert "c0.gesture.swing_left" in text or "cN.gesture" in text
    assert "gamepad.left_stick_x" in text and "Driving wheel" in text
    assert content_to_dicts([{"type": "text", "text": "x"}, Block(type="text", text="y")]) == [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]


def test_client_requires_key():
    with pytest.raises(RuntimeError):
        ClaudeClient(Settings(api_key="")).client


def test_analysis_and_chat_are_logged(tmp_path):
    from themover.ai.coachlog import CoachLog

    clog = CoachLog(tmp_path / "logs")
    prof = load_template("fps_pointer").to_dict()
    analysis = {"game": "Test Shooter", "genre": "FPS", "perspective": "first person", "inputs": ["w: walk"],
                "metaphor": "hold the right controller like a gun", "camera_used": False, "camera_reason": "gyro aim is enough",
                "unused_features": "no camera", "playability": "tilt to move keeps arms low"}
    thinking = Block(type="thinking", thinking="The mouse moves 80% of the time, so this is an aim game.", signature="x")
    client = FakeClient([msg([thinking, Block(type="text", text=json.dumps({"analysis": analysis, "profile": prof}))])])
    result = GameAnalyzer(ClaudeClient(Settings(api_key="k"), client=client), coach_log=clog).analyze(recording(), recording_folder="/rec/1")
    assert result.analysis["game"] == "Test Shooter" and result.profile.name == "FPS pointer"
    assert "aim game" in result.thinking and "Camera: not needed" in result.analysis_text()
    assert result.report_path.endswith(".md") and (tmp_path / "logs" / "coach_log.jsonl").exists()
    report = open(result.report_path, encoding="utf-8").read()
    assert "hold the right controller like a gun" in report and "aim game" in report and "Test Racer" in report

    # A chat turn about the same game is logged and becomes a lesson for the next analysis.
    tool_turn = msg([Block(type="tool_use", id="t1", name="modify_binding", input={"index": 0, "changes": {"scale": 0.5}})], stop="tool_use")
    final = msg([Block(type="text", text="Aim is slower now.")])
    host = Host()
    host.profile.game = "Test Racer"
    chat = CoachChat(ClaudeClient(Settings(api_key="k"), client=FakeClient([tool_turn, final])), host, coach_log=clog)
    chat.send("aim is too fast")
    entries = clog.entries()
    assert [e["kind"] for e in entries] == ["analysis", "chat"]
    assert entries[1]["tools"][0]["name"] == "modify_binding" and entries[1]["reply"] == "Aim is slower now."
    lessons = clog.lessons_for("test racer")
    assert "aim is too fast" in lessons and "modify_binding" in lessons
    assert clog.lessons_for("unknown game") == ""

    # Lessons are injected into the next analysis of that game.
    client2 = FakeClient([msg([Block(type="text", text=json.dumps({"analysis": analysis, "profile": prof}))])])
    GameAnalyzer(ClaudeClient(Settings(api_key="k"), client=client2), coach_log=clog).analyze(recording())
    assert "PAST FEEDBACK" in client2.calls[0]["messages"][0]["content"][-1]["text"]


def test_analyzer_accepts_bare_profile_json():
    prof = load_template("boxing").to_dict()
    client = FakeClient([msg([Block(type="text", text=json.dumps(prof))])])
    result = GameAnalyzer(ClaudeClient(Settings(api_key="k"), client=client)).analyze(recording())
    assert result.profile.name == "Boxing" and result.analysis == {}
