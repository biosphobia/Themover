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
from typing import Iterable, Optional

from themover.core.state import BUTTON_NAMES, MoveState, Vec3

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

LED_KEEPALIVE_SECONDS = 2.0  # the controller switches its LED off after ~4-5s of silence


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


def parse_input_report(report: bytes, model: str = "zcm1") -> Optional[RawSample]:
    """Decode a 49+ byte input report.  Returns ``None`` for foreign reports."""
    if len(report) < 45 or report[0] != REQ_GET_INPUT:
        return None
    buttons = decode_buttons(report)
    trigger = (report[5] + report[6]) // 2
    battery_raw = report[13]
    if model == "zcm2":
        accel = (_s16(report, 20), _s16(report, 22), _s16(report, 24))
        gyro = (_s16(report, 32), _s16(report, 34), _s16(report, 36))
        mag = (0, 0, 0)
        temperature = (report[38] << 4) | (report[39] >> 4)
    else:
        # Use the second (most recent) frame of each sensor.
        accel = (
            _u16(report, 20) - 0x8000,
            _u16(report, 22) - 0x8000,
            _u16(report, 24) - 0x8000,
        )
        gyro = (
            _u16(report, 32) - 0x8000,
            _u16(report, 34) - 0x8000,
            _u16(report, 36) - 0x8000,
        )
        mag = (
            _twelve_bit_signed(((report[39] & 0x0F) << 8) | report[40]),
            _twelve_bit_signed((report[41] << 4) | ((report[42] & 0xF0) >> 4)),
            _twelve_bit_signed(((report[42] & 0x0F) << 8) | report[43]),
        )
        temperature = (report[38] << 4) | ((report[39] & 0xF0) >> 4)
    timestamp = (report[12] << 8) | report[44]
    return RawSample(buttons, trigger, battery_raw, accel, gyro, mag, temperature, timestamp)


def battery_level(raw: int) -> tuple[float, bool]:
    """Return ``(level 0..1, charging)`` from the raw battery byte."""
    if raw in (0xEE, 0xEF):
        return (1.0 if raw == 0xEF else 0.8), True
    return min(max(raw, 0), 5) / 5.0, False


def build_led_report(r: int, g: int, b: int, rumble: float = 0.0) -> bytes:
    """Output report that sets the sphere colour and rumble strength."""
    clamp = lambda v: max(0, min(255, int(round(v))))  # noqa: E731
    return bytes(
        [
            REQ_SET_LEDS,
            0x00,
            clamp(r),
            clamp(g),
            clamp(b),
            0x00,
            clamp(rumble * 255.0),
            0x00,
            0x00,
        ]
    )


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
    _accel_mag_acc: float = 0.0
    _gyro_acc: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    _window: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = field(default_factory=list)

    def feed(self, accel_raw: tuple[int, int, int], gyro_raw: tuple[int, int, int]) -> None:
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
        # "Still" = accel magnitude barely changes and gyro barely changes.
        if spread < 0.05 * max(mean_mag, 1.0) and gyro_spread < 0.02 * (1.0 / self.gyro_rad_per_unit):
            self.still_samples += 1
            # Move the scale towards the observed gravity magnitude.
            self.accel_units_per_g = 0.8 * self.accel_units_per_g + 0.2 * mean_mag
            for i in range(3):
                mean_g = sum(g[i] for _, g in window) / len(window)
                self.gyro_bias[i] = 0.7 * self.gyro_bias[i] + 0.3 * mean_g

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
        """Send LED/rumble to the device (called every tick by the manager)."""
        now = time.monotonic()
        if force or now - self._last_led_write > LED_KEEPALIVE_SECONDS:
            self._write_led_report()
            self._last_led_write = now

    def _write_led_report(self) -> None:  # pragma: no cover - overridden
        pass

    def apply_outputs_now(self) -> None:
        self._write_led_report()
        self._last_led_write = time.monotonic()


class HidMoveController(MoveController):
    """A real controller reached through ``hidapi``."""

    def __init__(self, index: int, path: bytes, model: str, serial: str = "") -> None:
        self.model = model
        super().__init__(index)
        self.path = path
        self.state.model = model
        self.state.serial = serial
        self._dev = None
        self._last_sent: tuple[tuple[int, int, int], float] | None = None

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
        self.apply_outputs_now()

    def close(self) -> None:
        if self._dev is not None:
            try:
                self.set_led(0, 0, 0)
                self.set_rumble(0.0)
                self.apply_outputs_now()
                self._dev.close()
            except Exception:
                pass
        self._dev = None
        self.state.connected = False

    def poll(self) -> bool:
        if self._dev is None:
            return False
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
            sample = parse_input_report(bytes(data), self.model)
            if sample is None:
                continue
            self._apply_sample(sample)
            changed = True
        return changed

    def _apply_sample(self, sample: RawSample) -> None:
        st = self.state
        st.buttons = sample.buttons
        st.trigger = sample.trigger / 255.0
        st.battery, st.charging = battery_level(sample.battery_raw)
        self.calibration.feed(sample.accel, sample.gyro)
        st.accel, st.gyro = self.calibration.convert(sample.accel, sample.gyro)
        st.mag = Vec3(*sample.mag)
        st.touch()

    def _write_led_report(self) -> None:
        if self._dev is None:
            return
        try:
            self._dev.write(build_led_report(*self._led, self._rumble))
        except OSError:
            self.state.connected = False

    def flush_outputs(self, force: bool = False) -> None:
        key = (self._led, self._rumble)
        if self._last_sent is None or self._last_sent != key:
            self._last_sent = key
            force = True
        super().flush_outputs(force)


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
        prev = by_key.get(d.key)
        if prev is None:
            by_key[d.key] = d
        elif entry.get("usage_page", 0) in (1, 0) and prev.path != path:
            # Prefer the generic-desktop collection when several are exposed.
            by_key[d.key] = d
    found = list(by_key.values())
    # Bluetooth controllers first: those are the ones you play with.
    found.sort(key=lambda d: (d.interface != "bluetooth", d.serial, d.path))
    return found
