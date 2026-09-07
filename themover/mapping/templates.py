"""Built-in mapping templates.  Also shown to Claude as worked examples."""
from __future__ import annotations

from themover.mapping.profile import Profile

MAGENTA = [255, 0, 255]
CYAN = [0, 255, 255]


def _b(source: str, target: str, **kw) -> dict:
    d = {"source": source, "target": target}
    d.update(kw)
    return d


def _fb(**kw) -> dict:
    return kw


TEMPLATES: dict[str, dict] = {
    "generic_gamepad": {
        "name": "Generic gamepad",
        "game": "Any controller game",
        "description": "Both controllers act as one Xbox pad. Tilt the right controller to steer with the left stick, the left controller for the right stick / camera.",
        "play_style": "Hold one controller in each hand like a broken-in-half gamepad. Tilt the right-hand controller to move, the left-hand controller to look. Trigger = RT/LT, Move button = A, face buttons map to the pad's face buttons.",
        "controllers": [{"color": MAGENTA, "role": "right hand"}, {"color": CYAN, "role": "left hand"}],
        "bindings": [
            _b("c1.orient.roll", "gamepad.left_stick_x", mode="axis", input_range=[-45, 45], deadzone=0.12, curve="squared", comment="tilt left hand sideways to move"),
            _b("c1.orient.pitch", "gamepad.left_stick_y", mode="axis", input_range=[-45, 45], deadzone=0.12, curve="squared", comment="tilt left hand forward/back"),
            _b("c0.orient.roll", "gamepad.right_stick_x", mode="axis", input_range=[-45, 45], deadzone=0.12, curve="squared", comment="tilt right hand to look"),
            _b("c0.orient.pitch", "gamepad.right_stick_y", mode="axis", input_range=[-45, 45], deadzone=0.12, curve="squared"),
            _b("c0.trigger", "gamepad.right_trigger", mode="axis", input_range=[0, 1]),
            _b("c1.trigger", "gamepad.left_trigger", mode="axis", input_range=[0, 1]),
            _b("c0.button.move", "gamepad.a"), _b("c0.button.cross", "gamepad.a"), _b("c0.button.circle", "gamepad.b"),
            _b("c0.button.square", "gamepad.x"), _b("c0.button.triangle", "gamepad.y"),
            _b("c1.button.move", "gamepad.lb"), _b("c0.button.select", "gamepad.back"), _b("c0.button.start", "gamepad.start"),
            _b("c1.button.triangle", "gamepad.dpad_up"), _b("c1.button.cross", "gamepad.dpad_down"),
            _b("c1.button.square", "gamepad.dpad_left"), _b("c1.button.circle", "gamepad.dpad_right"),
            _b("c1.button.start", "gamepad.rb"), _b("c0.gesture.thrust", "gamepad.rs", mode="tap"),
        ],
        "feedback": [_fb(when="c0.trigger", controller=0, rumble=0.4, duration_ms=80, threshold=0.6)],
    },
    "driving_wheel": {
        "name": "Driving wheel",
        "game": "Racing / driving games",
        "description": "Hold both controllers like a steering wheel. The camera measures the tilt between the two spheres; trigger = accelerate, left trigger = brake.",
        "play_style": "Grip one controller in each hand at 9 and 3 o'clock and turn them together like a wheel. Squeeze the right trigger to accelerate and the left trigger to brake/reverse. Press Move on the right hand for handbrake, flick either controller up to shift up, down to shift down.",
        "controllers": [{"color": MAGENTA, "role": "right hand"}, {"color": CYAN, "role": "left hand"}],
        "bindings": [
            _b("wheel.angle", "gamepad.left_stick_x", mode="axis", input_range=[-70, 70], deadzone=0.04, curve="linear", smoothing=0.3, comment="steering"),
            _b("c0.trigger", "gamepad.right_trigger", mode="axis", input_range=[0, 1], comment="throttle"),
            _b("c1.trigger", "gamepad.left_trigger", mode="axis", input_range=[0, 1], comment="brake"),
            _b("c0.button.move", "gamepad.a", comment="handbrake"),
            _b("c0.gesture.swing_up", "gamepad.rb", mode="tap", comment="shift up"),
            _b("c0.gesture.swing_down", "gamepad.lb", mode="tap", comment="shift down"),
            _b("c1.button.move", "gamepad.x", comment="nitro / horn"),
            _b("c0.button.triangle", "gamepad.y", comment="change camera"),
            _b("c0.button.circle", "gamepad.b"), _b("c0.button.start", "gamepad.start"), _b("c0.button.select", "gamepad.back"),
            _b("c1.button.triangle", "gamepad.dpad_up"), _b("c1.button.cross", "gamepad.dpad_down"),
            _b("c1.button.square", "gamepad.dpad_left"), _b("c1.button.circle", "gamepad.dpad_right"),
        ],
        "feedback": [
            _fb(rumble_from="c0.trigger", controller=0, rumble=0.35, comment="engine buzz grows with throttle"),
            _fb(when="c1.trigger", controller=1, rumble=0.6, duration_ms=120, threshold=0.7, comment="brake thump"),
        ],
    },
    "sword_and_shield": {
        "name": "Sword & shield",
        "game": "Melee games (Chivalry, Mordhau, Souls-likes)",
        "description": "Swing the right controller to attack in that direction, raise the left controller to block, point with the right hand to look around.",
        "play_style": "Right hand is your sword: swing sideways for slashes, thrust forward to stab, flick up for an overhead. Left hand is your shield: hold the left trigger to block, punch forward to shield-bash. Tilt the left controller to walk, twist the right controller to look around. Move button = interact, trigger = alternate attack.",
        "controllers": [{"color": MAGENTA, "role": "right hand (sword)"}, {"color": CYAN, "role": "left hand (shield)"}],
        "bindings": [
            _b("c0.gesture.swing_left", "mouse.left", mode="tap", tap_ms=80, comment="slash"),
            _b("c0.gesture.swing_right", "mouse.left", mode="tap", tap_ms=80, comment="slash"),
            _b("c0.gesture.swing_up", "mouse.x1", mode="tap", tap_ms=80, comment="overhead"),
            _b("c0.gesture.thrust", "mouse.middle", mode="tap", tap_ms=80, comment="stab"),
            _b("c1.trigger", "mouse.right", threshold=0.4, comment="block"),
            _b("c1.gesture.thrust", "key.f", mode="tap", comment="shield bash / kick"),
            _b("c0.orient.yaw", "mouse.move_x", mode="mouse", input_range=[-60, 60], deadzone=0.08, scale=1.0, comment="twist right hand to look"),
            _b("c0.orient.pitch", "mouse.move_y", mode="mouse", input_range=[-60, 60], deadzone=0.15, scale=0.6),
            _b("c1.orient.pitch", "key.w", mode="hold", threshold=15, comment="tilt left hand forward to walk"),
            _b("c1.orient.pitch", "key.s", mode="hold", threshold=-25, compare="<"),
            _b("c1.orient.roll", "key.a", mode="hold", threshold=-25, compare="<"),
            _b("c1.orient.roll", "key.d", mode="hold", threshold=25),
            _b("c1.button.move", "key.lshift", comment="sprint"),
            _b("c0.button.move", "key.e", comment="interact"),
            _b("c0.button.cross", "key.space", comment="jump"),
            _b("c0.button.circle", "key.lctrl", comment="crouch"),
            _b("c0.button.square", "key.r"), _b("c0.button.triangle", "key.q"),
            _b("c0.button.start", "key.esc"), _b("c0.button.select", "key.tab"),
        ],
        "feedback": [
            _fb(when="c0.gesture.swing_any", controller=0, rumble=0.9, duration_ms=120, led=[255, 255, 255], led_duration_ms=100, comment="hit feedback"),
            _fb(when="c1.trigger", controller=1, rumble=0.3, duration_ms=100, threshold=0.4, led=[0, 120, 255], led_duration_ms=300),
        ],
        "gesture_sensitivity": 1.0,
    },
    "fps_pointer": {
        "name": "FPS pointer",
        "game": "First-person shooters",
        "description": "Aim by pointing the right controller at the screen (camera tracking + gyro), move with the left controller tilt.",
        "play_style": "Point the right controller at the screen like a light gun: the camera follows the glowing sphere for aiming and the gyro adds fine motion. Trigger = fire, Move = aim down sights. Tilt the left controller to move, squeeze its trigger to sprint, flick it up to jump. Shake the right controller to reload.",
        "controllers": [{"color": MAGENTA, "role": "right hand (gun)"}, {"color": CYAN, "role": "left hand (movement)"}],
        "bindings": [
            _b("c0.gyro.z", "mouse.move_x", mode="mouse", input_range=[-300, 300], deadzone=0.02, scale=1.2, invert=True, comment="gyro aim"),
            _b("c0.gyro.x", "mouse.move_y", mode="mouse", input_range=[-300, 300], deadzone=0.02, scale=1.0, invert=True),
            _b("c0.track.vx", "mouse.move_x", mode="mouse", input_range=[-2, 2], deadzone=0.03, scale=0.8, comment="camera tracking assists aim"),
            _b("c0.track.vy", "mouse.move_y", mode="mouse", input_range=[-2, 2], deadzone=0.03, scale=0.8),
            _b("c0.trigger", "mouse.left", threshold=0.35, comment="fire"),
            _b("c0.button.move", "mouse.right", comment="aim down sights"),
            _b("c0.gesture.shake", "key.r", mode="tap", comment="reload"),
            _b("c0.button.cross", "key.space", comment="jump"), _b("c1.gesture.swing_up", "key.space", mode="tap"),
            _b("c0.button.circle", "key.lctrl", comment="crouch"), _b("c0.button.square", "key.f"), _b("c0.button.triangle", "key.g", comment="grenade"),
            _b("c1.orient.pitch", "key.w", threshold=15), _b("c1.orient.pitch", "key.s", threshold=-25, compare="<"),
            _b("c1.orient.roll", "key.a", threshold=-25, compare="<"), _b("c1.orient.roll", "key.d", threshold=25),
            _b("c1.trigger", "key.lshift", threshold=0.5, comment="sprint"),
            _b("c1.button.move", "key.e", comment="interact"), _b("c1.button.square", "key.1"), _b("c1.button.triangle", "key.2"),
            _b("c1.button.circle", "key.3"), _b("c1.button.cross", "key.4"),
            _b("c0.button.start", "key.esc"), _b("c0.button.select", "key.tab"),
        ],
        "feedback": [
            _fb(when="c0.trigger", controller=0, rumble=0.8, duration_ms=60, threshold=0.35, led=[255, 200, 0], led_duration_ms=60, comment="muzzle kick"),
            _fb(when="c1.trigger", controller=1, rumble=0.2, duration_ms=100, threshold=0.5),
        ],
    },
    "boxing": {
        "name": "Boxing",
        "game": "Fighting / boxing games",
        "description": "Punch with either controller. Left hand jabs, right hand crosses, upward flicks are uppercuts. Lean by moving both spheres in front of the camera.",
        "play_style": "Stand facing the camera with a controller in each fist. Punch forward for straights, swing sideways for hooks and flick upward for uppercuts. Raise both hands to guard. Step left/right (the camera sees your spheres move) to dodge.",
        "controllers": [{"color": MAGENTA, "role": "right fist"}, {"color": CYAN, "role": "left fist"}],
        "bindings": [
            _b("c1.gesture.thrust", "gamepad.x", mode="tap", comment="left jab"),
            _b("c0.gesture.thrust", "gamepad.y", mode="tap", comment="right straight"),
            _b("c1.gesture.swing_right", "gamepad.a", mode="tap", comment="left hook"),
            _b("c0.gesture.swing_left", "gamepad.b", mode="tap", comment="right hook"),
            _b("c1.gesture.swing_up", "gamepad.lb", mode="tap", comment="left uppercut"),
            _b("c0.gesture.swing_up", "gamepad.rb", mode="tap", comment="right uppercut"),
            _b("both.center_y", "gamepad.left_trigger", mode="hold", threshold=0.35, comment="hands high = guard"),
            _b("both.center_x", "gamepad.left_stick_x", mode="axis", input_range=[-0.6, 0.6], deadzone=0.25, curve="squared", comment="lean / step sideways"),
            _b("both.depth_avg", "gamepad.left_stick_y", mode="axis", input_range=[0.2, 0.8], deadzone=0.3, comment="step in / out"),
            _b("c0.button.move", "gamepad.right_trigger", comment="special"),
            _b("c0.button.start", "gamepad.start"), _b("c0.button.select", "gamepad.back"),
        ],
        "feedback": [
            _fb(when="c0.gesture.swing_any", controller=0, rumble=1.0, duration_ms=150, led=[255, 255, 255], led_duration_ms=120),
            _fb(when="c1.gesture.swing_any", controller=1, rumble=1.0, duration_ms=150, led=[255, 255, 255], led_duration_ms=120),
        ],
        "gesture_sensitivity": 0.9,
    },
    "platformer": {
        "name": "Platformer",
        "game": "2D / 3D platformers",
        "description": "Tilt to run, flick up to jump, punch to attack. Simple and forgiving.",
        "play_style": "Hold the right controller like a remote. Tilt it left/right to run, flick it upward to jump (a bigger flick jumps higher via a longer press), thrust forward to attack. Left controller is optional: tilt it to move the camera.",
        "controllers": [{"color": MAGENTA, "role": "right hand"}, {"color": CYAN, "role": "left hand (optional)"}],
        "bindings": [
            _b("c0.orient.roll", "gamepad.left_stick_x", mode="axis", input_range=[-40, 40], deadzone=0.15, curve="linear", comment="tilt to run"),
            _b("c0.gesture.swing_up", "gamepad.a", mode="tap", tap_ms=180, comment="flick up = jump"),
            _b("c0.button.move", "gamepad.a", comment="jump button too"),
            _b("c0.gesture.thrust", "gamepad.x", mode="tap", comment="attack"),
            _b("c0.trigger", "gamepad.right_trigger", mode="axis", input_range=[0, 1], comment="run / dash"),
            _b("c0.button.cross", "gamepad.b"), _b("c0.button.square", "gamepad.x"), _b("c0.button.triangle", "gamepad.y"),
            _b("c1.orient.roll", "gamepad.right_stick_x", mode="axis", input_range=[-45, 45], deadzone=0.2),
            _b("c1.orient.pitch", "gamepad.right_stick_y", mode="axis", input_range=[-45, 45], deadzone=0.2),
            _b("c0.button.start", "gamepad.start"), _b("c0.button.select", "gamepad.back"),
        ],
        "feedback": [_fb(when="c0.gesture.swing_up", controller=0, rumble=0.5, duration_ms=100)],
    },
    "desktop_pointer": {
        "name": "Desktop pointer",
        "game": "Windows desktop / menus",
        "description": "Point the right controller at the screen to move the cursor; trigger = left click, Move = right click.",
        "play_style": "Point at the screen. The camera puts the cursor where your sphere is. Trigger clicks, Move right-clicks, tilt the left controller to scroll.",
        "controllers": [{"color": MAGENTA, "role": "pointer"}, {"color": CYAN, "role": "scroll"}],
        "bindings": [
            _b("c0.track.x", "mouse.abs_x", mode="absolute", input_range=[-0.7, 0.7], smoothing=0.5),
            _b("c0.track.y", "mouse.abs_y", mode="absolute", input_range=[-0.6, 0.6], smoothing=0.5),
            _b("c0.trigger", "mouse.left", threshold=0.4),
            _b("c0.button.move", "mouse.right"),
            _b("c1.orient.pitch", "mouse.wheel", mode="mouse", input_range=[-45, 45], deadzone=0.3, scale=0.4),
            _b("c0.button.cross", "key.enter", mode="tap"), _b("c0.button.circle", "key.esc", mode="tap"),
        ],
        "feedback": [_fb(when="c0.trigger", controller=0, rumble=0.3, duration_ms=50, threshold=0.4)],
    },
}


def template_names() -> list[str]:
    return list(TEMPLATES.keys())


def load_template(key: str) -> Profile:
    data = TEMPLATES[key]
    return Profile.from_dict(data)


def all_templates() -> dict[str, Profile]:
    return {k: load_template(k) for k in TEMPLATES}


def templates_as_examples(max_examples: int = 3) -> str:
    """Compact JSON of a few templates for Claude's prompt."""
    import json

    keys = ["driving_wheel", "sword_and_shield", "fps_pointer"][:max_examples]
    parts = []
    for k in keys:
        p = load_template(k)
        parts.append(json.dumps(p.to_dict(), separators=(",", ":")))
    return "\n".join(parts)
