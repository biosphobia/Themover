from themover.mapping import vocabulary as V
from themover.mapping.humanize import describe_binding, describe_feedback, describe_source, describe_target
from themover.mapping.profile import Binding, FeedbackRule
from themover.mapping.templates import all_templates


def test_every_source_and_target_has_words():
    for s in V.all_sources():
        text = describe_source(s)
        assert text and text != s, s
    for t in V.all_targets():
        assert describe_target(t), t


def test_binding_descriptions():
    what, game, how = describe_binding(Binding("c0.gesture.swing_down", "key.j", mode="tap"))
    assert what == "Strike down with right hand" and game == "Key J" and how == "tap"
    what, game, how = describe_binding(Binding("c1.orient.pitch", "key.w", threshold=15))
    assert what == "Tilt left hand forward / back (more than 15°)" and game == "Key W" and how == "hold"
    what, game, how = describe_binding(Binding("c1.orient.roll", "key.a", threshold=-25, compare="<"))
    assert "past -25° the other way" in what
    what, game, how = describe_binding(Binding("wheel.angle", "gamepad.left_stick_x", mode="axis", invert=True))
    assert what == "Turn the wheel (both hands)" and game == "Gamepad Left stick ← →" and how == "analog, inverted"
    assert describe_binding(Binding("c0.trigger", "mouse.left", threshold=0.4))[0] == "Trigger (right hand)"
    assert describe_binding(Binding("c0.track.x", "mouse.abs_x"))[2] == "cursor"
    assert describe_binding(Binding("c0.button.move", "key.space", enabled=False))[2] == "off"


def test_feedback_descriptions():
    assert describe_feedback(FeedbackRule(when="c0.gesture.swing_any", controller=0, rumble=0.9, duration_ms=120, led=[1, 2, 3])) == \
        "Right hand: rumble 90% for 120 ms and flash the sphere when any swing of right hand"
    assert describe_feedback(FeedbackRule(rumble_from="c0.trigger", controller=0)) == "Right hand rumbles along with trigger (right hand)"


def test_templates_describe_cleanly():
    for p in all_templates().values():
        for b in p.bindings:
            what, game, how = describe_binding(b)
            assert what and game and how
