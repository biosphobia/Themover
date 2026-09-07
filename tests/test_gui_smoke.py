import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def test_main_window_starts_and_arms(qapp):
    from themover.config import Settings, save_settings
    from themover.ui.main_window import MainWindow

    s = Settings(camera_backend="synthetic", controller_backend="simulated")
    save_settings(s)
    w = MainWindow(s)
    try:
        w.show()
        qapp.processEvents()
        assert w.ctx.profile.bindings
        w.play_tab.play_btn.setChecked(True)
        assert w.ctx.armed
        for i in range(5):
            w.tabs.setCurrentIndex(i)
            qapp.processEvents()
        w.tabs.setCurrentIndex(0)
        w.play_tab.refresh()
        w.devices_tab.refresh()
        w.mapping_tab.template_combo.setCurrentIndex(2)
        w.mapping_tab._load_template()
        assert w.mapping_tab.table.rowCount() == len(w.ctx.profile.bindings)
        w.ctx.set_armed(False)
        assert not w.ctx.armed
    finally:
        w.close()
        qapp.processEvents()


def test_binding_dialog_roundtrip(qapp):
    from themover.mapping.profile import Binding
    from themover.ui.mapping_tab import BindingDialog, FeedbackDialog
    from themover.mapping.profile import FeedbackRule

    b = Binding("c1.orient.roll", "gamepad.left_stick_x", mode="axis", input_range=[-30, 30], deadzone=0.2, comment="hi")
    dlg = BindingDialog(b)
    assert dlg.value().to_dict() == b.to_dict()
    f = FeedbackRule(when="c0.trigger", controller=1, rumble=0.7, led=[1, 2, 3], comment="c")
    fd = FeedbackDialog(f)
    assert fd.value().to_dict() == f.to_dict()


def test_ai_tab_recording_signals_and_chat_host(qapp):
    from themover.ai.recorder import Recording
    from themover.config import Settings
    from themover.mapping.templates import load_template
    from themover.ui.ai_tab import AITab, _Host
    from themover.ui.context import AppContext

    ctx = AppContext(Settings(camera_backend="synthetic", controller_backend="simulated"))
    tab = AITab(ctx)
    try:
        tab.rec_progress.emit(5.0, 10.0)
        qapp.processEvents()
        assert tab.progress.value() == 500
        tab.rec_done.emit(Recording(duration=3.0))
        qapp.processEvents()
        assert tab.analyze_btn.isEnabled() and tab.recording is not None
        host = _Host(ctx)
        host.apply_profile(load_template("boxing"))
        assert ctx.profile.name == "Boxing" and host.get_profile().name == "Boxing"
        host.buzz(0, 0.5, (9, 9, 9), 100)
        assert ctx.runtime._overrides[0][2] == (9, 9, 9)
    finally:
        ctx.shutdown()
