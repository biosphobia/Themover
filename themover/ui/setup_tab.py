"""Setup: controllers, camera and the Claude API key.  Advanced mode reveals the knobs."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from themover.ai.client import ClaudeClient
from themover.config import AVAILABLE_MODELS, save_settings, settings_path
from themover.devices.psmove import HidMoveController, hid_diagnostics
from themover.devices.tracker import TrackingConfig
from themover.ui.camera_view import CameraView
from themover.ui.context import AppContext
from themover.links import controller_install_html
from themover.ui.widgets import WrapLabel
from themover.ui.workers import Worker, run_in_background
from themover.updater import Updater, current_version, remove_override


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    t = QLabel(title)
    t.setObjectName("h2")
    layout.addWidget(t)
    return card, layout


class SetupTab(QWidget):
    def __init__(self, ctx: AppContext, on_api_changed=None, on_restart=None) -> None:
        super().__init__()
        self.ctx = ctx
        self.on_api_changed = on_api_changed
        self.on_restart = on_restart
        s = ctx.settings
        self._advanced_widgets: list[QWidget] = []
        self.updater = Updater(s)

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        root = QHBoxLayout(body)
        left = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        # ================================================== controllers
        card, cl = _card("Controllers")
        info = WrapLabel("Pair once with PSMoveServiceEx (then close it). Press PS on a controller to connect. 1 = right hand, 2 = left hand.")
        cl.addWidget(info)
        self.ctrl_status = WrapLabel("…", muted=False); cl.addWidget(self.ctrl_status)
        self.install_help = WrapLabel("", muted=False)
        self.install_help.setTextFormat(Qt.RichText); self.install_help.setOpenExternalLinks(True)
        self.install_help.setVisible(False)
        cl.addWidget(self.install_help)
        row = QHBoxLayout()
        self.identify_btns = [QPushButton("Buzz 1"), QPushButton("Buzz 2")]
        self.swap_btn = QPushButton("Swap 1 ↔ 2")
        for b in self.identify_btns:
            row.addWidget(b)
        row.addWidget(self.swap_btn)
        row.addStretch(1)
        cl.addLayout(row)
        self.detected = WrapLabel(""); cl.addWidget(self.detected); self._advanced_widgets.append(self.detected)
        adv = QWidget(); al = QGridLayout(adv); al.setContentsMargins(0, 6, 0, 0)
        self.rescan_btn = QPushButton("Rescan now")
        self.forget_btn = QPushButton("Forget slot assignment")
        self.backend = QComboBox(); self.backend.addItems(["auto", "hid", "simulated"]); self.backend.setCurrentText(s.controller_backend)
        self.led_method = QComboBox(); self.led_method.addItems(["auto", "write", "control"]); self.led_method.setCurrentText(s.led_method)
        self.led_method.setToolTip("How LED/rumble reports are sent. auto = hid_write, falling back to the Windows control pipe if writes fail.")
        self.recenter_btn = QPushButton("Re-centre yaw (point both at the screen, then click)")
        self.diag_btn = QPushButton("Copy HID diagnostics (for bug reports)")
        self.kbtest_btn = QPushButton("Test keyboard output (types 'themover' after 3 s)")
        self.motion_lbl = WrapLabel("")
        al.addWidget(self.rescan_btn, 0, 0); al.addWidget(self.forget_btn, 0, 1)
        al.addWidget(QLabel("Controller backend"), 1, 0); al.addWidget(self.backend, 1, 1)
        al.addWidget(QLabel("LED / rumble method"), 2, 0); al.addWidget(self.led_method, 2, 1)
        al.addWidget(self.recenter_btn, 3, 0, 1, 2)
        al.addWidget(self.diag_btn, 4, 0)
        al.addWidget(self.kbtest_btn, 4, 1)
        al.addWidget(self.motion_lbl, 5, 0, 1, 2)
        cl.addWidget(adv); self._advanced_widgets.append(adv)
        left.addWidget(card)

        # ======================================================= claude
        card, kl = _card("Claude (AI Coach)")
        krow = QHBoxLayout()
        self.api_key = QLineEdit(s.api_key); self.api_key.setEchoMode(QLineEdit.Password); self.api_key.setPlaceholderText("sk-ant-…  (get one at console.anthropic.com)")
        self.show_key = QPushButton("show"); self.show_key.setCheckable(True); self.show_key.setFixedWidth(60)
        self.test_btn = QPushButton("Save && test")
        krow.addWidget(self.api_key, 1); krow.addWidget(self.show_key); krow.addWidget(self.test_btn)
        kl.addLayout(krow)
        self.test_status = WrapLabel("Needed only for the AI Coach. Stored on this PC.")
        kl.addWidget(self.test_status)
        adv = QWidget(); af = QFormLayout(adv); af.setContentsMargins(0, 6, 0, 0)
        self.model = QComboBox(); self.model.setEditable(True); self.model.addItems(AVAILABLE_MODELS); self.model.setCurrentText(s.model)
        self.effort = QComboBox(); self.effort.addItems(["low", "medium", "high", "xhigh", "max"]); self.effort.setCurrentText(s.effort)
        self.fallback = QCheckBox("If Claude declines a request, retry on a fallback model"); self.fallback.setChecked(s.refusal_fallback)
        self.rec_seconds = QSpinBox(); self.rec_seconds.setRange(10, 300); self.rec_seconds.setValue(s.record_seconds); self.rec_seconds.setSuffix(" s")
        self.rec_fps = QDoubleSpinBox(); self.rec_fps.setRange(0.2, 5.0); self.rec_fps.setSingleStep(0.2); self.rec_fps.setValue(s.record_fps); self.rec_fps.setSuffix(" fps")
        self.max_frames = QSpinBox(); self.max_frames.setRange(1, 40); self.max_frames.setValue(s.max_frames_to_send)
        af.addRow("Model", self.model)
        af.addRow("Effort", self.effort)
        af.addRow("", self.fallback)
        af.addRow("Recording length", self.rec_seconds)
        af.addRow("Screenshot rate", self.rec_fps)
        af.addRow("Screenshots sent to Claude", self.max_frames)
        kl.addWidget(adv); self._advanced_widgets.append(adv)
        left.addWidget(card)

        # ================================================== game output
        card, ol = _card("Game output")
        self._advanced_widgets.append(card)
        self.output_info = WrapLabel("Keyboard and mouse work out of the box. Gamepad profiles need the free ViGEmBus driver (github.com/nefarius/ViGEmBus).")
        ol.addWidget(self.output_info)
        adv = QWidget(); of = QFormLayout(adv); of.setContentsMargins(0, 6, 0, 0)
        self.output_backend = QComboBox(); self.output_backend.addItems(["auto", "sendinput", "pynput"]); self.output_backend.setCurrentText(s.output_backend)
        self.gamepad = QCheckBox("Virtual Xbox 360 pad"); self.gamepad.setChecked(s.gamepad_enabled)
        self.tick = QSpinBox(); self.tick.setRange(30, 250); self.tick.setValue(s.tick_hz); self.tick.setSuffix(" Hz")
        of.addRow("Keyboard/mouse backend", self.output_backend)
        of.addRow("", self.gamepad)
        of.addRow("Engine rate", self.tick)
        ol.addWidget(adv); self._advanced_widgets.append(adv)
        self.path_lbl = WrapLabel(f"Settings file: {settings_path()}")
        ol.addWidget(self.path_lbl); self._advanced_widgets.append(self.path_lbl)
        left.addWidget(card)

        # ====================================================== updates
        card, ul = _card("Updates")
        self.version_lbl = WrapLabel(current_version().describe(), muted=False)
        ul.addWidget(self.version_lbl)
        self.update_status = WrapLabel("Updates come straight from GitHub pushes; no new .exe needed unless a dependency changes.")
        ul.addWidget(self.update_status)
        urow = QHBoxLayout()
        self.check_btn = QPushButton("Check for updates")
        self.update_btn = QPushButton("Update && restart"); self.update_btn.setObjectName("accent2"); self.update_btn.setEnabled(False)
        urow.addWidget(self.check_btn); urow.addWidget(self.update_btn); urow.addStretch(1)
        ul.addLayout(urow)
        adv = QWidget(); uf = QFormLayout(adv); uf.setContentsMargins(0, 6, 0, 0)
        self.upd_repo = QLineEdit(f"{s.update_owner}/{s.update_repo}")
        self.upd_branch = QLineEdit(s.update_branch); self.upd_branch.setPlaceholderText(f"(branch of this build: {current_version().branch or 'main'})")
        self.upd_token = QLineEdit(s.github_token); self.upd_token.setEchoMode(QLineEdit.Password); self.upd_token.setPlaceholderText("only for private repositories")
        self.upd_on_start = QCheckBox("Check for updates when The Mover starts"); self.upd_on_start.setChecked(s.check_updates_on_start)
        self.revert_btn = QPushButton("Remove downloaded update (use the built-in copy)")
        uf.addRow("GitHub repo", self.upd_repo)
        uf.addRow("Branch", self.upd_branch)
        uf.addRow("Token", self.upd_token)
        uf.addRow("", self.upd_on_start)
        uf.addRow("", self.revert_btn)
        ul.addWidget(adv); self._advanced_widgets.append(adv)
        left.addWidget(card)
        left.addStretch(1)

        # ======================================================= camera
        card, ccl = _card("Camera (PS3 Eye or any webcam)")
        self.camera = CameraView(); ccl.addWidget(self.camera, 1)
        hint = WrapLabel("Only some profiles use the camera. Light both controllers, point them at the camera and press Calibrate. "
                         "Drag on the picture to set the tracking area (what the tracker looks at) or the trigger zone (where camera input counts).")
        ccl.addWidget(hint)
        self.calibrate_btn = QPushButton("✨  Calibrate colours && thresholds (both controllers lit)"); self.calibrate_btn.setObjectName("accent2")
        ccl.addWidget(self.calibrate_btn)
        trow = QGridLayout(); trow.setContentsMargins(0, 0, 0, 0)
        self.crop_btn = QPushButton("Set tracking area"); self.crop_btn.setCheckable(True)
        self.zone_btn = QPushButton("Set trigger zone"); self.zone_btn.setCheckable(True)
        self.clear_btn = QPushButton("Clear areas")
        self.mask_btn = QPushButton("Show tracker view"); self.mask_btn.setCheckable(True)
        for i, w in enumerate((self.crop_btn, self.zone_btn, self.clear_btn, self.mask_btn)):
            trow.addWidget(w, i // 2, i % 2)
        ccl.addLayout(trow)
        tune = QGridLayout(); tune.setContentsMargins(0, 4, 0, 0)
        self.mask_lights = QCheckBox("Mask to the controller lights only (bright + colourful pixels)")
        tune.addWidget(self.mask_lights, 0, 0, 1, 3)
        self.brightness = QSlider(Qt.Horizontal); self.brightness.setRange(0, 255)
        self.saturation = QSlider(Qt.Horizontal); self.saturation.setRange(0, 255)
        self.hue_tol = QSlider(Qt.Horizontal); self.hue_tol.setRange(3, 60)
        self.brightness_lbl = QLabel(); self.saturation_lbl = QLabel(); self.hue_tol_lbl = QLabel()
        for row, (name, slider, lbl, tip) in enumerate((
            ("Brightness", self.brightness, self.brightness_lbl, "How bright a pixel must be to count as a light. Raise it if the room shows up in the mask."),
            ("Colourfulness", self.saturation, self.saturation_lbl, "How saturated a pixel must be. Lower it if the spheres look washed out."),
            ("Colour strictness", self.hue_tol, self.hue_tol_lbl, "How far a blob's hue may be from the controller colour (small = strict)."),
        ), start=1):
            slider.setToolTip(tip); lbl.setMinimumWidth(36)
            tune.addWidget(QLabel(name), row, 0); tune.addWidget(slider, row, 1); tune.addWidget(lbl, row, 2)
        tune.setColumnStretch(1, 1)
        ccl.addLayout(tune)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Click sets colour of"))
        self.sample_target = QComboBox(); self.sample_target.addItems(["controller 1", "controller 2"]); crow.addWidget(self.sample_target)
        self.color_btns = []
        for i in range(2):
            cb = QPushButton(f"colour {i + 1}"); cb.setMinimumHeight(30); self.color_btns.append(cb); crow.addWidget(cb)
        crow.addStretch(1)
        cw = QWidget(); cw.setLayout(crow); ccl.addWidget(cw); self._advanced_widgets.append(cw)
        adv = QWidget(); cf = QFormLayout(adv); cf.setContentsMargins(0, 6, 0, 0)
        self.cam_backend = QComboBox(); self.cam_backend.addItems(["auto", "pseye", "opencv", "synthetic"]); self.cam_backend.setCurrentText(s.camera_backend)
        self.cam_index = QSpinBox(); self.cam_index.setRange(0, 9); self.cam_index.setValue(s.camera_index)
        self.mirror = QCheckBox("mirror (camera faces you)"); self.mirror.setChecked(s.camera_mirror)
        self.reconnect_btn = QPushButton("Reconnect camera")
        srow = QHBoxLayout(); srow.addWidget(self.cam_backend); srow.addWidget(QLabel("index")); srow.addWidget(self.cam_index); srow.addStretch(1)
        cf.addRow("Source", srow)
        cf.addRow("", self.mirror)
        cf.addRow("", self.reconnect_btn)
        erow = QHBoxLayout()
        self.exposure = QSpinBox(); self.exposure.setRange(-13, 255); self.exposure.setToolTip("Webcam / CL-Eye: -13..0 (lower = darker). PS3 Eye via pseyepy: 0..255.")
        self.gain = QSpinBox(); self.gain.setRange(0, 79)
        erow.addWidget(QLabel("exposure")); erow.addWidget(self.exposure); erow.addWidget(QLabel("gain")); erow.addWidget(self.gain); erow.addStretch(1)
        cf.addRow("Exposure", erow)
        self.min_radius = QDoubleSpinBox(); self.min_radius.setRange(1, 60); self.min_radius.setSuffix(" px"); self.min_radius.setDecimals(0)
        self.max_radius = QDoubleSpinBox(); self.max_radius.setRange(10, 400); self.max_radius.setSuffix(" px"); self.max_radius.setDecimals(0)
        self.smoothing = QDoubleSpinBox(); self.smoothing.setRange(0.0, 0.95); self.smoothing.setSingleStep(0.05)
        self.downscale = QSpinBox(); self.downscale.setRange(1, 4); self.downscale.setToolTip("Detect on a picture shrunk by this factor (faster); the centre is always refined at full resolution.")
        rrow = QHBoxLayout(); rrow.addWidget(QLabel("min")); rrow.addWidget(self.min_radius); rrow.addWidget(QLabel("max")); rrow.addWidget(self.max_radius); rrow.addStretch(1)
        cf.addRow("Sphere size", rrow)
        frow = QHBoxLayout(); frow.addWidget(QLabel("smoothing")); frow.addWidget(self.smoothing); frow.addWidget(QLabel("shrink ×")); frow.addWidget(self.downscale); frow.addStretch(1)
        cf.addRow("Filter", frow)
        drow = QHBoxLayout()
        self.near_btn = QPushButton("Set NEAR (stand close)"); self.far_btn = QPushButton("Set FAR (stand back)")
        drow.addWidget(self.near_btn); drow.addWidget(self.far_btn)
        cf.addRow("Depth", drow)
        ccl.addWidget(adv); self._advanced_widgets.append(adv)
        self.cam_status = WrapLabel(""); ccl.addWidget(self.cam_status)
        self._tracking_widgets = (self.mask_lights, self.brightness, self.saturation, self.hue_tol, self.exposure, self.gain,
                                  self.min_radius, self.max_radius, self.smoothing, self.downscale)
        self._sync_tracking_widgets()
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(lambda: save_settings(self.ctx.settings))
        right.addWidget(card)
        right.addStretch(1)

        # ---------------------------------------------------------- wiring
        for i, b in enumerate(self.identify_btns):
            b.clicked.connect(lambda _=False, i=i: self.ctx.buzz(i, 1.0, (255, 255, 255), 600))
        self.swap_btn.clicked.connect(self._swap)
        self.rescan_btn.clicked.connect(self._rescan)
        self.forget_btn.clicked.connect(self._forget)
        self.backend.currentTextChanged.connect(self._backend_changed)
        self.led_method.currentTextChanged.connect(self._led_method_changed)
        self.recenter_btn.clicked.connect(lambda: self.ctx.runtime.devices.reset_yaw())
        self.diag_btn.clicked.connect(self._diagnostics)
        self.kbtest_btn.clicked.connect(self._keyboard_test)
        self.show_key.toggled.connect(lambda on: self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.test_btn.clicked.connect(self._test)
        self.api_key.editingFinished.connect(self.save)
        for w in (self.model, self.effort, self.output_backend):
            w.currentTextChanged.connect(lambda _t: self.save())
        for w in (self.fallback, self.gamepad):
            w.toggled.connect(lambda _v: self.save())
        for w in (self.rec_seconds, self.max_frames, self.tick):
            w.valueChanged.connect(lambda _v: self.save())
        self.rec_fps.valueChanged.connect(lambda _v: self.save())
        for i, b in enumerate(self.color_btns):
            b.clicked.connect(lambda _=False, i=i: self._pick_color(i))
        self.camera.clicked.connect(self._sample_color)
        self.camera.rect_drawn.connect(self._rect_drawn)
        self.calibrate_btn.clicked.connect(self._calibrate_tracking)
        self.crop_btn.toggled.connect(lambda on: self._set_draw_mode("crop", on))
        self.zone_btn.toggled.connect(lambda on: self._set_draw_mode("zone", on))
        self.clear_btn.clicked.connect(self._clear_areas)
        for w in (self.mask_lights,):
            w.toggled.connect(self._tracking_changed)
        for w in (self.brightness, self.saturation, self.hue_tol, self.exposure, self.gain, self.downscale):
            w.valueChanged.connect(self._tracking_changed)
        for w in (self.min_radius, self.max_radius, self.smoothing):
            w.valueChanged.connect(self._tracking_changed)
        self.reconnect_btn.clicked.connect(self._reconnect_camera)
        self.near_btn.clicked.connect(lambda: self._set_depth("near"))
        self.far_btn.clicked.connect(lambda: self._set_depth("far"))
        self.check_btn.clicked.connect(lambda: self.check_updates(silent=False))
        self.update_btn.clicked.connect(self._install_update)
        self.revert_btn.clicked.connect(self._revert_update)
        for w in (self.upd_repo, self.upd_branch, self.upd_token):
            w.editingFinished.connect(self._save_update_settings)
        self.upd_on_start.toggled.connect(lambda _v: self._save_update_settings())
        ctx.advanced_changed.connect(self.set_advanced)
        self.set_advanced(ctx.advanced)
        self._update_swatches()

    def set_advanced(self, on: bool) -> None:
        for w in self._advanced_widgets:
            w.setVisible(on)

    # ------------------------------------------------------------ updates
    def _save_update_settings(self) -> None:
        s = self.ctx.settings
        owner, _, repo = self.upd_repo.text().strip().partition("/")
        if owner and repo:
            s.update_owner, s.update_repo = owner, repo
        s.update_branch = self.upd_branch.text().strip()
        s.github_token = self.upd_token.text().strip()
        s.check_updates_on_start = self.upd_on_start.isChecked()
        save_settings(s)

    def check_updates(self, silent: bool = True) -> None:
        """Download the branch archive in the background and compare its commit with ours."""
        if not silent:
            self.update_status.setText("Checking GitHub…")
        self.check_btn.setEnabled(False)
        updater = self.updater

        def job(signals):
            return updater.check()

        w = Worker(job)
        w.signals.finished.connect(self._on_update_status)
        w.signals.error.connect(self._on_update_error)
        run_in_background(w)

    def _on_update_status(self, status) -> None:
        self.check_btn.setEnabled(True)
        self.update_status.setText(status.text())
        self.update_btn.setEnabled(bool(status.available) and self.updater.can_install)
        if status.available and not self.updater.can_install:
            self.update_status.setText(status.text() + " You run from source: use `git pull`.")
        if status.available:
            self.ctx.status.emit("Update available - Setup → Update && restart")

    def _on_update_error(self, err: str) -> None:
        self.check_btn.setEnabled(True)
        self.update_status.setText("Update check failed: " + err.split("\n")[0])

    def _install_update(self) -> None:
        self.update_btn.setEnabled(False)
        self.update_status.setText("Downloading and installing…")
        updater = self.updater

        def job(signals):
            return str(updater.install())

        w = Worker(job)
        w.signals.finished.connect(self._on_update_installed)
        w.signals.error.connect(self._on_update_error)
        run_in_background(w)

    def _on_update_installed(self, path: str) -> None:
        self.update_status.setText("Installed. Restarting…")
        if self.on_restart:
            self.on_restart()

    def _revert_update(self) -> None:
        remove_override()
        self.update_status.setText("Downloaded update removed. Restart to use the built-in copy.")
        self.update_btn.setEnabled(False)

    # ---------------------------------------------------------- settings
    def save(self) -> None:
        s = self.ctx.settings
        s.api_key = self.api_key.text().strip()
        s.model = self.model.currentText().strip() or s.model
        s.effort = self.effort.currentText()
        s.refusal_fallback = self.fallback.isChecked()
        s.output_backend = self.output_backend.currentText()
        s.gamepad_enabled = self.gamepad.isChecked()
        s.tick_hz = self.tick.value()
        s.record_seconds = self.rec_seconds.value()
        s.record_fps = self.rec_fps.value()
        s.max_frames_to_send = self.max_frames.value()
        save_settings(s)
        if self.on_api_changed:
            self.on_api_changed()

    def _test(self) -> None:
        self.save()
        if not self.ctx.settings.effective_api_key:
            self.test_status.setText("Paste an API key first.")
            return
        self.test_status.setText("Saved. Testing…")
        client = ClaudeClient(self.ctx.settings)

        def job(signals):
            return client.test_connection()

        w = Worker(job)
        w.signals.finished.connect(self._on_test_ok)
        w.signals.error.connect(self._on_test_error)
        run_in_background(w)

    def _on_test_ok(self, text: str) -> None:
        self.test_status.setText(f"Saved. Claude says: {text}")

    def _on_test_error(self, err: str) -> None:
        self.test_status.setText("Saved, but the test failed: " + err.split("\n")[0])

    # ------------------------------------------------------- controllers
    def _keyboard_test(self) -> None:
        """Type a word into whatever window has focus in 3 s, then report SendInput results."""
        from PySide6.QtCore import QTimer

        from themover.outputs.keyboard_mouse import create_keyboard_mouse_sink

        self.motion_lbl.setText("Click into Notepad or the game now… typing in 3 s")

        def go() -> None:
            import time

            sink = create_keyboard_mouse_sink(self.ctx.settings.output_backend)
            for ch in "themover":
                sink.key_down(ch)
                time.sleep(0.03)
                sink.key_up(ch)
                time.sleep(0.03)
            status = sink.status() or f"sent via {sink.description}"
            recent = " | ".join(getattr(sink, "recent", [])[-4:])
            sink.close()
            self.motion_lbl.setText(f"Keyboard test: {status}" + (f"  [{recent}]" if recent else ""))

        QTimer.singleShot(3000, go)

    def _diagnostics(self) -> None:
        import logging

        text = hid_diagnostics(self.ctx.runtime.devices.controllers)
        sink = self.ctx.runtime.sink
        text += f"\noutput: {sink.description} · armed={self.ctx.armed} · {sink.status() or 'no status'}"
        recent = getattr(sink, "recent", None) or getattr(getattr(sink, "km", None), "recent", None)
        if recent:
            text += "\nrecent sends: " + " | ".join(recent[-8:])
        text += f"\nprofile: {self.ctx.profile.name} ({len(self.ctx.profile.bindings)} bindings, fast sources {sorted(self.ctx.runtime.engine.fast_sources)})"
        text += f"\nengine: ticks={self.ctx.runtime.engine.stats.ticks} rate={self.ctx.runtime.tick_rate:.0f} Hz active={sorted(self.ctx.runtime.engine.stats.active_targets)} hits logged={len(self.ctx.runtime.hit_log)}"
        logging.getLogger("themover.diagnostics").info("HID diagnostics:\n%s", text)
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "HID diagnostics (copied to clipboard)", text)

    def _swap(self) -> None:
        self.ctx.runtime.devices.swap_controllers()
        save_settings(self.ctx.settings)
        self.refresh()

    def _forget(self) -> None:
        self.ctx.runtime.devices.forget_assignment()
        save_settings(self.ctx.settings)
        self._rescan()

    def _rescan(self) -> None:
        self.ctx.runtime.devices.rescan(force=True)
        save_settings(self.ctx.settings)
        self.refresh()

    def _backend_changed(self, text: str) -> None:
        self.ctx.settings.controller_backend = text
        save_settings(self.ctx.settings)
        self.ctx.runtime.devices.open_controllers()

    def _led_method_changed(self, text: str) -> None:
        self.ctx.settings.led_method = text
        save_settings(self.ctx.settings)
        self.ctx.runtime.devices.open_controllers()

    # ------------------------------------------------------------ camera
    def _update_swatches(self) -> None:
        for i, b in enumerate(self.color_btns):
            r, g, bl = self.ctx.settings.controller_colors[i]
            b.setStyleSheet(f"background: rgb({r},{g},{bl}); color: {'#000' if (r + g + bl) > 380 else '#fff'};")

    def _pick_color(self, index: int) -> None:
        r, g, b = self.ctx.settings.controller_colors[index]
        col = QColorDialog.getColor(QColor(r, g, b), self, f"Sphere colour for controller {index + 1}")
        if col.isValid():
            self._set_color(index, (col.red(), col.green(), col.blue()))

    def _set_color(self, index: int, rgb: tuple[int, int, int]) -> None:
        self.ctx.runtime.devices.set_color(index, rgb)
        p = self.ctx.profile.copy()
        p.controllers[index].color = list(rgb)
        self.ctx.apply_profile(p, reason="color")
        save_settings(self.ctx.settings)
        self._update_swatches()

    def _sample_color(self, x: int, y: int) -> None:
        frame = self.ctx.runtime.devices.latest_frame(overlay=False)
        if frame is None:
            return
        rgb = self.ctx.runtime.devices.tracker.sample_color_at(frame, x, y)
        self._set_color(self.sample_target.currentIndex(), rgb)
        self.cam_status.setText(f"Controller {self.sample_target.currentIndex() + 1} colour set to {rgb} from the picture.")

    def _set_depth(self, which: str) -> None:
        tracker = self.ctx.runtime.devices.tracker
        st = tracker.states[0] if tracker.states else None
        if st is None or not st.tracked:
            QMessageBox.information(self, "Not tracked", "Controller 1's sphere must be visible to the camera.")
            return
        cfg = self._tracking_config()
        if which == "near":
            cfg.radius_near = max(st.radius, cfg.radius_far + 2)
        else:
            cfg.radius_far = min(st.radius, cfg.radius_near - 2)
        self._apply_tracking(cfg)
        self.cam_status.setText(f"Depth calibration: far={cfg.radius_far:.0f}px near={cfg.radius_near:.0f}px")

    # ------------------------------------------------------------ tracking
    def _tracking_config(self) -> TrackingConfig:
        cfg = TrackingConfig.from_dict(self.ctx.runtime.devices.tracking.to_dict())
        cfg.mask_lights = self.mask_lights.isChecked()
        cfg.min_brightness = self.brightness.value()
        cfg.min_saturation = self.saturation.value()
        cfg.hue_tolerance = self.hue_tol.value()
        cfg.exposure = self.exposure.value()
        cfg.gain = self.gain.value()
        cfg.min_radius = self.min_radius.value()
        cfg.max_radius = max(self.min_radius.value() + 1, self.max_radius.value())
        cfg.smoothing = self.smoothing.value()
        cfg.downscale = self.downscale.value()
        return cfg

    def _sync_tracking_widgets(self) -> None:
        cfg = self.ctx.runtime.devices.tracking
        for w in self._tracking_widgets:
            w.blockSignals(True)
        self.mask_lights.setChecked(cfg.mask_lights)
        self.brightness.setValue(cfg.min_brightness); self.saturation.setValue(cfg.min_saturation); self.hue_tol.setValue(cfg.hue_tolerance)
        self.exposure.setValue(int(cfg.exposure)); self.gain.setValue(int(cfg.gain))
        self.min_radius.setValue(cfg.min_radius); self.max_radius.setValue(cfg.max_radius); self.smoothing.setValue(cfg.smoothing); self.downscale.setValue(cfg.downscale)
        for w in self._tracking_widgets:
            w.blockSignals(False)
        self.brightness_lbl.setText(str(cfg.min_brightness)); self.saturation_lbl.setText(str(cfg.min_saturation)); self.hue_tol_lbl.setText(f"±{cfg.hue_tolerance}")
        for slider in (self.brightness, self.saturation):
            slider.setEnabled(cfg.mask_lights)

    def _apply_tracking(self, cfg: TrackingConfig) -> None:
        self.ctx.runtime.devices.apply_tracking(cfg)
        self._sync_tracking_widgets()
        self._save_timer.start()

    def _tracking_changed(self, *_args) -> None:
        self._apply_tracking(self._tracking_config())

    def _set_draw_mode(self, mode: str, on: bool) -> None:
        other = self.zone_btn if mode == "crop" else self.crop_btn
        if on and other.isChecked():
            other.setChecked(False)
        self.camera.mode = mode if on else "click"
        if on:
            self.cam_status.setText("Drag a rectangle on the picture" + (" around the play area." if mode == "crop" else " where camera input should count."))

    def _rect_drawn(self, x0: float, y0: float, x1: float, y1: float) -> None:
        cfg = self._tracking_config()
        if self.camera.mode == "crop":
            cfg.crop = [x0, y0, x1, y1]
            self.crop_btn.setChecked(False)
            self.cam_status.setText(f"Tracking area set to {x0:.0%}-{x1:.0%} × {y0:.0%}-{y1:.0%} of the picture. Positions are now -1..1 inside it.")
        elif self.camera.mode == "zone":
            cfg.zone = [x0, y0, x1, y1]
            cfg.zone_enabled = True
            self.zone_btn.setChecked(False)
            self.cam_status.setText("Trigger zone set: camera bindings only fire while the sphere is inside the green box (track.in_zone).")
        self._apply_tracking(cfg)

    def _clear_areas(self) -> None:
        cfg = self._tracking_config()
        cfg.crop = [0.0, 0.0, 1.0, 1.0]
        cfg.zone = [0.0, 0.0, 1.0, 1.0]
        cfg.zone_enabled = False
        self._apply_tracking(cfg)
        self.cam_status.setText("Tracking area and trigger zone cleared (whole picture).")

    def _calibrate_tracking(self) -> None:
        dev = self.ctx.runtime.devices
        if dev.camera is None:
            self.cam_status.setText("No camera open.")
            return
        self.calibrate_btn.setEnabled(False)
        self.cam_status.setText("Calibrating… keep both controllers lit and still for a second.")
        w = Worker(lambda signals: dev.calibrate_tracking())
        w.signals.finished.connect(self._on_calibrated)
        w.signals.error.connect(lambda e: self._on_calibrated("Calibration failed: " + e.split("\n")[0]))
        run_in_background(w)

    def _on_calibrated(self, report: str) -> None:
        self.calibrate_btn.setEnabled(True)
        self._sync_tracking_widgets()
        self._update_swatches()
        p = self.ctx.profile.copy()
        for i in range(min(2, len(p.controllers))):
            p.controllers[i].color = list(self.ctx.settings.controller_colors[i])
        self.ctx.apply_profile(p, reason="color")
        save_settings(self.ctx.settings)
        self.cam_status.setText(report)

    def _reconnect_camera(self) -> None:
        s = self.ctx.settings
        s.camera_backend = self.cam_backend.currentText()
        s.camera_index = self.cam_index.value()
        s.camera_mirror = self.mirror.isChecked()
        self.ctx.runtime.devices.tracking.mirror = s.camera_mirror
        save_settings(s)
        self.ctx.runtime.devices.open_camera()

    # ----------------------------------------------------------- refresh
    def refresh(self) -> None:
        dev = self.ctx.runtime.devices
        lines = []
        for i, c in enumerate(dev.controllers):
            st = c.state
            if st.model == "simulated":
                lines.append(f"Controller {i + 1}: — none —")
                continue
            batt = "charging" if st.charging else f"battery {int(st.battery * 100)}%"
            lines.append(f"Controller {i + 1}: PS Move {st.serial} · {batt} · {st.output_status or 'LED/rumble: waiting'}")
        self.ctrl_status.setText("\n".join(lines) or "no controllers")
        import math

        motion = []
        for i, c in enumerate(dev.controllers):
            st = c.state
            motion.append(
                f"{i + 1}: accel ({st.accel.x:+.2f}, {st.accel.y:+.2f}, {st.accel.z:+.2f}) g · "
                f"gyro ({math.degrees(st.gyro.x):+.0f}, {math.degrees(st.gyro.y):+.0f}, {math.degrees(st.gyro.z):+.0f}) °/s · "
                f"roll {st.roll:+.0f}° pitch {st.pitch:+.0f}° yaw {st.yaw:+.0f}°"
            )
        self.motion_lbl.setText("\n".join(motion))
        none_found = dev.real_controller_count() == 0 and self.ctx.settings.controller_backend != "simulated"
        if none_found and not self.install_help.isVisible():
            self.install_help.setText(controller_install_html())
        self.install_help.setVisible(none_found)
        slots = {c.key: i for i, c in enumerate(dev.controllers) if isinstance(c, HidMoveController)}
        if dev.discovered:
            rows = []
            for d in dev.discovered:
                slot = slots.get(d.key)
                rows.append(f"• {d.label}  →  " + (f"slot {slot + 1}" if slot is not None else "not assigned (only 2 slots)"))
            self.detected.setText("\n".join(rows))
        elif self.ctx.settings.controller_backend == "simulated":
            self.detected.setText("Backend is 'simulated' (Advanced → Controller backend) so real controllers are ignored.")
        else:
            self.detected.setText("No PS Move found yet. Press its PS button; The Mover rescans every 3 s.")
        self.camera.show_frame(dev.latest_frame(mask=self.mask_btn.isChecked()))
        if dev.camera is not None and not self.cam_status.text().startswith(("Controller", "Depth", "Drag", "Tracking", "Trigger", "Calibrat", "Found", "No bright")):
            parts = [f"{dev.camera.source.name} · {dev.camera.fps:.0f} fps · tracker {dev.tracker.process_ms:.1f} ms"]
            for i, det in enumerate(dev.tracker.last_detections):
                parts.append(f"P{i + 1}: " + (f"r={det.radius:.0f}px hue±{det.hue_error:.0f}" + ("" if det.in_zone else " outside zone") if det else "not seen"))
            self.cam_status.setText(" · ".join(parts))
