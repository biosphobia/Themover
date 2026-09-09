from themover.devices import psmove as P
from themover.core.state import Vec3


def make_report(buttons1=0, buttons2=0, buttons3=0, buttons4=0, trigger=0, battery=5, accel=(0, 0, 0), gyro=(0, 0, 0), mag=(0, 0, 0), model="zcm1", accel_frame1=None):
    """Build a report from the psmoveapi PSMove_Data_Input layout (independent of the parser)."""
    rep = bytearray(49)
    rep[0] = 0x01
    rep[1], rep[2], rep[3], rep[4] = buttons1, buttons2, buttons3, buttons4
    rep[5] = rep[6] = trigger
    # 7..10 unknown, 11 timehigh
    rep[12] = battery

    def put16(off, v):
        if model == "zcm1":
            v = v + 0x8000
        v &= 0xFFFF
        rep[off] = v & 0xFF
        rep[off + 1] = (v >> 8) & 0xFF

    f1 = accel_frame1 or accel
    for i, v in enumerate(f1):
        put16(13 + 2 * i, v)  # accel frame 1
    for i, v in enumerate(accel):
        put16(19 + 2 * i, v)  # accel frame 2
    for i, v in enumerate(gyro):
        put16(25 + 2 * i, v)  # gyro frame 1
        put16(31 + 2 * i, v)  # gyro frame 2
    mx, my, mz = [m & 0xFFF for m in mag]
    rep[37] = 0  # temphigh
    rep[38] = (rep[38] & 0xF0) | ((mx >> 8) & 0x0F)  # templow / mXhigh
    rep[39] = mx & 0xFF
    rep[40] = (my >> 4) & 0xFF
    rep[41] = ((my & 0x0F) << 4) | ((mz >> 8) & 0x0F)
    rep[42] = mz & 0xFF
    rep[43] = 0  # timelow
    return bytes(rep)


def test_layout_matches_reference_offsets():
    """Guard against off-by-one: fields sit exactly where psmoveapi puts them."""
    from themover.devices import psmove as P

    assert (P.OFF_BATTERY, P.OFF_ACCEL, P.OFF_GYRO, P.OFF_TEMP, P.OFF_TIMELOW) == (12, 13, 25, 37, 43)
    rep = bytearray(49)
    rep[0] = 0x01
    rep[12] = 0xEE  # battery byte
    for off in (13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35):
        rep[off:off + 2] = (0x8000).to_bytes(2, "little")  # zero in ZCM1 encoding
    rep[19:21] = (4300 + 0x8000).to_bytes(2, "little")  # accel X, frame 2
    rep[31:33] = (0x8000 - 500).to_bytes(2, "little")  # gyro X, frame 2
    smp = P.parse_input_report(bytes(rep))
    assert smp.battery_raw == 0xEE and smp.accel == (4300, 0, 0) and smp.gyro == (-500, 0, 0)


def test_button_decoding():
    rep = make_report(buttons2=P.BTN_TRIANGLE | P.BTN_SQUARE, buttons1=(P.BTN_START >> 8), buttons3=0x01, buttons4=0x40 | 0x80)
    s = P.parse_input_report(rep)
    assert s is not None
    b = s.buttons
    assert b["triangle"] and b["square"] and b["start"] and b["ps"] and b["move"] and b["trigger_click"]
    assert not b["cross"] and not b["circle"] and not b["select"]


def test_sensor_decoding_zcm1():
    rep = make_report(trigger=200, accel=(100, -4300, 12), gyro=(-7, 8, 9), mag=(-5, 7, -2047))
    s = P.parse_input_report(rep, "zcm1")
    assert s.trigger == 200
    assert s.accel == (100, -4300, 12)
    assert s.gyro == (-7, 8, 9)
    assert s.mag == (-5, 7, -2047)


def test_sensor_decoding_zcm2():
    rep = make_report(accel=(-1000, 4096, 5), gyro=(300, -300, 0), model="zcm2")
    s = P.parse_input_report(rep, "zcm2")
    assert s.accel == (-1000, 4096, 5)
    assert s.gyro == (300, -300, 0)


def test_battery_levels():
    assert P.battery_level(5) == (1.0, False)
    assert P.battery_level(0) == (0.0, False)
    assert P.battery_level(0xEE)[1] is True
    assert P.battery_level(0xEF) == (1.0, True)


def test_foreign_report_is_ignored():
    assert P.parse_input_report(b"\x02" + bytes(48)) is None
    assert P.parse_input_report(b"\x01\x00") is None


def test_led_report_layout():
    rep = P.build_led_report(255, 0, 128, 0.5)
    assert rep[0] == P.REQ_SET_LEDS and rep[1] == 0
    assert rep[2:5] == bytes([255, 0, 128])
    assert rep[6] == 128 and len(rep) == P.LED_REPORT_SIZE == 49
    assert rep[7:] == bytes(42)  # zero padding, like psmoveapi / PSMoveService
    assert P.build_led_report(300, -5, 0, 2.0)[2:5] == bytes([255, 0, 0])


def test_auto_calibration_learns_scale_and_bias():
    cal = P.AutoCalibration(accel_units_per_g=4300.0, gyro_rad_per_unit=0.001)
    for _ in range(1200):
        cal.feed((0, 4000, 0), (50, -20, 5))
    assert cal.still_samples > 0
    assert abs(cal.accel_units_per_g - 4000) < 60
    accel, gyro = cal.convert((0, 4000, 0), (50, -20, 5))
    assert abs(accel.y - 1.0) < 0.02
    assert abs(gyro.x) < 0.01 and abs(gyro.y) < 0.01


def test_simulated_controller():
    sim = P.SimulatedMove(0)
    sim.simulate_button("move", True)
    sim.simulate_trigger(2.0)
    sim.simulate_motion(Vec3(0, 1, 0), Vec3(0, 0, 0))
    assert sim.poll()
    assert sim.state.buttons["move"] and sim.state.trigger == 1.0
    assert sim.state.accel.y == 1.0


class _Dev:
    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    def open_path(self, p): pass
    def set_nonblocking(self, v): pass
    def read(self, n): return []
    def close(self): pass
    def error(self): return "boom" if self.fail else ""

    def write(self, data):
        self.writes.append(bytes(data))
        return -1 if self.fail else len(data)


def _controller(monkeypatch, dev):
    class H:
        @staticmethod
        def device():
            return dev
    monkeypatch.setattr(P, "hid", H)
    c = P.HidMoveController(0, b"path", "zcm1", "aa:bb", led_method="write")
    c.open()
    return c


def test_led_writer_rate_limits_and_keeps_alive(monkeypatch):
    dev = _Dev()
    c = _controller(monkeypatch, dev)
    c.set_led(255, 0, 0)
    c.apply_outputs_now()
    assert len(dev.writes) == 1 and dev.writes[0][2:5] == bytes([255, 0, 0]) and len(dev.writes[0]) == 49
    c.set_led(0, 255, 0)
    c.flush_outputs()  # too soon after the last write: deferred
    assert len(dev.writes) == 1
    c._last_write -= P.LED_MIN_WRITE_INTERVAL
    c.flush_outputs()
    assert len(dev.writes) == 2 and dev.writes[1][2:5] == bytes([0, 255, 0])
    c._last_write -= P.LED_MIN_WRITE_INTERVAL
    c.flush_outputs()  # nothing changed, not yet keep-alive time
    assert len(dev.writes) == 2
    c._last_write -= P.LED_KEEPALIVE_SECONDS
    c.flush_outputs()
    assert len(dev.writes) == 3
    assert c.state.output_status.startswith("LED/rumble sent") and c.write_results["hid_write:plain"] == [3, 0]


def test_short_rumble_pulse_is_latched(monkeypatch):
    dev = _Dev()
    c = _controller(monkeypatch, dev)
    c.apply_outputs_now()
    c.set_rumble(1.0)
    c.set_rumble(0.0)  # pulse over before the next write slot
    c._last_write -= P.LED_MIN_WRITE_INTERVAL
    c.flush_outputs()
    assert dev.writes[-1][6] == 255  # the motor still got the pulse
    c._last_write -= P.LED_MIN_WRITE_INTERVAL
    c.flush_outputs()
    assert dev.writes[-1][6] == 0  # ...and is switched off at the next slot


def test_failed_writes_are_reported_and_retried(monkeypatch):
    dev = _Dev(fail=True)
    c = _controller(monkeypatch, dev)
    c.set_led(1, 2, 3)
    c.apply_outputs_now()
    assert c.writes_failed == 1 and "NOT working" in c.state.output_status and "boom" in c.output_error
    c._last_write -= P.LED_MIN_WRITE_INTERVAL
    c.flush_outputs()  # retried even though the desired state did not change
    assert len(dev.writes) == 2
    assert c.state.connected  # a failed LED write must not drop the controller


def test_both_imu_frames_are_parsed_oldest_first():
    rep = make_report(accel=(10, 20, 30), gyro=(1, 2, 3), accel_frame1=(100, 200, 300))
    frames = P.parse_input_frames(rep)
    assert [f.accel for f in frames] == [(100, 200, 300), (10, 20, 30)]
    assert frames[1].gyro == (1, 2, 3)
    assert P.parse_input_frames(b"\x07" + bytes(48)) == []


def test_reader_thread_delivers_frames(monkeypatch):
    import threading
    import time as _time

    class RDev(_Dev):
        def __init__(self):
            super().__init__()
            self.reports = [make_report(accel=(0, 4300, 0), trigger=255, buttons4=0x40, accel_frame1=(0, 4200, 0)),
                            make_report(accel=(0, 4300, 0), trigger=255, buttons4=0x40)]  # duplicate frames -> one callback

        def read(self, n, timeout=None):
            if self.reports:
                return list(self.reports.pop(0))
            _time.sleep(0.005)
            return []

    dev = RDev()
    c = _controller(monkeypatch, dev)
    got = []
    done = threading.Event()

    def on_frame(accel, gyro, t, trigger, move):
        got.append((accel, t, trigger, move))
        if len(got) == 3:
            done.set()

    c.on_frame = on_frame
    c.start_reader()
    assert done.wait(1.0)
    assert not c.poll()  # the reader owns the device now
    c.stop_reader()
    assert len(got) == 3 and got[0][1] < got[1][1] and 0 < (got[1][1] - got[0][1]) <= 0.0057 + 1e-6
    assert got[1][2] == 1.0 and got[1][3] is True
    assert abs(got[1][0].y - 1.0) < 0.05 and c.state.trigger == 1.0 and c.reports == 2


def test_gyro_scale_is_learned_from_gravity():
    import math

    true_scale = 0.0015  # rad/s per raw unit
    cal = P.AutoCalibration(accel_units_per_g=4300.0, gyro_rad_per_unit=0.0009)  # start 40% off
    dt = 1 / 85
    rate = math.radians(60)  # slow tumble at 60 deg/s about the sensor x axis
    raw_rate = rate / true_scale
    ang = 0.0
    for _ in range(600):
        ang += rate * dt
        accel = (0, int(4300 * math.cos(ang)), int(4300 * math.sin(ang)))
        cal.feed(accel, (int(raw_rate), 0, 0), dt)
    assert cal.scale_updates >= 1
    assert abs(cal.gyro_rad_per_unit - true_scale) / true_scale < 0.15
    assert max(abs(b) for b in cal.gyro_bias) < 50  # a steady rotation must not be mistaken for rest


def test_hid_diagnostics_mentions_status(monkeypatch):
    dev = _Dev()
    c = _controller(monkeypatch, dev)
    c.apply_outputs_now()
    text = P.hid_diagnostics([c])
    assert "slot 1" in text and "LED/rumble sent" in text and "cal:" in text


def test_crc_variant_has_ds4_style_trailer():
    import zlib

    rep = P.build_led_report(10, 20, 30, 0.5)
    v = P.crc_variant(rep)
    assert len(v) == 49 and v[:45] == rep[:45]
    assert int.from_bytes(v[45:], "little") == zlib.crc32(b"\xa2" + rep[:45]) & 0xFFFFFFFF


def test_zcm2_sends_plain_and_crc_variants(monkeypatch):
    dev = _Dev()

    class H:
        @staticmethod
        def device():
            return dev
    monkeypatch.setattr(P, "hid", H)
    c = P.HidMoveController(0, b"col01", "zcm2", "90895fd457b5", led_method="auto", alt_paths=[b"col02"])
    c.open()
    c.set_led(1, 2, 3)
    c.apply_outputs_now()
    labels = set(c.write_results)
    assert "hid_write:plain" in labels and "hid_write:crc" in labels
    assert dev.writes[0][:5] == bytes([2, 0, 1, 2, 3]) and dev.writes[1][:45] == dev.writes[0][:45] and dev.writes[1][45:] != bytes(4)


def test_bluetooth_detected_from_12_hex_serial_and_path():
    entries = [
        {"vendor_id": P.PSMOVE_VID, "product_id": P.PSMOVE_PID_ZCM2, "path": b"\\\\?\\HID#{00001124-0000-1000-8000-00805f9b34fb}_VID&0002054c_PID&0c5e&Col01#8&1&0000", "serial_number": "90895FD457B5", "usage_page": 1},
    ]
    found = P.enumerate_controllers(entries)
    assert found[0].interface == "bluetooth" and found[0].serial == "90895fd457b5" and found[0].model == "zcm2"


def test_gyro_scale_ignores_sensor_noise():
    import random

    rnd = random.Random(1)
    cal = P.AutoCalibration(accel_units_per_g=4096.0, gyro_rad_per_unit=0.001)
    for _ in range(4000):  # resting controller with accelerometer noise at ~400 Hz
        accel = (int(rnd.gauss(0, 40)), int(rnd.gauss(0, 40)), int(4096 + rnd.gauss(0, 40)))
        cal.feed(accel, (int(rnd.gauss(0, 3)), int(rnd.gauss(0, 3)), int(rnd.gauss(0, 3))), 1 / 400)
    assert cal.scale_updates == 0 and cal.gyro_rad_per_unit == 0.001
