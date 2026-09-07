"""Devices: controllers, pairing, camera, colours, calibration, tests."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget, QCheckBox,
)
from PySide6.QtGui import QColor

from themover.config import save_settings
from themover.devices.psmove import HidMoveController, enumerate_controllers, host_bluetooth_address
from themover.ui.camera_view import CameraView
from themover.ui.context import AppContext


class DevicesTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        # ---------------------------------------------------- controllers
        card = QFrame(); card.setObjectName("card"); cl = QVBoxLayout(card)
        t = QLabel("Controllers"); t.setObjectName("h2"); cl.addWidget(t)
        self.ctrl_status = QLabel("…"); self.ctrl_status.setWordWrap(True); cl.addWidget(self.ctrl_status)
        row = QHBoxLayout()
        self.rescan_btn = QPushButton("Rescan controllers")
        self.backend = QComboBox(); self.backend.addItems(["auto", "hid", "simulated"]); self.backend.setCurrentText(ctx.settings.controller_backend)
        row.addWidget(self.rescan_btn); row.addWidget(QLabel("Backend")); row.addWidget(self.backend); row.addStretch(1)
        cl.addLayout(row)
        grid = QGridLayout()
        self.color_btns = []
        self.test_btns = []
        for i in range(2):
            grid.addWidget(QLabel(f"Controller {i + 1} sphere colour"), i, 0)
            cb = QPushButton("pick"); cb.setFixedWidth(70); cb.setMinimumHeight(30); self.color_btns.append(cb); grid.addWidget(cb, i, 1)
            tb = QPushButton("test rumble + flash"); tb.setMinimumHeight(30); self.test_btns.append(tb); grid.addWidget(tb, i, 2)
        cl.addLayout(grid)
        self.recenter_btn = QPushButton("Re-centre yaw (point both at the screen, then click)")
        cl.addWidget(self.recenter_btn)
        left.addWidget(card)

        # -------------------------------------------------------- pairing
        pair = QFrame(); pair.setObjectName("card"); pl = QVBoxLayout(pair)
        pt = QLabel("Pair a controller over Bluetooth"); pt.setObjectName("h2"); pl.addWidget(pt)
        info = QLabel("Plug the controller in with a mini-USB cable, click Pair, unplug, then press its PS button. "
                      "The controller remembers this PC's Bluetooth address and connects on its own next time.")
        info.setObjectName("muted"); info.setWordWrap(True); pl.addWidget(info)
        prow = QHBoxLayout()
        self.host_addr = QLineEdit(host_bluetooth_address() or ""); self.host_addr.setPlaceholderText("this PC's Bluetooth address (aa:bb:cc:dd:ee:ff)")
        self.pair_btn = QPushButton("Pair via USB")
        prow.addWidget(self.host_addr, 1); prow.addWidget(self.pair_btn)
        pl.addLayout(prow)
        self.pair_status = QLabel(""); self.pair_status.setWordWrap(True); pl.addWidget(self.pair_status)
        left.addWidget(pair)
        left.addStretch(1)

        # --------------------------------------------------------- camera
        cam = QFrame(); cam.setObjectName("card"); ccl = QVBoxLayout(cam)
        ct = QLabel("Camera (PS3 Eye or any webcam)"); ct.setObjectName("h2"); ccl.addWidget(ct)
        self.camera = CameraView(); ccl.addWidget(self.camera, 1)
        hint = QLabel("Click a glowing sphere in the picture to teach the tracker its colour (controller 1 first, then 2)."); hint.setObjectName("muted"); hint.setWordWrap(True)
        ccl.addWidget(hint)
        form = QFormLayout()
        self.cam_backend = QComboBox(); self.cam_backend.addItems(["auto", "pseye", "opencv", "synthetic"]); self.cam_backend.setCurrentText(ctx.settings.camera_backend)
        self.cam_index = QSpinBox(); self.cam_index.setRange(0, 9); self.cam_index.setValue(ctx.settings.camera_index)
        self.mirror = QCheckBox("mirror (camera faces you)"); self.mirror.setChecked(ctx.settings.camera_mirror)
        self.sample_target = QComboBox(); self.sample_target.addItems(["controller 1", "controller 2"])
        crow = QHBoxLayout(); crow.addWidget(self.cam_backend); crow.addWidget(QLabel("index")); crow.addWidget(self.cam_index); crow.addWidget(self.mirror)
        self.reconnect_btn = QPushButton("Reconnect camera"); crow.addWidget(self.reconnect_btn)
        form.addRow("Source", crow)
        form.addRow("Click sets colour of", self.sample_target)
        drow = QHBoxLayout()
        self.near_btn = QPushButton("Set NEAR (stand close, click)"); self.far_btn = QPushButton("Set FAR (stand back, click)")
        drow.addWidget(self.near_btn); drow.addWidget(self.far_btn)
        form.addRow("Depth calibration", drow)
        ccl.addLayout(form)
        self.cam_status = QLabel(""); self.cam_status.setObjectName("muted"); self.cam_status.setWordWrap(True); ccl.addWidget(self.cam_status)
        right.addWidget(cam)

        self.rescan_btn.clicked.connect(self._rescan)
        self.backend.currentTextChanged.connect(self._backend_changed)
        for i, b in enumerate(self.color_btns):
            b.clicked.connect(lambda _=False, i=i: self._pick_color(i))
        for i, b in enumerate(self.test_btns):
            b.clicked.connect(lambda _=False, i=i: self.ctx.buzz(i, 1.0, (255, 255, 255), 400))
        self.recenter_btn.clicked.connect(lambda: self.ctx.runtime.devices.reset_yaw())
        self.pair_btn.clicked.connect(self._pair)
        self.reconnect_btn.clicked.connect(self._reconnect_camera)
        self.camera.clicked.connect(self._sample_color)
        self.near_btn.clicked.connect(lambda: self._set_depth("near"))
        self.far_btn.clicked.connect(lambda: self._set_depth("far"))
        self._update_swatches()

    # ------------------------------------------------------------- slots
    def _update_swatches(self) -> None:
        for i, b in enumerate(self.color_btns):
            r, g, bl = self.ctx.settings.controller_colors[i]
            b.setStyleSheet(f"background: rgb({r},{g},{bl}); color: {'#000' if (r + g + bl) > 380 else '#fff'};")

    def _rescan(self) -> None:
        self.ctx.runtime.devices.open_controllers()
        self.refresh()

    def _backend_changed(self, text: str) -> None:
        self.ctx.settings.controller_backend = text
        save_settings(self.ctx.settings)

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

    def _pair(self) -> None:
        host = self.host_addr.text().strip()
        if not host:
            QMessageBox.warning(self, "Bluetooth address", "Enter this PC's Bluetooth adapter address first (Settings → Bluetooth → adapter properties).")
            return
        usb = [d for d in enumerate_controllers() if d.interface == "usb"]
        if not usb:
            self.pair_status.setText("No controller found over USB. Plug it in with a mini-USB cable and try again.")
            return
        results = []
        for d in usb:
            ctrl = HidMoveController(9, d.path, d.model, d.serial)
            try:
                ctrl.open()
                before = ctrl.read_bt_addresses()
                ctrl.set_host_address(host)
                after = ctrl.read_bt_addresses()
                results.append(f"{d.model.upper()} {after[0]}: host {before[1]} → {after[1]}")
            except Exception as exc:
                results.append(f"{d.model.upper()}: failed ({exc})")
            finally:
                ctrl.close()
        self.pair_status.setText("\n".join(results) + "\n\nNow unplug the controller and press its PS button. On Windows, if it does not connect within ~10 s, open Bluetooth settings and remove/re-add 'Motion Controller'.")

    # ----------------------------------------------------------- refresh
    def refresh(self) -> None:
        dev = self.ctx.runtime.devices
        lines = []
        for i, c in enumerate(dev.controllers):
            st = c.state
            kind = "simulated" if st.model == "simulated" else f"PS Move {st.model.upper()} {st.serial}"
            lines.append(f"Controller {i + 1}: {kind} · {'charging' if st.charging else f'battery {int(st.battery * 100)}%'} · {'tracked' if st.tracker.tracked else 'not tracked'}")
        self.ctrl_status.setText("\n".join(lines) or "no controllers")
        self.camera.show_frame(dev.latest_frame())
        if dev.camera is not None and not self.cam_status.text().startswith(("Controller", "Depth")):
            self.cam_status.setText(f"{dev.camera.source.name} · {dev.camera.fps:.0f} fps")
