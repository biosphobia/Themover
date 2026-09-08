"""Controller discovery and slot assignment (fake hidapi, no hardware)."""
import time

import pytest

from themover.config import Settings
from themover.devices import manager as M
from themover.devices import psmove as P


def entry(serial, path, pid=P.PSMOVE_PID_ZCM1, usage_page=1):
    return {"vendor_id": P.PSMOVE_VID, "product_id": pid, "path": path, "serial_number": serial, "usage_page": usage_page}


def test_enumerate_dedupes_interfaces_and_sorts_bluetooth_first():
    entries = [
        entry("", b"usb-1"),
        entry("00:1B:44:11:3A:B7", b"bt-a-if0", usage_page=0xFF00),
        entry("00:1b:44:11:3a:b7", b"bt-a-if1", usage_page=1),
        entry("00:1B:44:11:3A:C2", b"bt-b", pid=P.PSMOVE_PID_ZCM2),
        {"vendor_id": 0x1234, "product_id": P.PSMOVE_PID_ZCM1, "path": b"other", "serial_number": ""},
    ]
    found = P.enumerate_controllers(entries)
    assert [d.key for d in found] == ["00:1b:44:11:3a:b7", "00:1b:44:11:3a:c2", "usb-1"]
    assert found[0].path == b"bt-a-if1" and found[0].interface == "bluetooth"
    assert set(found[0].paths) == {b"bt-a-if0", b"bt-a-if1"}  # every collection is remembered for output fallbacks
    assert found[1].model == "zcm2" and found[2].interface == "usb"
    assert "PS Move (PS4 model)" in found[1].label


class FakeDev:
    """Stands in for hid.device: returns nothing to read, accepts writes."""

    def __init__(self):
        self.path = b""
        self.writes = []
        self.closed = False

    def open_path(self, path):
        if path == b"broken":
            raise OSError("cannot open")
        self.path = path

    def set_nonblocking(self, v):
        pass

    def read(self, n):
        return []

    def write(self, data):
        self.writes.append(bytes(data))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_hid(monkeypatch):
    state = {"entries": []}

    class FakeHidModule:
        @staticmethod
        def enumerate(vid, pid):
            return list(state["entries"])

        device = FakeDev

    monkeypatch.setattr(P, "hid", FakeHidModule)
    return state


def make_manager(backend="auto"):
    s = Settings(camera_backend="synthetic", controller_backend=backend)
    return M.DeviceManager(s), s


def real(dm):
    return [c.key if isinstance(c, P.HidMoveController) and c.state.connected else None for c in dm.controllers]


def test_two_controllers_get_separate_slots_and_leds(fake_hid):
    fake_hid["entries"] = [entry("aa:aa:aa:aa:aa:01", b"p1"), entry("aa:aa:aa:aa:aa:02", b"p2")]
    dm, s = make_manager()
    dm.open_controllers()
    assert real(dm) == ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]
    assert s.controller_serials == ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]
    assert [c.state.index for c in dm.controllers] == [0, 1]
    # Each slot got its own colour written to its own device.
    for i, c in enumerate(dm.controllers):
        assert c._dev.writes[-1][2:5] == bytes(s.controller_colors[i])
    assert dm.real_controller_count() == 2
    assert "aa:aa:aa:aa:aa:01" in dm.status_message and "aa:aa:aa:aa:aa:02" in dm.status_message


def test_remembered_slots_survive_enumeration_order(fake_hid):
    fake_hid["entries"] = [entry("aa:aa:aa:aa:aa:02", b"p2"), entry("aa:aa:aa:aa:aa:01", b"p1")]
    dm, s = make_manager()
    s.controller_serials = ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]
    dm.open_controllers()
    assert real(dm) == ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]


def test_hotplug_fills_second_slot_later_and_handles_disconnect(fake_hid):
    fake_hid["entries"] = [entry("aa:aa:aa:aa:aa:01", b"p1")]
    dm, s = make_manager()
    dm.open_controllers()
    assert real(dm) == ["aa:aa:aa:aa:aa:01", None]
    assert isinstance(dm.controllers[1], P.SimulatedMove)
    # Second controller shows up; the periodic rescan in update() picks it up.
    fake_hid["entries"].append(entry("aa:aa:aa:aa:aa:02", b"p2"))
    dm._last_rescan = time.monotonic() - M.RESCAN_INTERVAL_S - 1
    dm.update()
    assert real(dm) == ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]
    # Controller 1 drops: its slot falls back to simulated, slot 2 is untouched.
    dm.controllers[0].state.connected = False
    dm.update()
    assert isinstance(dm.controllers[0], P.SimulatedMove)
    assert real(dm)[1] == "aa:aa:aa:aa:aa:02"
    # It comes back and lands in slot 1 again (remembered).
    dm._last_rescan = time.monotonic() - M.RESCAN_INTERVAL_S - 1
    dm.update()
    assert real(dm) == ["aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"]


def test_swap_and_forget(fake_hid):
    fake_hid["entries"] = [entry("aa:aa:aa:aa:aa:01", b"p1"), entry("aa:aa:aa:aa:aa:02", b"p2")]
    dm, s = make_manager()
    dm.open_controllers()
    dm.swap_controllers()
    assert real(dm) == ["aa:aa:aa:aa:aa:02", "aa:aa:aa:aa:aa:01"]
    assert s.controller_serials == ["aa:aa:aa:aa:aa:02", "aa:aa:aa:aa:aa:01"]
    assert [c.state.index for c in dm.controllers] == [0, 1]
    assert dm.controllers[0]._dev.writes[-1][2:5] == bytes(s.controller_colors[0])
    dm.forget_assignment()
    assert s.controller_serials == ["", ""]


def test_broken_device_falls_back_to_simulated(fake_hid):
    fake_hid["entries"] = [entry("aa:aa:aa:aa:aa:01", b"broken"), entry("aa:aa:aa:aa:aa:02", b"p2")]
    dm, _ = make_manager()
    dm.open_controllers()
    assert real(dm)[0] is None and real(dm)[1] == "aa:aa:aa:aa:aa:02"
    assert "could not be opened" in dm.status_message or "simulated" in dm.status_message


def test_simulated_backend_ignores_hardware(fake_hid):
    fake_hid["entries"] = [entry("aa:aa:aa:aa:aa:01", b"p1")]
    dm, _ = make_manager("simulated")
    dm.open_controllers()
    assert real(dm) == [None, None] and dm.discovered == []
