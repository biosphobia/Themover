"""Main window: header, tabs, status bar, periodic refresh, PS-button play toggle."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QTabWidget, QVBoxLayout, QWidget

from themover import APP_NAME, TAGLINE, __version__
from themover.config import Settings
from themover.ui.ai_tab import AITab
from themover.ui.context import AppContext
from themover.ui.devices_tab import DevicesTab
from themover.ui.mapping_tab import MappingTab
from themover.ui.play_tab import PlayTab
from themover.ui.settings_tab import SettingsTab

log = logging.getLogger(__name__)

PS_HOLD_SECONDS = 0.8


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — {TAGLINE}")
        self.resize(1180, 760)
        self.ctx = AppContext(settings)

        central = QWidget()
        layout = QVBoxLayout(central)
        header = QHBoxLayout()
        title = QLabel(APP_NAME); title.setObjectName("title")
        tagline = QLabel(TAGLINE); tagline.setObjectName("tagline")
        version = QLabel(f"v{__version__}"); version.setObjectName("muted")
        header.addWidget(title); header.addSpacing(12); header.addWidget(tagline); header.addStretch(1); header.addWidget(version)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.play_tab = PlayTab(self.ctx)
        self.mapping_tab = MappingTab(self.ctx)
        self.ai_tab = AITab(self.ctx)
        self.devices_tab = DevicesTab(self.ctx)
        self.settings_tab = SettingsTab(self.ctx, on_api_changed=self.ai_tab.reset_client)
        self.tabs.addTab(self.play_tab, "▶ Play")
        self.tabs.addTab(self.mapping_tab, "Mapping")
        self.tabs.addTab(self.ai_tab, "✨ AI Coach")
        self.tabs.addTab(self.devices_tab, "Devices")
        self.tabs.addTab(self.settings_tab, "Settings")
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.status_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.ctx.status.connect(self._set_status)

        # Load last profile and start the engine (devices + camera) in preview mode.
        self.ctx.load_profile_key(settings.last_profile or "generic_gamepad")
        if not self.ctx.profile.bindings:
            self.ctx.load_profile_key("generic_gamepad")
        self.ctx.runtime.start()
        if not settings.effective_api_key:
            self._set_status("Welcome! Add your Anthropic API key in Settings to unlock the AI Coach. Everything else works without it.")

        self._ps_down_since = 0.0
        self._ps_toggled = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(50)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _refresh(self) -> None:
        idx = self.tabs.currentIndex()
        if idx == 0:
            self.play_tab.refresh()
        elif idx == 3:
            self.devices_tab.refresh()
        self._check_ps_button()

    def _check_ps_button(self) -> None:
        world = self.ctx.runtime.world
        if not world or not world.controllers:
            return
        ps = world.controllers[0].buttons.get("ps", False)
        now = time.monotonic()
        if ps:
            if not self._ps_down_since:
                self._ps_down_since = now
            elif not self._ps_toggled and now - self._ps_down_since >= PS_HOLD_SECONDS:
                self._ps_toggled = True
                self.ctx.set_armed(not self.ctx.armed)
        else:
            self._ps_down_since = 0.0
            self._ps_toggled = False

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        try:
            self.ctx.shutdown()
        except Exception as exc:
            log.warning("shutdown: %s", exc)
        super().closeEvent(event)
