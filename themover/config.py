"""Persistent user settings (API key, model, device preferences).

Settings live in a JSON file inside the per-user application data folder so the
packaged executable can be dropped anywhere and still remember its configuration.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-5"
AVAILABLE_MODELS = [
    "claude-opus-5",
    "claude-fable-5-1",
    "claude-sonnet-5",
]


def app_data_dir() -> Path:
    """Return (and create) the folder where The Mover stores its settings."""
    override = os.environ.get("THEMOVER_HOME")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home())) / "TheMover"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "TheMover"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "themover"
    base.mkdir(parents=True, exist_ok=True)
    (base / "profiles").mkdir(exist_ok=True)
    (base / "recordings").mkdir(exist_ok=True)
    return base


@dataclass
class Settings:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    refusal_fallback: bool = True
    effort: str = "high"

    # Devices
    camera_backend: str = "auto"  # auto | pseye | opencv | synthetic
    camera_index: int = 0
    camera_mirror: bool = True
    tracking: dict = field(default_factory=dict)  # TrackingConfig (crop, trigger zone, thresholds, exposure...)
    controller_colors: list[list[int]] = field(
        default_factory=lambda: [[255, 0, 255], [0, 255, 255]]
    )
    controller_backend: str = "auto"  # auto | hid | simulated
    controller_serials: list[str] = field(default_factory=lambda: ["", ""])  # remembered slot assignment
    led_method: str = "auto"  # auto | write | control  (how LED/rumble reports are sent)

    # Engine
    tick_hz: int = 100
    output_backend: str = "auto"  # auto | sendinput | pynput
    gamepad_enabled: bool = True
    last_profile: str = "user:generic_gamepad"

    # Recording
    record_seconds: int = 45
    record_fps: float = 1.0
    max_frames_to_send: int = 12

    # UI
    start_minimized: bool = False
    advanced_mode: bool = False

    # Updates (straight from GitHub pushes, no rebuild needed)
    update_owner: str = "biosphobia"
    update_repo: str = "Themover"
    update_branch: str = ""  # "" = the branch this build came from
    check_updates_on_start: bool = True
    github_token: str = ""  # only needed for private repositories

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**clean)

    @property
    def effective_api_key(self) -> str:
        return self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings() -> Settings:
    path = settings_path()
    if not path.exists():
        return Settings()
    try:
        return Settings.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Settings()


def save_settings(settings: Settings) -> Path:
    path = settings_path()
    path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    return path
