import json

from themover.mapping import vocabulary as V
from themover.mapping.profile import Binding, FeedbackRule, Profile, profile_json_schema
from themover.mapping.templates import TEMPLATES, all_templates
from themover.profiles import list_profiles, load_profile, save_profile, delete_profile


def test_vocabulary_consistency():
    for s in V.all_sources():
        assert V.is_valid_source(s), s
    for t in V.all_targets():
        assert V.is_valid_target(t), t
    assert not V.is_valid_source("c2.trigger")
    assert not V.is_valid_source("c0.button.nope")
    assert not V.is_valid_target("key.nope")
    assert V.target_kind("gamepad.left_stick_x") == "axis"
    assert V.target_kind("mouse.left") == "button"
    assert V.target_kind("none") == "none"
    text = V.vocabulary_text()
    assert "wheel.angle" in text and "gamepad." in text


def test_templates_are_valid():
    for key, p in all_templates().items():
        assert p.validate() == [], key
        assert p.bindings and p.play_style


def test_roundtrip_and_validation():
    p = Profile(name="t", bindings=[Binding("c0.trigger", "gamepad.right_trigger", mode="axis", input_range=[0, 1])], feedback=[FeedbackRule(when="c0.trigger", rumble=0.5)])
    q = Profile.from_json(p.to_json())
    assert q.to_dict() == p.to_dict()
    bad = Profile.from_dict({"bindings": [{"source": "c0.bogus", "target": "key.a"}, {"source": "c0.trigger", "target": "key.a", "input_range": [1, 1]}], "feedback": [{"when": "x.y", "controller": 3}]})
    problems = bad.validate()
    assert len(problems) >= 3
    assert bad.sanitized().bindings == [] and bad.sanitized().feedback == []


def test_from_dict_tolerates_missing_fields():
    p = Profile.from_dict({"name": "x", "controllers": [{"color": [1, 2]}], "bindings": [{"source": "c0.button.move", "target": "key.space"}]})
    assert len(p.controllers) == 2 and p.controllers[0].color == [1, 2, 0]
    assert p.bindings[0].effective_mode() == "hold"
    assert Binding("c0.trigger", "gamepad.right_trigger").effective_mode() == "axis"
    assert Binding("c0.track.x", "mouse.move_x").effective_mode() == "mouse"
    assert Binding("c0.track.x", "mouse.abs_x").effective_mode() == "absolute"


def test_schema_is_strict_json():
    schema = profile_json_schema()
    json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"].keys())
    b = schema["properties"]["bindings"]["items"]
    assert set(b["required"]) == set(b["properties"].keys())


def test_profile_library_roundtrip():
    keys = list(list_profiles().keys())
    assert set(TEMPLATES).issubset(keys)
    p = load_profile("driving_wheel")
    p.name = "My Racer"
    path = save_profile(p)
    assert path.exists()
    lib = list_profiles()
    assert "user:my_racer" in lib and lib["user:my_racer"].name == "My Racer"
    assert load_profile("user:my_racer").bindings
    assert delete_profile("user:my_racer") and "user:my_racer" not in list_profiles()
    assert not delete_profile("driving_wheel")
