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


def test_profile_library_seeds_templates_and_edits_persist():
    from themover.profiles import create_from_template, resolve_key, restore_template, seed_templates

    lib = list_profiles()  # first call seeds the built-in templates as ordinary profiles
    assert set(f"user:{k}" for k in TEMPLATES).issubset(lib)
    assert lib["user:driving_wheel"].based_on == "driving_wheel"
    assert list(lib)[: len(TEMPLATES)] == [f"user:{k}" for k in TEMPLATES]  # templates first, in order
    # Old-style keys still resolve.
    assert resolve_key("driving_wheel") == "user:driving_wheel" and resolve_key("user:x") == "user:x"
    # Editing a seeded template is saved in place, like any profile.
    p = load_profile("driving_wheel")
    p.name = "My Racer"
    p.bindings.pop()
    assert save_profile(p, "user:driving_wheel") == "user:driving_wheel"
    again = load_profile("user:driving_wheel")
    assert again.name == "My Racer" and len(again.bindings) == len(TEMPLATES["driving_wheel"]["bindings"]) - 1
    # Reset brings the pristine template back.
    assert restore_template("user:driving_wheel") == "user:driving_wheel"
    assert load_profile("user:driving_wheel").name == "Driving wheel"
    # New from template never overwrites; names get a suffix.
    key, prof = create_from_template("boxing")
    assert key == "user:boxing_2" and prof.name == "Boxing 2" and prof.based_on == "boxing"
    key2, _ = create_from_template("boxing", name="Ring Night")
    assert key2 == "user:ring_night"
    # Save without a key creates a new unique file; deleting a seeded template stays deleted.
    fresh = Profile(name="Ring Night", bindings=[Binding("c0.trigger", "key.a")])
    assert save_profile(fresh) == "user:ring_night_2"
    assert delete_profile("user:platformer") and "user:platformer" not in list_profiles()
    assert seed_templates() == []  # already seeded: nothing comes back on its own
    assert "user:platformer" in seed_templates(force=True) and "user:platformer" in list_profiles()
    assert not delete_profile("user:does_not_exist")
