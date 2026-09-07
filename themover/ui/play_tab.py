"""Home screen: pick a profile, check readiness, press Play."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from themover.profiles import list_profiles
from themover.ui.camera_view import CameraView
from themover.ui.checklist import Checklist, build_items
from themover.ui.context import AppContext
from themover.ui.controller_card import ControllerCard
from themover.ui.widgets import WrapLabel


class PlayTab(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        pick = QHBoxLayout()
        pick.addWidget(QLabel("Game profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(260)
        pick.addWidget(self.profile_combo, 1)
        left.addLayout(pick)

        self.play_btn = QPushButton("▶  PLAY")
        self.play_btn.setObjectName("primary")
        self.play_btn.setCheckable(True)
        self.play_btn.setMinimumHeight(56)
        left.addWidget(self.play_btn)
        self.hint = WrapLabel("Press PLAY, switch to your game. Hold the PS button for a second to pause / resume.")
        left.addWidget(self.hint)

        self.style_box = QTextEdit()
        self.style_box.setReadOnly(True)
        self.style_box.setPlaceholderText("How to play with this profile…")
        self.style_box.setMaximumHeight(130)
        left.addWidget(self.style_box)

        self.checklist = Checklist()
        left.addWidget(self.checklist)

        cards = QHBoxLayout()
        self.cards = [ControllerCard(0), ControllerCard(1)]
        for c in self.cards:
            cards.addWidget(c)
        left.addLayout(cards)
        self.active = QLabel("")
        self.active.setObjectName("muted")
        self.active.setWordWrap(True)
        left.addWidget(self.active)
        left.addStretch(1)
        root.addLayout(left, 3)

        right = QVBoxLayout()
        cam_title = QLabel("Camera")
        cam_title.setObjectName("h2")
        right.addWidget(cam_title)
        self.camera = CameraView()
        self.camera.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.camera, 1)
        self.cam_status = QLabel("")
        self.cam_status.setObjectName("muted")
        right.addWidget(self.cam_status)
        self.output_status = QLabel("")
        self.output_status.setObjectName("muted")
        self.output_status.setWordWrap(True)
        right.addWidget(self.output_status)
        root.addLayout(right, 2)

        self.reload_profiles()
        self.profile_combo.currentIndexChanged.connect(self._on_pick)
        self.play_btn.toggled.connect(self._on_play)
        ctx.profile_changed.connect(self._on_profile_changed)
        ctx.armed_changed.connect(self._on_armed)
        ctx.advanced_changed.connect(self.set_advanced)
        self.set_advanced(ctx.advanced)
        self._tick = 0

    def set_advanced(self, on: bool) -> None:
        for c in self.cards:
            c.set_advanced(on)
        self.active.setVisible(on)
        self.output_status.setVisible(on)
        self.cam_status.setVisible(on)
        self.checklist.show_all = on
        self.checklist.update_items(build_items(self.ctx))

    # ---------------------------------------------------------------- slots
    def reload_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self._keys: list[str] = []
        for key, p in list_profiles().items():
            label = f"{p.name}  —  {p.game}" if p.game else p.name
            if key.startswith("user:"):
                label = "★ " + label
            self.profile_combo.addItem(label, key)
            self._keys.append(key)
        if self.ctx.profile_key in self._keys:
            self.profile_combo.setCurrentIndex(self._keys.index(self.ctx.profile_key))
        self.profile_combo.blockSignals(False)

    def _on_pick(self, index: int) -> None:
        key = self.profile_combo.itemData(index)
        if key and key != self.ctx.profile_key:
            self.ctx.load_profile_key(key)

    def _on_play(self, checked: bool) -> None:
        if checked != self.ctx.armed:
            self.ctx.set_armed(checked)

    def _on_armed(self, armed: bool) -> None:
        self.play_btn.blockSignals(True)
        self.play_btn.setChecked(armed)
        self.play_btn.setText("■  PLAYING  (click to pause)" if armed else "▶  PLAY")
        self.play_btn.blockSignals(False)
        self.output_status.setText(f"Output: {self.ctx.runtime.sink.description}" if armed else "Output: paused")

    def _on_profile_changed(self, profile, reason: str) -> None:
        text = profile.play_style or profile.description
        self.style_box.setPlainText(f"{profile.name}\n\n{text}")
        if reason in ("saved", "analysis") or self.ctx.profile_key not in getattr(self, "_keys", []):
            self.reload_profiles()
        if self.ctx.profile_key in getattr(self, "_keys", []):
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(self._keys.index(self.ctx.profile_key))
            self.profile_combo.blockSignals(False)

    # -------------------------------------------------------------- refresh
    def refresh(self) -> None:
        rt = self.ctx.runtime
        world = rt.world
        for i, card in enumerate(self.cards):
            card.update_state(world.controllers[i] if world and i < len(world.controllers) else None)
        self.camera.show_frame(rt.devices.latest_frame())
        cam = rt.devices.camera
        if cam is not None:
            self.cam_status.setText(f"{cam.source.name} · {cam.fps:.0f} fps · wheel {rt.devices.wheel_angle:+.0f}° ({rt.devices.wheel_source})")
        targets = sorted(rt.engine.stats.active_targets)
        self.active.setText(("active: " + ", ".join(targets)) if targets else "")
        self._tick += 1
        if self._tick % 10 == 0:  # checklist twice a second is plenty
            self.checklist.update_items(build_items(self.ctx))
