"""Devices: controller detection, camera, colours, calibration, tests."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget, QCheckBox,
)
from PySide6.QtGui import QColor

from themover.config import save_settings
from themover.devices.psmove import HidMoveController
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

        # ------------------------------------------------ detected list
        det = QFrame(); det.setObjectName("card"); dl = QVBoxLayout(det)
        dt = QLabel("Detected PS Move controllers"); dt.setObjectName("h2"); dl.addWidget(dt)
        info = QLabel("Pair your controllers with PSMoveServiceEx (its pairing tool writes the PC's Bluetooth address into the "
                      "controller). Once Windows lists them as 'Motion Controller', The Mover picks them up automatically, even while "
                      "it is running. Close PSMoveService itself while playing so it does not fight over the LEDs and rumble. "
                      "Each controller keeps its slot (1 = right hand, 2 = left hand); use Swap if they come up the wrong way round.")
        info.setObjectName("muted"); info.setWordWrap(True); dl.addWidget(info)
        self.detected = QLabel("scanning…"); self.detected.setWordWrap(True); dl.addWidget(self.detected)
        drow2 = QHBoxLayout()
        self.swap_btn = QPushButton("Swap 1 ↔ 2"); self.forget_btn = QPushButton("Forget slot assignment")
        self.identify_btns = [QPushButton("Identify 1"), QPushButton("Identify 2")]
        drow2.addWidget(self.swap_btn); drow2.addWidget(self.forget_btn)
        for b in self.identify_btns:
            drow2.addWidget(b)
        drow2.addStretch(1)
        dl.addLayout(drow2)
        left.addWidget(det)
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
        self.swap_btn.clicked.connect(self._swap)
        self.forget_btn.clicked.connect(self._forget)
        for i, b in enumerate(self.identify_btns):
            b.clicked.connect(lambda _=False, i=i: self.ctx.buzz(i, 1.0, (255, 255, 255), 600))
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
        self.ctx.runtime.devices.rescan(force=True)
        save_settings(self.ctx.settings)
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

    def _swap(self) -> None:
        self.ctx.runtime.devices.swap_controllers()
        save_settings(self.ctx.settings)
        self.refresh()

    def _forget(self) -> None:
        self.ctx.runtime.devices.forget_assignment()
        save_settings(self.ctx.settings)
        self._rescan()

    # ----------------------------------------------------------- refresh
    def refresh(self) -> None:
        dev = self.ctx.runtime.devices
        lines = []
        for i, c in enumerate(dev.controllers):
            st = c.state
            kind = "simulated (no controller in this slot)" if st.model == "simulated" else f"PS Move {st.model.upper()} {st.serial}"
            live = f"trigger {st.trigger:.2f} · roll {st.roll:+.0f}° · pitch {st.pitch:+.0f}°"
            lines.append(f"Controller {i + 1}: {kind} · {'charging' if st.charging else f'battery {int(st.battery * 100)}%'} · {'tracked' if st.tracker.tracked else 'not tracked'}\n    {live}")
        self.ctrl_status.setText("\n".join(lines) or "no controllers")
        slots = {c.key: i for i, c in enumerate(dev.controllers) if isinstance(c, HidMoveController)}
        if dev.discovered:
            rows = []
            for d in dev.discovered:
                slot = slots.get(d.key)
                rows.append(f"• {d.label}  →  " + (f"slot {slot + 1}" if slot is not None else "not assigned (only 2 slots)"))
            self.detected.setText("\n".join(rows))
        elif self.ctx.settings.controller_backend == "simulated":
            self.detected.setText("Backend is 'simulated'; switch to 'auto' to use real controllers.")
        else:
            self.detected.setText("No PS Move found yet. Pair it with PSMoveServiceEx, press its PS button and wait a few seconds; The Mover rescans every 3 s.")
        self.camera.show_frame(dev.latest_frame())
        if dev.camera is not None and not self.cam_status.text().startswith(("Controller", "Depth")):
            self.cam_status.setText(f"{dev.camera.source.name} · {dev.camera.fps:.0f} fps")
