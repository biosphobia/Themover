"""Objects shared by every tab: settings, runtime, active profile."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

from themover.config import Settings, save_settings
from themover.mapping.profile import Profile
from themover.mapping.runtime import Runtime
from themover.profiles import load_profile, save_profile

log = logging.getLogger(__name__)


class AppContext(QObject):
    profile_changed = Signal(object, str)  # Profile, reason
    status = Signal(str)
    armed_changed = Signal(bool)
    advanced_changed = Signal(bool)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.runtime = Runtime(settings)
        self.runtime.devices.on_status = lambda text: self.status.emit(text)
        self.profile: Profile = Profile()
        self.profile_key: str = ""
        self.chat = None  # CoachChat, created by the AI tab

    # ---------------------------------------------------------- profiles
    def load_profile_key(self, key: str) -> None:
        try:
            profile = load_profile(key)
        except Exception as exc:
            self.status.emit(f"Could not load profile {key}: {exc}")
            return
        self.profile_key = key
        self.settings.last_profile = key
        save_settings(self.settings)
        self.apply_profile(profile, reason="loaded")

    def apply_profile(self, profile: Profile, reason: str = "edited") -> None:
        self.profile = profile
        self.runtime.set_profile(profile)
        self.profile_changed.emit(profile, reason)

    def save_current(self, name: Optional[str] = None) -> str:
        path = save_profile(self.profile, name or self.profile.name)
        self.profile_key = f"user:{path.stem}"
        self.settings.last_profile = self.profile_key
        save_settings(self.settings)
        self.status.emit(f"Saved profile to {path}")
        return str(path)

    # ---------------------------------------------------------- advanced
    @property
    def advanced(self) -> bool:
        return bool(self.settings.advanced_mode)

    def set_advanced(self, on: bool) -> None:
        self.settings.advanced_mode = bool(on)
        save_settings(self.settings)
        self.advanced_changed.emit(bool(on))

    # ------------------------------------------------------------- engine
    def set_armed(self, armed: bool) -> None:
        if armed:
            self.runtime.arm()
        else:
            self.runtime.disarm()
        self.armed_changed.emit(self.runtime.armed)
        self.status.emit("Playing - inputs are being sent to the game" if armed else "Paused - nothing is sent to the game")

    @property
    def armed(self) -> bool:
        return self.runtime.armed

    def live_signals(self) -> dict[str, float]:
        return self.runtime.signal_snapshot()

    def buzz(self, controller: int, rumble: float, led, duration_ms: int) -> None:
        self.runtime.buzz(int(controller), float(rumble), tuple(led) if led else None, int(duration_ms))

    def shutdown(self) -> None:
        save_settings(self.settings)
        self.runtime.stop()
