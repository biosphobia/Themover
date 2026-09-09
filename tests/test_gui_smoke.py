import os
import sys
import time

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

    s = Settings(camera_backend="synthetic", controller_backend="simulated", check_updates_on_start=False)
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
        # Fine-tune tab: load a synthetic session, tag it, auto-fit applies detector settings to the profile.
        import tempfile
        from pathlib import Path

        from tests.test_motion_capture import synth_session

        ft = w.finetune_tab
        assert ft.timeline.session is None and not ft.send_btn.isEnabled()
        session = synth_session(Path(tempfile.mkdtemp()), [(1.0, "don"), (1.8, "kat")])
        ft.set_session(session)
        assert ft.send_btn.isEnabled() and ft.timeline.session is session
        ft.timeline.set_cursor(1.0)
        ft._add_tag("don")
        ft.timeline.set_cursor(1.8)
        ft._add_tag("kat")
        assert len(session.tags) == 2 and ft.tag_table.rowCount() == 2
        ft.timeline.zoom(0.5)
        assert ft.timeline.grab().width() > 0
        from themover.ai.motion_capture import auto_fit

        cfg, res = auto_fit(session, {"stop_g": 6.0}, complete=True)
        ft._on_autofit((cfg, res))
        assert w.ctx.profile.hit_config["stop_g"] == cfg["stop_g"] and ft.timeline.replay_hits
        assert "matched" in ft.result_lbl.text()
        sent = []
        ft.send_to_coach.connect(lambda s, e, c: sent.append((s, e, c)))
        ft.explain.setText("second is kat")
        ft._send()
        assert sent and sent[0][1] == "second is kat"
        w.tabs.setCurrentIndex(0)
        for _ in range(10):
            w.play_tab.refresh()
        assert w.play_tab.checklist._labels and w.play_tab.checklist._labels[0].isVisibleTo(w.play_tab)
        w.setup_tab.refresh()
        # Camera tracking controls: sliders apply live, drawn rectangles set the crop / zone, calibration runs on synthetic frames.
        st = w.setup_tab
        dev = w.ctx.runtime.devices
        st.brightness.setValue(120)
        assert dev.tracking.min_brightness == 120 and dev.tracker.config is dev.tracking and w.ctx.settings.tracking["min_brightness"] == 120
        st.zone_btn.setChecked(True)
        assert st.camera.mode == "zone"
        st.camera.rect_drawn.emit(0.1, 0.1, 0.6, 0.9)
        assert dev.tracking.zone_enabled and dev.tracking.zone == [0.1, 0.1, 0.6, 0.9] and not st.zone_btn.isChecked() and st.camera.mode == "click"
        st.crop_btn.setChecked(True); st.camera.rect_drawn.emit(0.0, 0.0, 0.5, 0.5)
        assert dev.tracking.crop == [0.0, 0.0, 0.5, 0.5]
        st._clear_areas()
        assert not dev.tracking.zone_enabled and dev.tracking.crop == [0.0, 0.0, 1.0, 1.0]
        for _ in range(20):
            if dev.camera is not None and dev.camera.latest() is not None:
                break
            time.sleep(0.05)
        report = dev.calibrate_tracking(seconds=0.3)
        assert "colour" in report
        st.mask_btn.setChecked(True); st.refresh(); st.mask_btn.setChecked(False)
        assert w.play_tab.how_to_play.program[0] and w.play_tab.how_to_play.grab().width() > 0
        w.mapping_tab.template_combo.setCurrentIndex(2)
        w.mapping_tab._load_template()
        assert w.mapping_tab.table.rowCount() == len(w.ctx.profile.bindings)
        assert w.ctx.profile_key.startswith("user:") and w.ctx.profile.based_on
        # Edits autosave to the active profile file.
        from themover.profiles import load_profile
        p = w.ctx.profile.copy()
        p.bindings.pop()
        w.ctx.apply_profile(p, reason="edited")
        assert len(load_profile(w.ctx.profile_key).bindings) == len(p.bindings)
        # Save as makes a separate copy and switches to it.
        old_key = w.ctx.profile_key
        new_key = w.ctx.save_as("Smoke copy")
        assert new_key != old_key and load_profile(new_key).name == "Smoke copy" and load_profile(old_key).name != "Smoke copy"
        # Simple mode hides raw columns; advanced shows them.
        assert w.mapping_tab.table.isColumnHidden(4) and not w.mapping_tab.table.isColumnHidden(1)
        assert not w.play_tab.cards[0].gauges["roll"].isVisibleTo(w.play_tab)
        w.advanced_toggle.setChecked(True)
        qapp.processEvents()
        assert w.ctx.settings.advanced_mode and not w.mapping_tab.table.isColumnHidden(4)
        assert w.play_tab.cards[0].gauges["roll"].isVisibleTo(w.play_tab)
        assert w.setup_tab.backend.isVisibleTo(w.setup_tab)
        w.advanced_toggle.setChecked(False)
        qapp.processEvents()
        assert not w.setup_tab.backend.isVisibleTo(w.setup_tab)
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
    assert "Tilt left hand sideways" in dlg.preview.text()
    simple = BindingDialog(b, advanced=False)
    assert simple.value().to_dict() == b.to_dict()  # hidden rows keep their values
    f = FeedbackRule(when="c0.trigger", controller=1, rumble=0.7, led=[1, 2, 3], comment="c")
    fd = FeedbackDialog(f)
    assert fd.value().to_dict() == f.to_dict()


def test_ai_tab_recording_signals_and_chat_host(qapp):
    from themover.ai.recorder import Recording
    from themover.config import Settings
    from themover.mapping.templates import load_template
    from themover.ui.ai_tab import AITab, _Host
    from themover.ui.context import AppContext

    ctx = AppContext(Settings(camera_backend="synthetic", controller_backend="simulated", check_updates_on_start=False))
    tab = AITab(ctx)
    try:
        tab.rec_progress.emit(5.0, 10.0)
        qapp.processEvents()
        assert tab.progress.value() == 500
        tab.rec_done.emit(Recording(duration=3.0))
        qapp.processEvents()
        assert tab.analyze_btn.isEnabled() and tab.recording is not None
        ctx.load_profile_key("user:boxing")
        host = _Host(ctx)
        edited = load_template("boxing")
        edited.name = "Boxing tuned"
        host.apply_profile(edited)
        assert ctx.profile.name == "Boxing tuned" and host.get_profile().name == "Boxing tuned"
        from themover.profiles import load_profile
        assert load_profile("user:boxing").name == "Boxing tuned"  # coach edits persist to the built-in profile
        assert "copy" in host.save_profile("Boxing experiment").lower() and ctx.profile_key == "user:boxing_experiment"
        host.buzz(0, 0.5, (9, 9, 9), 100)
        assert ctx.runtime._overrides[0][2] == (9, 9, 9)
    finally:
        ctx.shutdown()
