"""Settings: API key, model, output backends, recording options."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from themover.ai.client import ClaudeClient
from themover.config import AVAILABLE_MODELS, save_settings, settings_path
from themover.ui.context import AppContext
from themover.ui.workers import Worker, run_in_background


class SettingsTab(QWidget):
    def __init__(self, ctx: AppContext, on_api_changed=None) -> None:
        super().__init__()
        self.ctx = ctx
        self.on_api_changed = on_api_changed
        s = ctx.settings
        root = QVBoxLayout(self)

        card = QFrame(); card.setObjectName("card"); form = QFormLayout(card)
        t = QLabel("Claude"); t.setObjectName("h2"); form.addRow(t)
        krow = QHBoxLayout()
        self.api_key = QLineEdit(s.api_key); self.api_key.setEchoMode(QLineEdit.Password); self.api_key.setPlaceholderText("sk-ant-…  (get one at console.anthropic.com)")
        self.show_key = QPushButton("show"); self.show_key.setCheckable(True); self.show_key.setFixedWidth(60)
        self.test_btn = QPushButton("Test key")
        krow.addWidget(self.api_key, 1); krow.addWidget(self.show_key); krow.addWidget(self.test_btn)
        form.addRow("API key", krow)
        self.model = QComboBox(); self.model.setEditable(True); self.model.addItems(AVAILABLE_MODELS); self.model.setCurrentText(s.model)
        form.addRow("Model", self.model)
        self.effort = QComboBox(); self.effort.addItems(["low", "medium", "high", "xhigh", "max"]); self.effort.setCurrentText(s.effort)
        form.addRow("Effort", self.effort)
        self.fallback = QCheckBox("If Claude declines a request, automatically retry on a fallback model"); self.fallback.setChecked(s.refusal_fallback)
        form.addRow("", self.fallback)
        self.test_status = QLabel(""); self.test_status.setObjectName("muted"); form.addRow("", self.test_status)
        root.addWidget(card)

        card2 = QFrame(); card2.setObjectName("card"); form2 = QFormLayout(card2)
        t2 = QLabel("Game output"); t2.setObjectName("h2"); form2.addRow(t2)
        self.output_backend = QComboBox(); self.output_backend.addItems(["auto", "sendinput", "pynput"]); self.output_backend.setCurrentText(s.output_backend)
        form2.addRow("Keyboard/mouse backend", self.output_backend)
        self.gamepad = QCheckBox("Virtual Xbox 360 pad (needs the ViGEmBus driver)"); self.gamepad.setChecked(s.gamepad_enabled)
        form2.addRow("", self.gamepad)
        self.tick = QSpinBox(); self.tick.setRange(30, 250); self.tick.setValue(s.tick_hz); self.tick.setSuffix(" Hz")
        form2.addRow("Engine rate", self.tick)
        root.addWidget(card2)

        card3 = QFrame(); card3.setObjectName("card"); form3 = QFormLayout(card3)
        t3 = QLabel("Recording"); t3.setObjectName("h2"); form3.addRow(t3)
        self.rec_seconds = QSpinBox(); self.rec_seconds.setRange(10, 300); self.rec_seconds.setValue(s.record_seconds); self.rec_seconds.setSuffix(" s")
        self.rec_fps = QDoubleSpinBox(); self.rec_fps.setRange(0.2, 5.0); self.rec_fps.setSingleStep(0.2); self.rec_fps.setValue(s.record_fps); self.rec_fps.setSuffix(" fps")
        self.max_frames = QSpinBox(); self.max_frames.setRange(1, 40); self.max_frames.setValue(s.max_frames_to_send)
        form3.addRow("Default length", self.rec_seconds)
        form3.addRow("Screenshot rate", self.rec_fps)
        form3.addRow("Screenshots sent to Claude", self.max_frames)
        root.addWidget(card3)

        brow = QHBoxLayout()
        self.save_btn = QPushButton("Save settings"); self.save_btn.setObjectName("primary")
        self.path_lbl = QLabel(f"stored in {settings_path()}"); self.path_lbl.setObjectName("muted")
        brow.addWidget(self.save_btn); brow.addWidget(self.path_lbl, 1)
        root.addLayout(brow)
        root.addStretch(1)

        self.show_key.toggled.connect(lambda on: self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.save_btn.clicked.connect(self.save)
        self.test_btn.clicked.connect(self._test)

    def save(self) -> None:
        s = self.ctx.settings
        s.api_key = self.api_key.text().strip()
        s.model = self.model.currentText().strip()
        s.effort = self.effort.currentText()
        s.refusal_fallback = self.fallback.isChecked()
        s.output_backend = self.output_backend.currentText()
        s.gamepad_enabled = self.gamepad.isChecked()
        s.tick_hz = self.tick.value()
        s.record_seconds = self.rec_seconds.value()
        s.record_fps = self.rec_fps.value()
        s.max_frames_to_send = self.max_frames.value()
        save_settings(s)
        self.test_status.setText("Saved.")
        if self.on_api_changed:
            self.on_api_changed()

    def _test(self) -> None:
        self.save()
        self.test_status.setText("Testing…")
        client = ClaudeClient(self.ctx.settings)

        def job(signals):
            return client.test_connection()

        w = Worker(job)
        w.signals.finished.connect(self._on_test_ok)
        w.signals.error.connect(self._on_test_error)
        run_in_background(w)

    def _on_test_ok(self, text: str) -> None:
        self.test_status.setText(f"Claude says: {text}")

    def _on_test_error(self, err: str) -> None:
        self.test_status.setText("Failed: " + err.split("\n")[0])
