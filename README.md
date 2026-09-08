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
- **Simple by default, Advanced on demand**: the app shows plain-English mappings ("Strike down with
  right hand → Key J") and a readiness checklist; the **Advanced** switch in the header reveals raw
  signal names, every numeric option and all device/engine settings for pro users.

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
4. **Controllers**: pair each PS Move with
   [PSMoveServiceEx](https://github.com/Timocop/PSMoveServiceEx) (its config tool has a USB pairing
   step that writes your PC's Bluetooth address into the controller). Once Windows lists a controller
   as *Motion Controller*, The Mover detects it by itself. The Mover does **not** pair controllers.
   - Close PSMoveService itself while playing; The Mover talks to the controllers directly and the
     two would fight over the LEDs and rumble.
   - The **Devices** tab lists every controller found, which slot it is in (1 = right hand,
     2 = left hand), and offers **Swap 1 ↔ 2** and **Identify** (buzz + flash). Slots are remembered
     by Bluetooth address, so each controller keeps its slot across restarts. Controllers can be
     switched on or off at any time; The Mover rescans every 3 seconds.
5. **Play**: choose a profile (Driving wheel, Sword & shield, FPS pointer, Boxing, Platformer,
   osu! taiko drums, Generic gamepad, Desktop pointer), start your game, press **PLAY**. Holding
   the **PS button** on controller 1 for a second toggles Play from the couch. The *Ready to play?*
   checklist on the Play tab tells you exactly what is still missing for the chosen profile.

Without any hardware the app still runs with simulated controllers and a synthetic camera so you
can explore the mapping editor and the AI Coach.

## 2. AI Coach (Claude)

1. **Setup** → paste your Anthropic API key (console.anthropic.com) → **Save & test**.
   The key is stored only in your user profile folder (`%APPDATA%\TheMover\settings.json`).
2. **AI Coach** → optionally type the game name and a note ("I want to swing to attack") →
   **Record** → alt-tab into the game and play normally for the countdown. The Mover takes ~1
   screenshot per second and logs which keys you hold/tap, mouse motion and clicks.
3. **Analyze & build mapping**. Claude works out the genre and controls and returns a profile.
   It becomes active immediately, is saved to your library and explained in the chat.
4. Use the **chat box** to tune anything. Claude can read the current profile, read live
   controller signals, add/modify/remove bindings, change feedback rules, colours and sensitivity,
   buzz a controller or save the profile.

How the coach thinks: it first works out what the game is and what every input does (held keys =
movement, taps = actions, continuous mouse = aim), then commits to one physical metaphor themed to
that game (a steering wheel for racing, holding the right controller like a gun with a flick to
reload for shooters, sword and shield for melee, drumsticks for rhythm games) and keeps it playable:
essentials on tilt, triggers and buttons, gestures for the satisfying occasional actions. It uses
only the features that add fun, and the camera only where it clearly helps (light-gun pointing,
two-hand wheel, boxing lean). Its structured analysis (game, genre, inputs, metaphor, why the
camera was or was not used, playability concerns) is shown after each build.

### Coach logs

Every analysis and chat turn is written to `%APPDATA%\TheMover\coach_logs\`:

- `coach_log.jsonl`: one JSON line per event with the recording summary, the structured analysis,
  the resulting profile, tool calls made during chat, Claude's summarised reasoning, model, token
  usage and timing.
- `analysis-<timestamp>.md`: a readable report per build (what the coach saw, its analysis and
  reasoning summary, the play style, the bindings).
- Recordings themselves (screenshots + input events) stay in `recordings\<timestamp>\`.

Past chat feedback about the same game is fed back into the next analysis of that game, so
"aim is too fast" said once is remembered next time you rebuild the controls.

Default model: `claude-opus-5` (changeable under Advanced in Setup). Requests use adaptive thinking, prompt
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

### osu! taiko (rhythm games): how hits are detected

Rhythm games are a special case because timing accuracy and latency decide everything. A typical
Oni chart runs 170 BPM 1/4 streams (a note every 88 ms, 176 ms per hand when alternating) with the
colour switching don/kat inside the stream, and at OD 5 a GREAT is only ±35 ms wide. The generic
gesture path (threshold on a smoothed magnitude, evaluated on the engine tick) is not good enough
for that, so drum hits use their own pipeline:

1. **Every IMU frame is used.** Each Bluetooth report carries two accelerometer frames (~5.7 ms
   apart); both are decoded, not just the newest one.
2. **A reader thread per controller.** While a rhythm profile is active, each controller gets a
   dedicated thread that blocks on the HID read and processes a report the moment it arrives,
   instead of waiting for the next engine tick.
3. **The hit is the stop of the stroke.** An air-drum stroke is an acceleration lobe in the
   direction of motion, a short cruise, then a sharp spike in the *opposite* direction as the arm
   stops. That stop is what you feel as the hit and is by far the sharpest feature, so the hit fires
   on the first frame of the stop lobe. Because the spike is so steep, soft and hard strokes fire at
   the same phase (jitter is one frame, not "whenever the swing crossed a threshold").
4. **Don or kat comes from the stroke direction relative to gravity.** Straight down (within 40°)
   is don, angled outward or sideways is kat, upward strokes (the rebound) never fire. Holding the
   trigger forces kat and holding Move forces don, for players who prefer a modifier. This works in
   any grip because gravity is measured, not assumed.
5. **No double hits, no hijacked strokes.** An onset must last two consecutive frames in a
   consistent direction, so the single-frame blip of a rebound cannot start a false stroke, and a
   sustained acceleration in a new direction re-arms the detector so a don→kat switch mid-stream
   registers with the correct colour. A 45 ms refractory period follows every hit.
6. **Keys are pressed on the reader thread.** `cN.hit.don/kat/any` bindings in tap mode bypass the
   mapping tick entirely: the key goes down inside the hit callback and a timer releases it 30 ms
   later. The rest of the profile (menus, rumble) still runs on the normal engine.

Simulating a full 1448-note Oni chart (1510 strokes including big notes) through the detector gives
every stroke detected, no extra hits, no wrong colours and a timing error of ±2.9 ms. On real
hardware the remaining fixed delay is the Bluetooth transport plus half a report (roughly 10 to 25 ms)
and the jitter is about one report period; run osu!'s **offset wizard** once so the constant part
is absorbed by the game's offset. The controller cards on the Play tab show the hit count, the last
hit and the report rate (about 85 Hz per controller over Bluetooth) so you can check a controller
before a map.

Preset keys: right hand J (don) / K (kat), left hand F (don) / D (kat), Cross = Enter, Circle = Esc,
Triangle / Square scroll the song list, F2 random, ` quick retry. Rumble thumps on every hit.
Tune with the coach ("hits register too easily" lowers sensitivity) or, in Advanced, edit the
bindings directly.

## 4. Setup tab

Simple view: controller status with **Identify** (buzz + flash) and **Swap 1 ↔ 2**, the Claude API
key, and the camera picture (click a sphere to teach the tracker its colour, or pick a colour; the
controller LED follows).

Advanced view adds: rescan / forget slot assignment, controller backend, **LED / rumble method**,
re-centre yaw, model / effort / refusal fallback, recording length and screenshot rate, keyboard
backend, virtual gamepad on/off, engine rate, camera source / index / mirror and depth calibration
(*Set NEAR* / *Set FAR*).

Auto-calibration learns gyro bias and accelerometer scale whenever a controller rests still for a
moment, so no calibration ritual is needed.

### LED and rumble

Output reports are sent exactly like psmoveapi / PSMoveService do (49-byte report 0x02), at most
every 120 ms (Bluetooth stacks drop faster writes and can even disconnect the controller), with a
keep-alive every 2 s so the sphere stays lit. Short rumble pulses are latched so they always reach
the motor. Each controller's write health is shown in Setup ("LED/rumble ok [hid_write]"). If it
says *NOT working*:

1. Make sure PSMoveService is closed (it overrides colours and rumble).
2. Switch *LED / rumble method* (Advanced) to `control`, which sends reports through the Windows
   HID control pipe instead of the interrupt pipe. `auto` does this by itself when a write fails.
3. Check `%APPDATA%\TheMover\themover.log` for the exact error.

### Updates without rebuilding the .exe

`TheMover.exe` is a launcher: it bundles Python and every dependency plus a copy of the app code,
and on start it prefers a newer copy of the `themover` package in `%APPDATA%\TheMover\app\` if one
is there. The **Updates** card in Setup fills that folder straight from GitHub:

- **Check for updates** downloads the branch archive (`codeload.github.com/…/zip/refs/heads/<branch>`,
  about 200 KB) and reads the commit SHA GitHub stores in the zip comment, so no API token and no
  rate limit are involved. The app also checks once on start (Advanced → toggle).
- **Update & restart** unpacks `themover/` into the app folder (atomic swap), writes `version.json`,
  releases the controllers and relaunches. The next start runs the new code.
- Before installing, the updater reads the archive's `requirements.txt` and refuses if it needs a
  package the executable does not contain (that is the one case where a new .exe is required).
- If downloaded code fails to import, the launcher moves it to `themover.broken` and starts the
  built-in copy, so a bad push can never brick the app. **Remove downloaded update** (Advanced)
  goes back to the built-in copy manually.
- The branch defaults to the one the executable was built from (stamped by CI into `_build.json`);
  Advanced lets you point it at another branch or repo, and add a token for private repos.

Running from source, the card only reports whether the branch moved; use `git pull` there.

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
  core/       state, orientation fusion, gesture detection, drum-hit detection (rhythm games)
  devices/    PS Move HID protocol, controller discovery + stable slots, PS3 Eye / OpenCV / synthetic camera, sphere tracker
  mapping/    vocabulary, profile schema, built-in templates, engine, runtime loop
  outputs/    Windows SendInput (scan-codes), pynput fallback, ViGEm virtual gamepad
  ai/         recorder (screen + input), Claude client, analyzer (structured analysis + profile), chat coach (tool use), coach log
  ui/         PySide6 app: Play (checklist), Mapping (plain-English / advanced), AI Coach, Setup; Advanced switch
  profiles/   user profile library
tests/        90 unit tests (protocol + LED writer + reader thread, discovery/slots, tracker, motion, drum hits, engine + fast path, runtime, humanizer, AI + coach log with a fake client, GUI smoke)
```

## 7. Notes and known limits

- Bluetooth pairing is done by PSMoveServiceEx (or psmoveapi's `psmove pair`), not by The Mover;
  The Mover only needs the controller to show up as a Bluetooth HID device. A controller plugged in
  over USB is detected too, but Bluetooth ones take the slots first.
- The PS4-era Move (CECH-ZCM2) is supported for buttons and IMU; its magnetometer is absent.
- Gyro scale is approximate until a controller has rested still once (auto-calibration); the
  orientation used for tilt-to-move comes from gravity and is exact.
- Virtual gamepad output requires ViGEmBus; without it gamepad targets are ignored and keyboard/mouse
  targets still work.
- The LED/rumble control-pipe fallback and the write diagnostics were written against the Windows
  HID API documentation, not exercised on real hardware in CI; the Setup tab reports what happens.
