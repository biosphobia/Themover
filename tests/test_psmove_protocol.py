from themover.devices import psmove as P
from themover.core.state import Vec3


def make_report(buttons1=0, buttons2=0, buttons3=0, buttons4=0, trigger=0, battery=5, accel=(0, 0, 0), gyro=(0, 0, 0), mag=(0, 0, 0), model="zcm1"):
    rep = bytearray(49)
    rep[0] = P.REQ_GET_INPUT
    rep[1], rep[2], rep[3], rep[4] = buttons1, buttons2, buttons3, buttons4
    rep[5] = rep[6] = trigger
    rep[13] = battery

    def put16(off, v):
        if model == "zcm1":
            v = v + 0x8000
            rep[off] = v & 0xFF
            rep[off + 1] = (v >> 8) & 0xFF
        else:
            v &= 0xFFFF
            rep[off] = v & 0xFF
            rep[off + 1] = (v >> 8) & 0xFF

    for i, v in enumerate(accel):
        put16(14 + 2 * i, v)
        put16(20 + 2 * i, v)
    for i, v in enumerate(gyro):
        put16(26 + 2 * i, v)
        put16(32 + 2 * i, v)
    mx, my, mz = [m & 0xFFF for m in mag]
    rep[39] = (rep[39] & 0xF0) | ((mx >> 8) & 0x0F)
    rep[40] = mx & 0xFF
    rep[41] = (my >> 4) & 0xFF
    rep[42] = ((my & 0x0F) << 4) | ((mz >> 8) & 0x0F)
    rep[43] = mz & 0xFF
    return bytes(rep)


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
    assert c.state.output_status.startswith("LED/rumble ok")


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
    rep = bytearray(make_report(accel=(10, 20, 30), gyro=(1, 2, 3)))
    # frame 0 lives at 14..19 / 26..31; give it different values
    for i, v in enumerate((100, 200, 300)):
        rep[14 + 2 * i] = (v + 0x8000) & 0xFF
        rep[15 + 2 * i] = ((v + 0x8000) >> 8) & 0xFF
    frames = P.parse_input_frames(bytes(rep))
    assert [f.accel for f in frames] == [(100, 200, 300), (10, 20, 30)]
    assert frames[1].gyro == (1, 2, 3)
    assert P.parse_input_frames(b"\x07" + bytes(48)) == []


def test_reader_thread_delivers_frames(monkeypatch):
    import threading
    import time as _time

    class RDev(_Dev):
        def __init__(self):
            super().__init__()
            self.reports = [make_report(accel=(0, 4300, 0), trigger=255, buttons4=0x40)]

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
        if len(got) == 2:
            done.set()

    c.on_frame = on_frame
    c.start_reader()
    assert done.wait(1.0)
    assert not c.poll()  # the reader owns the device now
    c.stop_reader()
    assert len(got) == 2 and got[0][1] < got[1][1] and abs((got[1][1] - got[0][1]) - 0.0057) < 1e-6
    assert got[1][2] == 1.0 and got[1][3] is True
    assert abs(got[1][0].y - 1.0) < 0.05 and c.state.trigger == 1.0 and c.reports == 1
