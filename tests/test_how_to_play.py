from themover.links import controller_install_html, PSMOVESERVICE_URL, VIRTUAL_DEVICE_MANAGER_URL
from themover.mapping.profile import Binding, FeedbackRule, Profile
from themover.mapping.templates import all_templates
from themover.ui.how_to_play import animation_program


def test_install_links_are_hyperlinks():
    html = controller_install_html()
    assert f'href="{PSMOVESERVICE_URL}"' in html and f'href="{VIRTUAL_DEVICE_MANAGER_URL}"' in html


def test_animation_program_from_bindings():
    right, left = animation_program(Profile(bindings=[
        Binding("wheel.angle", "gamepad.left_stick_x", mode="axis"),
        Binding("c0.trigger", "gamepad.right_trigger", mode="axis"),
        Binding("c0.gesture.swing_up", "gamepad.rb", mode="tap"),
        Binding("c1.orient.pitch", "key.w", threshold=15),
        Binding("c1.button.move", "key.e"),
    ], feedback=[FeedbackRule(rumble_from="c0.trigger", controller=0, rumble=0.4), FeedbackRule(when="c0.gesture.swing_up", controller=0, rumble=0.9, led=[255, 255, 255])]))
    assert [m.kind for m in right] == ["wheel", "trigger", "swing_up"]
    assert right[1].rumble and not right[1].flash
    assert right[2].rumble and right[2].flash
    assert [m.kind for m in left] == ["wheel", "tilt_pitch"]
    assert "Left stick" in right[0].caption and right[0].caption.startswith("Hold both like a wheel")


def test_every_template_animates_both_hands_and_uses_feedback():
    for key, p in all_templates().items():
        right, left = animation_program(p)
        assert right and left, key
        assert all(m.caption for m in right + left), key
        assert any(f.rumble or f.rumble_from for f in p.feedback), f"{key} has no rumble"
        assert any(f.led for f in p.feedback), f"{key} has no LED feedback"
        assert p.controllers[0].color != p.controllers[1].color, key
    taiko_r, taiko_l = animation_program(all_templates()["osu_taiko"])
    assert taiko_r[0].kind == "strike" and taiko_r[0].rumble and taiko_r[0].flash
    assert taiko_l[0].kind == "strike"


def test_unused_hand_gets_idle_caption():
    right, left = animation_program(Profile(bindings=[Binding("c0.trigger", "key.a"), Binding("c1.button.cross", "key.b")]))
    assert right[0].kind == "trigger"
    assert left[0].kind == "idle" and left[0].caption == "Buttons only"
