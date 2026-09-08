"""Setup: controllers, camera and the Claude API key.  Advanced mode reveals the knobs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from themover.ai.client import ClaudeClient
from themover.config import AVAILABLE_MODELS, save_settings, settings_path
from themover.devices.psmove import HidMoveController
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
        al.addWidget(self.rescan_btn, 0, 0); al.addWidget(self.forget_btn, 0, 1)
        al.addWidget(QLabel("Controller backend"), 1, 0); al.addWidget(self.backend, 1, 1)
        al.addWidget(QLabel("LED / rumble method"), 2, 0); al.addWidget(self.led_method, 2, 1)
        al.addWidget(self.recenter_btn, 3, 0, 1, 2)
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
        hint = WrapLabel("Only some profiles use the camera. If a sphere is not tracked, click it in the picture.")
        ccl.addWidget(hint)
        crow = QHBoxLayout()
        crow.addWidget(QLabel("Clicking sets the colour of"))
        self.sample_target = QComboBox(); self.sample_target.addItems(["controller 1", "controller 2"]); crow.addWidget(self.sample_target)
        self.color_btns = []
        for i in range(2):
            cb = QPushButton(f"pick colour {i + 1}"); cb.setMinimumHeight(30); self.color_btns.append(cb); crow.addWidget(cb)
            self._advanced_widgets.append(cb)
        crow.addStretch(1)
        ccl.addLayout(crow)
        adv = QWidget(); cf = QFormLayout(adv); cf.setContentsMargins(0, 6, 0, 0)
        self.cam_backend = QComboBox(); self.cam_backend.addItems(["auto", "pseye", "opencv", "synthetic"]); self.cam_backend.setCurrentText(s.camera_backend)
        self.cam_index = QSpinBox(); self.cam_index.setRange(0, 9); self.cam_index.setValue(s.camera_index)
        self.mirror = QCheckBox("mirror (camera faces you)"); self.mirror.setChecked(s.camera_mirror)
        self.reconnect_btn = QPushButton("Reconnect camera")
        srow = QHBoxLayout(); srow.addWidget(self.cam_backend); srow.addWidget(QLabel("index")); srow.addWidget(self.cam_index); srow.addWidget(self.reconnect_btn)
        cf.addRow("Source", srow)
        cf.addRow("", self.mirror)
        drow = QHBoxLayout()
        self.near_btn = QPushButton("Set NEAR (stand close)"); self.far_btn = QPushButton("Set FAR (stand back)")
        drow.addWidget(self.near_btn); drow.addWidget(self.far_btn)
        cf.addRow("Depth", drow)
        ccl.addWidget(adv); self._advanced_widgets.append(adv)
        self.cam_status = WrapLabel(""); ccl.addWidget(self.cam_status)
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
        if which == "near":
            tracker.depth.radius_near = max(st.radius, tracker.depth.radius_far + 2)
        else:
            tracker.depth.radius_far = min(st.radius, tracker.depth.radius_near - 2)
        self.cam_status.setText(f"Depth calibration: far={tracker.depth.radius_far:.0f}px near={tracker.depth.radius_near:.0f}px")

    def _reconnect_camera(self) -> None:
        s = self.ctx.settings
        s.camera_backend = self.cam_backend.currentText()
        s.camera_index = self.cam_index.value()
        s.camera_mirror = self.mirror.isChecked()
        self.ctx.runtime.devices.tracker.mirror = s.camera_mirror
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
        self.camera.show_frame(dev.latest_frame())
        if dev.camera is not None and not self.cam_status.text().startswith(("Controller", "Depth")):
            self.cam_status.setText(f"{dev.camera.source.name} · {dev.camera.fps:.0f} fps")
