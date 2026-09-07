<p align="center"><img src="assets/icon.png" width="96" alt="The Mover"></p>

# The Mover

**but here's the mover**

Play *any* PC game with two PlayStation Move controllers and a PS3 Eye camera.
The Mover turns swings, tilts, pointing, punching and steering-wheel grips into the
keyboard, mouse and Xbox-gamepad input your game already understands, and its built-in
AI Coach (Claude) can watch you play for a minute and design a motion mapping for that game.

- **Plug and play**: one executable, a big PLAY button, sensible defaults, built-in profiles.
- **Every controller feature**: buttons, analog trigger, accelerometer, gyroscope, orientation,
  sphere colour tracking (x / y / depth) through the camera, rumble and LED feedback.
- **Any game**: output as keyboard keys (DirectInput scan-codes), mouse motion / clicks / absolute
  pointer, and a virtual Xbox 360 gamepad.
- **AI Coach**: record 45 seconds of you playing with keyboard/mouse, Claude analyses screenshots +
  input statistics and returns a full profile ("hold both controllers like a wheel…").
- **Chat box**: "steering is too twitchy", "make jump a flick up", "buzz the left controller" -
  Claude edits the live profile with tools, changes apply instantly.
- **Regular mapping too**: a full editor for bindings, curves, deadzones, thresholds, feedback rules,
  templates, import/export.

---

## 1. Quick start (Windows)

1. **Get the app**: download `TheMover.exe` from the Releases page (or build it, see below).
2. **Virtual gamepad (optional but recommended)**: install the
   [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases) so The Mover can appear
   as an Xbox 360 controller. Keyboard/mouse output works without it.
3. **Camera**: plug in the PS3 Eye. Windows has no built-in driver for it, pick one:
   - **CL-Eye Driver** (or any driver that makes it a normal webcam) - The Mover then uses it
     through the "opencv" backend, or
   - **libusb via [Zadig](https://zadig.akeo.ie/)** + `pip install git+https://github.com/bensondaled/pseyepy`
     (only when running from source) - the "pseye" backend.
   Any ordinary webcam also works; tracking only needs to see the glowing spheres.
4. **Controllers**: pair each PS Move over Bluetooth (once):
   - Open The Mover → **Devices** → plug the controller in with a mini-USB cable → **Pair via USB**
     (the PC's Bluetooth address is filled in automatically; type it if not) → unplug → press **PS**.
   - Windows shows it as *Motion Controller*. If it does not connect within ~10 s, remove it in
     Bluetooth settings and press PS again.
5. **Play**: choose a profile (Driving wheel, Sword & shield, FPS pointer, Boxing, Platformer,
   Generic gamepad, Desktop pointer), start your game, press **PLAY**. Holding the **PS button**
   on controller 1 for a second toggles Play from the couch.

Without any hardware the app still runs with simulated controllers and a synthetic camera so you
can explore the mapping editor and the AI Coach.

## 2. AI Coach (Claude)

1. **Settings** → paste your Anthropic API key (console.anthropic.com) → **Save** / **Test key**.
   The key is stored only in your user profile folder (`%APPDATA%\TheMover\settings.json`).
2. **AI Coach** → optionally type the game name and a note ("I want to swing to attack") →
   **Record** → alt-tab into the game and play normally for the countdown. The Mover takes ~1
   screenshot per second and logs which keys you hold/tap, mouse motion and clicks.
3. **Analyze & build mapping**. Claude works out the genre and controls and returns a profile.
   It becomes active immediately, is saved to your library and explained in the chat.
4. Use the **chat box** to tune anything. Claude can read the current profile, read live
   controller signals, add/modify/remove bindings, change feedback rules, colours and sensitivity,
   buzz a controller or save the profile.

Default model: `claude-opus-5` (changeable in Settings). Requests use adaptive thinking, prompt
caching for the stable system prompt, and the server-side refusal fallback, so a declined request
is automatically retried on a fallback model.

## 3. How mapping works

A **profile** is a JSON file with *bindings* and *feedback rules*.

```
source  ->  target   (mode + options)
c0.trigger            -> gamepad.right_trigger   axis, input_range [0,1]
wheel.angle           -> gamepad.left_stick_x    axis, input_range [-70,70], deadzone 0.04
c0.gesture.swing_left -> mouse.left              tap 80 ms
c1.orient.pitch       -> key.w                   hold when > 15 degrees
c0.track.x            -> mouse.abs_x             absolute pointer
```

**Sources** (`c0` right hand, `c1` left hand): `button.<name>`, `trigger`, `gesture.<swing_left|
swing_right|swing_up|swing_down|thrust|pull|flick|shake|swing_any>`, `orient.<roll|pitch|yaw>`,
`accel.<x|y|z>`, `gyro.<x|y|z>`, `track.<x|y|depth|vx|vy|tracked>`, `motion.<strength|angular_speed>`,
plus `wheel.angle` (tilt of the line between both spheres, or roll of controller 1) and
`both.<distance|center_x|center_y|depth_avg|height_diff>`.

**Targets**: `key.<a..z, 0-9, f1-f12, space, enter, esc, shift, ctrl, alt, arrows, ...>`,
`mouse.<left|right|middle|x1|x2>`, `mouse.move_x/move_y/wheel`, `mouse.abs_x/abs_y`,
`gamepad.<a|b|x|y|lb|rb|back|start|ls|rs|guide|dpad_*>`, `gamepad.<left_stick_x|left_stick_y|
right_stick_x|right_stick_y|left_trigger|right_trigger>`.

**Modes**: `hold`, `tap`, `toggle`, `repeat`, `axis`, `mouse` (relative), `absolute`, or `auto`.
Options: `threshold`, `compare`, `input_range`, `deadzone`, `scale`, `invert`, `curve`, `smoothing`,
`tap_ms`, `repeat_ms`.

**Feedback**: `{"when": "c0.gesture.swing_any", "controller": 0, "rumble": 0.9, "duration_ms": 120,
"led": [255,255,255]}` or continuous `{"rumble_from": "c0.trigger", "controller": 0}`.

Profiles live in `%APPDATA%\TheMover\profiles\*.json` and can be exported/imported from the
Mapping tab.

## 4. Devices tab

- **Sphere colours**: click a sphere in the camera picture to teach the tracker its colour, or pick
  a colour; the controller LED follows. Defaults are magenta and cyan.
- **Depth**: stand close → *Set NEAR*, stand back → *Set FAR*.
- **Re-centre yaw** after pointing both controllers at the screen.
- **Test rumble + flash** to identify each controller.
- Auto-calibration learns gyro bias and accelerometer scale whenever a controller rests still for
  a moment, so no calibration ritual is needed.

## 5. Running from source

```bash
pip install -r requirements.txt        # Windows also gets vgamepad
python -m themover                     # GUI
python -m themover --headless --list   # list profiles
python -m themover --headless --profile driving_wheel --dry-run
```

Tests: `pip install -r requirements-dev.txt && pytest` (headless CI uses `QT_QPA_PLATFORM=offscreen`).

Build the executable: `build.bat` (Windows) or `./build.sh` → `dist/TheMover.exe`.
The GitHub Actions workflow builds `TheMover.exe` on every push and attaches it to `v*` releases.

## 6. Project layout

```
themover/
  core/       state, orientation fusion, gesture detection
  devices/    PS Move HID protocol + pairing, PS3 Eye / OpenCV / synthetic camera, sphere tracker, device manager
  mapping/    vocabulary, profile schema, built-in templates, engine, runtime loop
  outputs/    Windows SendInput (scan-codes), pynput fallback, ViGEm virtual gamepad
  ai/         recorder (screen + input), Claude client, analyzer (structured output), chat coach (tool use)
  ui/         PySide6 app: Play, Mapping, AI Coach, Devices, Settings
  profiles/   user profile library
tests/        59 unit tests (protocol, tracker, motion, engine, runtime, AI with a fake client, GUI smoke)
```

## 7. Notes and known limits

- Bluetooth on Windows: the Microsoft stack sometimes refuses the first PS Move connection. If
  pairing via USB does not lead to a connection, install the controller through Bluetooth settings
  once, or use a controller already paired by other tools; The Mover only needs it to show up as an
  HID device. Wired USB also works for testing (no motion sphere tracking difference).
- The PS4-era Move (CECH-ZCM2) is supported for buttons and IMU; its magnetometer is absent.
- Gyro scale is approximate until a controller has rested still once (auto-calibration); the
  orientation used for tilt-to-move comes from gravity and is exact.
- Virtual gamepad output requires ViGEmBus; without it gamepad targets are ignored and keyboard/mouse
  targets still work.
