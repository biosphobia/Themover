"""PlayStation Move controller support over Bluetooth / USB HID.

Pair the controllers with PSMoveServiceEx (or any other tool) first; The Mover
only *reads* controllers that Windows already lists as HID devices.  The wire
protocol follows the community documentation (psmoveapi).  Parsing is kept in
pure functions so it is unit-testable; :class:`HidMoveController` wraps a real
``hidapi`` device and :class:`SimulatedMove` provides a stand-in when no
hardware is connected.
"""
from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass, field
from typing import Optional

from themover.core.state import BUTTON_NAMES, MoveState, Vec3

import logging
import sys
import threading
from typing import Callable


def _is_windows() -> bool:
    return sys.platform.startswith("win")

log = logging.getLogger(__name__)

FrameCallback = Callable[[Vec3, Vec3, float, float, bool], None]  # accel, gyro, t, trigger, move

try:  # hidapi is optional so the app can start without it (simulated mode).
    import hid  # type: ignore
except Exception:  # pragma: no cover - depends on the machine
    hid = None  # type: ignore

PSMOVE_VID = 0x054C
PSMOVE_PID_ZCM1 = 0x03D5
PSMOVE_PID_ZCM2 = 0x0C5E
PSMOVE_PIDS = {PSMOVE_PID_ZCM1: "zcm1", PSMOVE_PID_ZCM2: "zcm2"}

REQ_GET_INPUT = 0x01
REQ_SET_LEDS = 0x02
REQ_GET_CALIBRATION = 0x10

# Button bit layout of the 32-bit word built by :func:`button_word`.
BTN_TRIANGLE = 1 << 4
BTN_CIRCLE = 1 << 5
BTN_CROSS = 1 << 6
BTN_SQUARE = 1 << 7
BTN_SELECT = 1 << 8
BTN_START = 1 << 11
BTN_PS = 1 << 16
BTN_MOVE = 1 << 19
BTN_T = 1 << 20

BUTTON_BITS = {
    "triangle": BTN_TRIANGLE,
    "circle": BTN_CIRCLE,
    "cross": BTN_CROSS,
    "square": BTN_SQUARE,
    "select": BTN_SELECT,
    "start": BTN_START,
    "ps": BTN_PS,
    "move": BTN_MOVE,
    "trigger_click": BTN_T,
}

# Approximate raw sensor scales.  They are refined at runtime by
# :class:`AutoCalibration` (gravity magnitude for the accelerometer, rest bias for
# the gyroscope) so the exact values matter little.
DEFAULT_ACCEL_UNITS_PER_G = {"zcm1": 4300.0, "zcm2": 4096.0}
DEFAULT_GYRO_RAD_PER_UNIT = {"zcm1": 8.378 / 7000.0, "zcm2": math.radians(1.0 / 16.4)}

LED_REPORT_SIZE = 49  # psmoveapi / PSMoveService send the full-size report
LED_KEEPALIVE_SECONDS = 2.0  # the controller switches its LED off after ~4-5s of silence
LED_MIN_WRITE_INTERVAL = 0.12  # Bluetooth stacks drop (or worse, disconnect on) faster output reports


# --------------------------------------------------------------------------- #
# Pure protocol helpers
# --------------------------------------------------------------------------- #
def button_word(report: bytes) -> int:
    """Combine the four button bytes of an input report into one integer."""
    if len(report) < 5:
        return 0
    b1, b2, b3, b4 = report[1], report[2], report[3], report[4]
    return b2 | (b1 << 8) | ((b3 & 0x01) << 16) | ((b4 & 0xF0) << 13)


def decode_buttons(report: bytes) -> dict[str, bool]:
    word = button_word(report)
    return {name: bool(word & BUTTON_BITS.get(name, 0)) for name in BUTTON_NAMES}


def _u16(report: bytes, offset: int) -> int:
    return report[offset] | (report[offset + 1] << 8)


def _s16(report: bytes, offset: int) -> int:
    return struct.unpack_from("<h", report, offset)[0]


def _twelve_bit_signed(value: int) -> int:
    value &= 0xFFF
    return value - 0x1000 if value & 0x800 else value


@dataclass
class RawSample:
    buttons: dict[str, bool]
    trigger: int  # 0..255
    battery_raw: int
    accel: tuple[int, int, int]
    gyro: tuple[int, int, int]
    mag: tuple[int, int, int]
    temperature: int
    timestamp: int


# Byte layout of the 49-byte input report (psmoveapi / PSMoveService PSMove_Data_Input):
#  0 type  1-4 buttons  5 trigger  6 trigger2  7-10 unknown  11 timehigh  12 battery
#  13-18 accel frame 1  19-24 accel frame 2  25-30 gyro frame 1  31-36 gyro frame 2
#  37 temphigh  38 templow/mXhigh  39 mXlow  40 mYhigh  41 mYlow/mZhigh  42 mZlow  43 timelow
OFF_TIMEHIGH, OFF_BATTERY, OFF_ACCEL, OFF_GYRO, OFF_TEMP, OFF_TIMELOW = 11, 12, 13, 25, 37, 43


def _imu_frame(report: bytes, model: str, frame: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Accelerometer + gyroscope of frame 0 (older) or 1 (newer) of a report."""
    a_off = OFF_ACCEL + 6 * frame
    g_off = OFF_GYRO + 6 * frame
    if model == "zcm2":
        accel = (_s16(report, a_off), _s16(report, a_off + 2), _s16(report, a_off + 4))
        gyro = (_s16(report, g_off), _s16(report, g_off + 2), _s16(report, g_off + 4))
    else:
        accel = (_u16(report, a_off) - 0x8000, _u16(report, a_off + 2) - 0x8000, _u16(report, a_off + 4) - 0x8000)
        gyro = (_u16(report, g_off) - 0x8000, _u16(report, g_off + 2) - 0x8000, _u16(report, g_off + 4) - 0x8000)
    return accel, gyro


def parse_input_frames(report: bytes, model: str = "zcm1") -> list[RawSample]:
    """Both IMU frames of a report, oldest first (each report carries two ~5.7 ms apart)."""
    latest = parse_input_report(report, model)
    if latest is None:
        return []
    accel0, gyro0 = _imu_frame(report, model, 0)
    older = RawSample(latest.buttons, latest.trigger, latest.battery_raw, accel0, gyro0, latest.mag, latest.temperature, latest.timestamp)
    return [older, latest]


def parse_input_report(report: bytes, model: str = "zcm1") -> Optional[RawSample]:
    """Decode a 49+ byte input report (newest IMU frame).  ``None`` for foreign reports."""
    if len(report) < 44 or report[0] != REQ_GET_INPUT:
        return None
    buttons = decode_buttons(report)
    trigger = (report[5] + report[6]) // 2
    battery_raw = report[OFF_BATTERY]
    accel, gyro = _imu_frame(report, model, 1)
    t = OFF_TEMP
    if model == "zcm2":
        mag = (0, 0, 0)
        temperature = (report[t] << 4) | (report[t + 1] >> 4)
    else:
        mag = (
            _twelve_bit_signed(((report[t + 1] & 0x0F) << 8) | report[t + 2]),
            _twelve_bit_signed((report[t + 3] << 4) | ((report[t + 4] & 0xF0) >> 4)),
            _twelve_bit_signed(((report[t + 4] & 0x0F) << 8) | report[t + 5]),
        )
        temperature = (report[t] << 4) | ((report[t + 1] & 0xF0) >> 4)
    timestamp = (report[OFF_TIMEHIGH] << 8) | report[OFF_TIMELOW]
    return RawSample(buttons, trigger, battery_raw, accel, gyro, mag, temperature, timestamp)


def battery_level(raw: int) -> tuple[float, bool]:
    """Return ``(level 0..1, charging)`` from the raw battery byte."""
    if raw in (0xEE, 0xEF):
        return (1.0 if raw == 0xEF else 0.8), True
    return min(max(raw, 0), 5) / 5.0, False


def build_led_report(r: int, g: int, b: int, rumble: float = 0.0) -> bytes:
    """Output report that sets the sphere colour and rumble strength.

    Layout: type 0x02, zero, r, g, b, rumble2 (0), rumble, then zero padding up
    to the 49-byte report size the controller expects.
    """
    clamp = lambda v: max(0, min(255, int(round(v))))  # noqa: E731
    head = bytes([REQ_SET_LEDS, 0x00, clamp(r), clamp(g), clamp(b), 0x00, clamp(rumble * 255.0)])
    return head + bytes(LED_REPORT_SIZE - len(head))


# --------------------------------------------------------------------------- #
# Runtime auto-calibration
# --------------------------------------------------------------------------- #
@dataclass
class AutoCalibration:
    """Learn sensor scale/bias from the controller sitting still.

    * accelerometer: the magnitude at rest must be exactly 1 g;
    * gyroscope: the reading at rest must be zero.
    """

    accel_units_per_g: float
    gyro_rad_per_unit: float
    gyro_bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    still_samples: int = 0
    scale_updates: int = 0  # how many times the gyro scale was refined from gravity
    _accel_mag_acc: float = 0.0
    _gyro_acc: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    _window: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = field(default_factory=list)
    _prev_dir: tuple[float, float, float] | None = None
    _theta_sum: float = 0.0
    _omega_sum: float = 0.0
    _default_gyro_scale: float = 0.0

    def feed(self, accel_raw: tuple[int, int, int], gyro_raw: tuple[int, int, int], dt: float = 1.0 / 85.0) -> None:
        if not self._default_gyro_scale:
            self._default_gyro_scale = self.gyro_rad_per_unit
        self._learn_gyro_scale(accel_raw, gyro_raw, dt)
        self._window.append((accel_raw, gyro_raw))
        if len(self._window) < 40:
            return
        window = self._window
        self._window = []
        mags = [math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2) for a, _ in window]
        mean_mag = sum(mags) / len(mags)
        spread = max(mags) - min(mags)
        gyro_spread = max(
            max(g[i] for _, g in window) - min(g[i] for _, g in window) for i in range(3)
        )
        # The gravity direction must not move either (a steady slow rotation keeps
        # |a| and the gyro constant but is not "still").
        first = window[0][0]
        fm = mags[0] or 1.0
        dir_spread = 0.0
        for (a, _), m in zip(window, mags):
            cos = (a[0] * first[0] + a[1] * first[1] + a[2] * first[2]) / ((m or 1.0) * fm)
            dir_spread = max(dir_spread, math.acos(max(-1.0, min(1.0, cos))))
        # "Still" = accel magnitude, accel direction and gyro all barely change.
        if spread < 0.05 * max(mean_mag, 1.0) and gyro_spread < 0.02 * (1.0 / self.gyro_rad_per_unit) and dir_spread < math.radians(1.5):
            self.still_samples += 1
            # Move the scale towards the observed gravity magnitude.
            self.accel_units_per_g = 0.8 * self.accel_units_per_g + 0.2 * mean_mag
            for i in range(3):
                mean_g = sum(g[i] for _, g in window) / len(window)
                self.gyro_bias[i] = 0.7 * self.gyro_bias[i] + 0.3 * mean_g

    def _learn_gyro_scale(self, accel_raw: tuple[int, int, int], gyro_raw: tuple[int, int, int], dt: float) -> None:
        """Refine the gyro scale: while the controller is turned slowly the gravity
        direction rotates by exactly the angular speed perpendicular to gravity."""
        s = self.accel_units_per_g or 1.0
        ax, ay, az = accel_raw
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if not (0.8 * s <= mag <= 1.2 * s):
            self._prev_dir = None
            return
        d = (ax / mag, ay / mag, az / mag)
        prev = self._prev_dir
        self._prev_dir = d
        if prev is None:
            return
        cos = max(-1.0, min(1.0, d[0] * prev[0] + d[1] * prev[1] + d[2] * prev[2]))
        theta = math.acos(cos)  # radians the gravity direction moved this sample
        g = [gyro_raw[i] - self.gyro_bias[i] for i in range(3)]
        along = g[0] * d[0] + g[1] * d[1] + g[2] * d[2]
        perp = [g[i] - along * d[i] for i in range(3)]
        omega = math.sqrt(perp[0] ** 2 + perp[1] ** 2 + perp[2] ** 2) * dt  # raw-units * s
        if 0.004 < theta < 0.35 and omega > 0:
            self._theta_sum += theta
            self._omega_sum += omega
        if self._theta_sum >= 1.2:  # ~70 degrees of slow rotation observed
            est = self._theta_sum / self._omega_sum
            lo, hi = self._default_gyro_scale * 0.2, self._default_gyro_scale * 5.0
            est = max(lo, min(hi, est))
            self.gyro_rad_per_unit = 0.6 * self.gyro_rad_per_unit + 0.4 * est
            self.scale_updates += 1
            self._theta_sum = self._omega_sum = 0.0

    def convert(self, accel_raw: tuple[int, int, int], gyro_raw: tuple[int, int, int]) -> tuple[Vec3, Vec3]:
        s = self.accel_units_per_g or 1.0
        accel = Vec3(accel_raw[0] / s, accel_raw[1] / s, accel_raw[2] / s)
        gyro = Vec3(
            (gyro_raw[0] - self.gyro_bias[0]) * self.gyro_rad_per_unit,
            (gyro_raw[1] - self.gyro_bias[1]) * self.gyro_rad_per_unit,
            (gyro_raw[2] - self.gyro_bias[2]) * self.gyro_rad_per_unit,
        )
        return accel, gyro


# --------------------------------------------------------------------------- #
# Controller implementations
# --------------------------------------------------------------------------- #
class MoveController:
    """Abstract controller.  Subclasses fill ``state`` in :meth:`poll`."""

    model = "simulated"

    def __init__(self, index: int) -> None:
        self.state = MoveState(index=index, model=self.model)
        self.calibration = AutoCalibration(
            DEFAULT_ACCEL_UNITS_PER_G.get(self.model, 4096.0),
            DEFAULT_GYRO_RAD_PER_UNIT.get(self.model, 0.001),
        )
        self._led = (0, 0, 0)
        self._rumble = 0.0
        self._last_led_write = 0.0

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        self.state.connected = True

    def close(self) -> None:
        self.state.connected = False

    # -- I/O -----------------------------------------------------------------
    def poll(self) -> bool:
        """Read pending reports; return True if the state changed."""
        return False

    def set_led(self, r: int, g: int, b: int) -> None:
        self._led = (int(r), int(g), int(b))
        self.state.led = self._led

    def set_rumble(self, strength: float) -> None:
        self._rumble = max(0.0, min(1.0, float(strength)))
        self.state.rumble = self._rumble

    def flush_outputs(self, force: bool = False) -> None:
        """Send LED/rumble to the device when needed (called every tick)."""

    def apply_outputs_now(self) -> None:
        """Send the current LED/rumble state right away (rate limit permitting)."""


class HidMoveController(MoveController):
    """A real controller reached through ``hidapi``.

    Output reports (LED colour + rumble) are scheduled rather than written
    immediately:

    * at most one write every :data:`LED_MIN_WRITE_INTERVAL` (faster writes are
      dropped by Bluetooth stacks and can even disconnect the controller);
    * a rumble pulse that starts and ends between two writes is *latched* so it
      still reaches the motor for one interval;
    * a keep-alive write every :data:`LED_KEEPALIVE_SECONDS` stops the sphere
      from switching itself off;
    * write results are checked - failures are logged and shown in the UI - and
      on Windows a failed ``hid_write`` switches to the control-pipe method.
    """

    def __init__(self, index: int, path: bytes, model: str, serial: str = "", led_method: str = "auto", alt_paths: Optional[list] = None) -> None:
        self.model = model
        super().__init__(index)
        self.path = path
        self.alt_paths = [p for p in (alt_paths or []) if p != path]
        self._alt_devs: list = []  # extra hid handles opened for output on other collections
        self._alt_tried = False
        self.write_results: dict[str, list[int]] = {}  # method -> [ok, failed]
        self.state.model = model
        self.state.serial = serial
        self._dev = None
        self._control = None  # ControlPipeWriter when the fallback is active
        self.led_method = led_method  # auto | write | control
        self._last_sent: tuple[tuple[int, int, int], float] | None = None
        self._last_write = 0.0
        self._rumble_latched = 0.0
        self.writes_ok = 0
        self.writes_failed = 0
        self.output_error = ""
        self._reader: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self.on_frame: Optional[FrameCallback] = None
        self.reports = 0
        self.report_rate = 0.0
        self._last_report_t = 0.0

    @property
    def key(self) -> str:
        return self.state.serial or self.path.decode("utf-8", "replace")

    def open(self) -> None:
        if hid is None:
            raise RuntimeError("hidapi is not installed")
        dev = hid.device()
        dev.open_path(self.path)
        dev.set_nonblocking(1)
        self._dev = dev
        self.state.connected = True
        if self.led_method == "control":
            self._enable_control_pipe()


    # -- input ---------------------------------------------------------------
    def poll(self) -> bool:
        if self._dev is None or self._reader is not None:
            return False  # the reader thread applies samples as they arrive
        changed = False
        # Drain everything queued so we always act on the freshest sample.
        for _ in range(8):
            try:
                data = self._dev.read(64)
            except OSError:
                self.state.connected = False
                return changed
            if not data:
                break
            changed |= self._handle_report(bytes(data))
        return changed

    def _handle_report(self, report: bytes) -> bool:
        frames = parse_input_frames(report, self.model)
        if not frames:
            return False
        now = time.monotonic()
        if self._last_report_t:
            dt = now - self._last_report_t
            if 0 < dt < 1.0:
                self.report_rate = 0.95 * self.report_rate + 0.05 * (1.0 / dt)
        self._last_report_t = now
        self.reports += 1
        latest = frames[-1]
        self._apply_sample(latest)
        if self.on_frame is not None:
            # Both IMU frames, oldest first; the older one is ~5.7 ms before "now".
            trig = latest.trigger / 255.0
            move = bool(latest.buttons.get("move"))
            for i, fr in enumerate(frames):
                accel, gyro = self.calibration.convert(fr.accel, fr.gyro)
                t = now - (0.0057 if i == 0 else 0.0)
                try:
                    self.on_frame(accel, gyro, t, trig, move)
                except Exception as exc:  # never let a callback kill the reader
                    log.debug("on_frame failed: %s", exc)
        return True

    # -- low-latency reader thread ------------------------------------------
    def start_reader(self) -> None:
        """Read reports on a dedicated thread as soon as they arrive (rhythm games)."""
        if self._reader is not None or self._dev is None:
            return
        self._reader_stop.clear()
        self._reader = threading.Thread(target=self._reader_loop, name=f"psmove-{self.state.index + 1}", daemon=True)
        self._reader.start()

    def stop_reader(self) -> None:
        if self._reader is None:
            return
        self._reader_stop.set()
        self._reader.join(timeout=1.0)
        self._reader = None

    def _reader_loop(self) -> None:
        while not self._reader_stop.is_set() and self._dev is not None:
            try:
                data = self._dev.read(64, 20)  # blocks up to 20 ms
            except (OSError, ValueError):
                self.state.connected = False
                break
            if data:
                self._handle_report(bytes(data))

    def _apply_sample(self, sample: RawSample) -> None:
        st = self.state
        st.buttons = sample.buttons
        st.trigger = sample.trigger / 255.0
        st.battery, st.charging = battery_level(sample.battery_raw)
        self.calibration.feed(sample.accel, sample.gyro, 1.0 / self.report_rate if self.report_rate > 20 else 1.0 / 85.0)
        st.accel, st.gyro = self.calibration.convert(sample.accel, sample.gyro)
        st.mag = Vec3(*sample.mag)
        st.touch()

    # -- output --------------------------------------------------------------
    def set_rumble(self, strength: float) -> None:
        super().set_rumble(strength)
        if self._rumble > 0.0:
            # Remember the pulse even if it ends before the next write slot.
            self._rumble_latched = max(self._rumble_latched, self._rumble)

    def flush_outputs(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < LED_MIN_WRITE_INTERVAL:
            return
        rumble = max(self._rumble, self._rumble_latched)
        desired = (self._led, rumble)
        keepalive_due = now - self._last_write >= LED_KEEPALIVE_SECONDS
        if force or desired != self._last_sent or keepalive_due:
            self._write_now(force)

    def apply_outputs_now(self) -> None:
        self.flush_outputs(force=True)

    def _write_now(self, force: bool = False) -> None:
        if self._dev is None:
            return
        rumble = max(self._rumble, self._rumble_latched)
        report = build_led_report(*self._led, rumble)
        result = self._send_report(report)
        self._last_write = time.monotonic()
        self._rumble_latched = 0.0
        if result < 0:
            self.writes_failed += 1
            self._last_sent = None  # retry on the next slot
            if self.writes_failed in (1, 10, 100):
                log.warning("controller %s: LED/rumble write failed (%s); method=%s", self.state.index + 1, self.output_error, self.led_method)
        else:
            self.writes_ok += 1
            self._last_sent = (self._led, rumble)
            self.output_error = ""
        self.state.output_status = self.output_status()

    def _count(self, method: str, ok: bool) -> None:
        r = self.write_results.setdefault(method, [0, 0])
        r[0 if ok else 1] += 1

    def _hid_write(self, dev, report: bytes, label: str) -> int:
        try:
            result = dev.write(report)
            if result is None:
                result = len(report)
        except Exception as exc:
            self.output_error = f"{label}: {exc}"
            result = -1
        if result < 0:
            try:
                err = dev.error()
            except Exception:
                err = ""
            self.output_error = f"{label} failed ({err or 'no detail'})"
        self._count(label, result >= 0)
        return result

    def _send_report(self, report: bytes) -> int:
        """Deliver an output report by every mechanism that could work.

        Windows Bluetooth stacks differ in whether an output report must go
        through WriteFile (``hid_write``, interrupt pipe) or through
        ``HidD_SetOutputReport`` (control pipe), and some expose the output
        report on a second HID collection.  In ``auto`` mode we send through
        all of them; duplicates are harmless and rate-limited anyway.
        """
        results: list[int] = []
        if self.led_method in ("auto", "write"):
            results.append(self._hid_write(self._dev, report, "hid_write"))
            if self.led_method == "auto" and (results[-1] < 0 or _is_windows()):
                self._open_alt_devices()
                for dev, label in self._alt_devs:
                    results.append(self._hid_write(dev, report, label))
        if self.led_method in ("auto", "control"):
            if self._control is not None or self._enable_control_pipe():
                try:
                    r = self._control.write(report)
                except Exception as exc:
                    self.output_error = f"control pipe: {exc}"
                    r = -1
                self._count("control", r >= 0)
                results.append(r)
        if not results:
            return -1
        return max(results)

    def _open_alt_devices(self) -> None:
        if self._alt_tried or hid is None:
            return
        self._alt_tried = True
        for path in self.alt_paths:
            try:
                dev = hid.device()
                dev.open_path(path)
                dev.set_nonblocking(1)
                self._alt_devs.append((dev, f"hid_write[{path.decode('utf-8', 'replace')[-12:]}]"))
            except Exception as exc:
                log.debug("alt path %r not opened: %s", path, exc)

    _control_failed: bool = False

    def _enable_control_pipe(self) -> bool:
        if self._control is not None:
            return True
        if self._control_failed:
            return False
        try:
            from themover.devices.winhid import ControlPipeWriter

            self._control = ControlPipeWriter(self.path)
            return True
        except Exception as exc:
            self._control_failed = True
            log.debug("control pipe unavailable: %s", exc)
            if self.led_method == "control":
                self.output_error = f"control pipe unavailable: {exc}"
            return False

    def close(self) -> None:
        self.stop_reader()
        if self._dev is not None:
            try:
                self.set_led(0, 0, 0)
                self.set_rumble(0.0)
                self._write_now(force=True)
                self._dev.close()
            except Exception:
                pass
        for dev, _label in self._alt_devs:
            try:
                dev.close()
            except Exception:
                pass
        self._alt_devs = []
        if self._control is not None:
            self._control.close()
            self._control = None
        self._dev = None
        self.state.connected = False

    def output_status(self) -> str:
        methods = ", ".join(f"{m} {ok}/{ok + bad}" for m, (ok, bad) in self.write_results.items()) or "no method"
        if self.writes_failed and not self.writes_ok:
            return f"LED/rumble NOT working: {self.output_error} [{methods}]"
        if self.writes_failed:
            return f"LED/rumble partly ok ({self.writes_failed} failed) [{methods}]"
        if self.writes_ok:
            return f"LED/rumble sent [{methods}]"
        return "LED/rumble: nothing written yet"


class SimulatedMove(MoveController):
    """A fake controller with a gentle idle motion.

    The GUI can also drive it directly (``simulate_button`` etc.) so the mapping
    engine can be exercised without any hardware.
    """

    model = "simulated"

    def __init__(self, index: int) -> None:
        super().__init__(index)
        self.state.connected = True
        self.state.battery = 1.0
        self._t0 = time.monotonic()
        self.animate = True

    def poll(self) -> bool:
        st = self.state
        t = time.monotonic() - self._t0
        if self.animate:
            # Slow wobble so the UI shows something alive.
            st.accel = Vec3(0.05 * math.sin(t), 0.05 * math.cos(t * 0.7), 1.0)
            st.gyro = Vec3(0.0, 0.0, 0.0)
        st.touch()
        return True

    def simulate_button(self, name: str, pressed: bool) -> None:
        self.state.buttons[name] = pressed

    def simulate_trigger(self, value: float) -> None:
        self.state.trigger = max(0.0, min(1.0, value))

    def simulate_motion(self, accel: Vec3, gyro: Vec3) -> None:
        self.animate = False
        self.state.accel = accel
        self.state.gyro = gyro


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@dataclass
class DiscoveredMove:
    path: bytes
    model: str
    serial: str
    interface: str  # "bluetooth" | "usb"
    paths: list = field(default_factory=list)  # every HID collection path for this controller
    usages: list = field(default_factory=list)  # (usage_page, usage) per path, for diagnostics

    @property
    def key(self) -> str:
        """Stable identity: the Bluetooth address when known, else the HID path."""
        return self.serial or self.path.decode("utf-8", "replace")

    @property
    def label(self) -> str:
        name = {"zcm1": "PS Move", "zcm2": "PS Move (PS4 model)"}.get(self.model, self.model)
        return f"{name} {self.serial or '(no serial)'} via {self.interface}"


def _normalise_serial(serial: str) -> str:
    serial = (serial or "").strip().lower()
    return serial.replace("-", ":")


def enumerate_controllers(entries=None) -> list[DiscoveredMove]:
    """List every PS Move the OS knows about, one entry per physical controller.

    Windows can report the same controller through several HID interfaces or
    collections; they are collapsed by serial number (Bluetooth address).
    """
    if entries is None:
        if hid is None:
            return []
        try:
            entries = hid.enumerate(PSMOVE_VID, 0)
        except Exception:
            return []
    by_key: dict[str, DiscoveredMove] = {}
    for entry in entries:
        pid = entry.get("product_id")
        if entry.get("vendor_id", PSMOVE_VID) != PSMOVE_VID or pid not in PSMOVE_PIDS:
            continue
        path = entry.get("path") or b""
        if isinstance(path, str):
            path = path.encode()
        serial = _normalise_serial(entry.get("serial_number") or "")
        interface = "bluetooth" if ":" in serial else "usb"
        d = DiscoveredMove(path=path, model=PSMOVE_PIDS[pid], serial=serial, interface=interface)
        usage = (entry.get("usage_page", 0), entry.get("usage", 0))
        prev = by_key.get(d.key)
        if prev is None:
            d.paths, d.usages = [path], [usage]
            by_key[d.key] = d
        else:
            if path not in prev.paths:
                prev.paths.append(path)
                prev.usages.append(usage)
            if usage[0] in (1, 0) and prev.path != path:
                # Prefer the generic-desktop collection when several are exposed.
                prev.path = path
    found = list(by_key.values())
    # Bluetooth controllers first: those are the ones you play with.
    found.sort(key=lambda d: (d.interface != "bluetooth", d.serial, d.path))
    return found


def hid_diagnostics(controllers: Optional[list] = None) -> str:
    """Everything useful for a bug report about controller I/O."""
    lines = []
    try:
        lines.append(f"hidapi {getattr(hid, '__version__', '?')} on {sys.platform}")
    except Exception:
        lines.append("hidapi missing")
    for d in enumerate_controllers():
        lines.append(f"{d.label}")
        for path, usage in zip(d.paths, d.usages):
            mark = "*" if path == d.path else " "
            lines.append(f"  {mark} usage_page=0x{usage[0]:04x} usage=0x{usage[1]:04x} path={path.decode('utf-8', 'replace')}")
    for c in controllers or []:
        if not isinstance(c, HidMoveController):
            continue
        st = c.state
        cal = c.calibration
        lines.append(
            f"slot {st.index + 1}: {c.output_status()} · reports={c.reports} @ {c.report_rate:.0f} Hz · "
            f"accel=({st.accel.x:+.2f},{st.accel.y:+.2f},{st.accel.z:+.2f}) g gyro=({math.degrees(st.gyro.x):+.0f},{math.degrees(st.gyro.y):+.0f},{math.degrees(st.gyro.z):+.0f}) deg/s · "
            f"cal: accel {cal.accel_units_per_g:.0f}/g, gyro {cal.gyro_rad_per_unit:.6f} rad/unit ({cal.scale_updates} refinements), bias {[round(b, 1) for b in cal.gyro_bias]}, still {cal.still_samples}"
        )
    return "\n".join(lines)
