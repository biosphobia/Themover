"""Readiness checklist shown on the Play tab: tells the player what is missing."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from themover.ui.widgets import WrapLabel

from themover.devices.psmove import HidMoveController
from themover.links import VIGEMBUS_URL, controller_install_html, link
from themover.mapping.profile import Profile
from themover.ui.context import AppContext

OK, WARN, BAD = "ok", "warn", "bad"
ICON = {OK: "✔", WARN: "▲", BAD: "✖"}
COLOR = {OK: "#4ade80", WARN: "#fbbf24", BAD: "#f87171"}


@dataclass
class Item:
    level: str
    text: str


def profile_needs_camera(profile: Profile) -> bool:
    return any(b.enabled and (".track." in b.source or b.source.startswith("wheel.") or b.source.startswith("both.")) for b in profile.bindings)


def profile_needs_gamepad(profile: Profile) -> bool:
    return any(b.enabled and b.target.startswith("gamepad.") for b in profile.bindings)


def profile_uses_hand(profile: Profile, index: int) -> bool:
    return any(b.enabled and b.source.startswith(f"c{index}.") for b in profile.bindings) or profile_needs_camera(profile)


def build_items(ctx: AppContext) -> list[Item]:
    items: list[Item] = []
    dev = ctx.runtime.devices
    profile = ctx.profile
    for i in range(2):
        ctrl = dev.controllers[i] if i < len(dev.controllers) else None
        real = isinstance(ctrl, HidMoveController) and ctrl.state.connected
        hand = "right hand" if i == 0 else "left hand"
        if real:
            st = ctrl.state
            extra = f" · {st.output_status}" if st.output_status and "NOT" in st.output_status else ""
            items.append(Item(WARN if extra else OK, f"Controller {i + 1} ({hand}): PS Move {st.serial}{extra}"))
        elif ctx.settings.controller_backend == "simulated":
            items.append(Item(WARN, f"Controller {i + 1}: simulated (backend set to 'simulated' in Setup)"))
        elif profile_uses_hand(profile, i):
            items.append(Item(BAD, f"Controller {i + 1} ({hand}): " + controller_install_html()))
        else:
            items.append(Item(OK, f"Controller {i + 1}: not needed by this profile"))
    cam = dev.camera
    if cam is None:
        items.append(Item(BAD if profile_needs_camera(profile) else OK, "Camera: not open"))
    elif "synthetic" in cam.source.name:
        if profile_needs_camera(profile):
            items.append(Item(BAD, "Camera: none found and this profile uses camera tracking (wheel / pointing). Plug in the PS3 Eye or pick a camera in Setup."))
        else:
            items.append(Item(OK, "Camera: not needed by this profile (none found)"))
    else:
        tracked = sum(1 for c in dev.controllers if c.state.tracker.tracked)
        if profile_needs_camera(profile) and tracked == 0:
            items.append(Item(WARN, f"Camera: {cam.source.name}, but no sphere is tracked. Click a sphere in Setup to set its colour."))
        else:
            items.append(Item(OK, f"Camera: {cam.source.name} ({tracked} sphere{'s' if tracked != 1 else ''} tracked)"))
    if profile_needs_gamepad(profile):
        if ctx.armed:
            desc = ctx.runtime.sink.description
            items.append(Item(BAD if "no virtual gamepad" in desc else OK, f"Virtual gamepad: {desc.split(' + ')[-1]}"))
        elif not ctx.settings.gamepad_enabled:
            items.append(Item(BAD, "Virtual gamepad: disabled in Setup, but this profile uses gamepad buttons"))
        elif sys.platform.startswith("win") and importlib.util.find_spec("vgamepad") is None:
            items.append(Item(BAD, "Virtual gamepad: this profile uses gamepad buttons; install " + link(VIGEMBUS_URL, "ViGEmBus") + " (free driver)"))
        else:
            items.append(Item(OK, "Virtual gamepad: ready (needs the ViGEmBus driver)"))
    if ctx.settings.effective_api_key:
        items.append(Item(OK, "Claude API key: set (AI Coach available)"))
    else:
        items.append(Item(WARN, "Claude API key: not set. Add it in Setup to use the AI Coach (optional)."))
    return items


class Checklist(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self.title = QLabel("Ready to play?")
        self.title.setObjectName("h2")
        self._layout.addWidget(self.title)
        self._labels: list[QLabel] = []
        self.show_all = False

    def update_items(self, items: list[Item]) -> None:
        if not self.show_all:
            problems = [i for i in items if i.level != OK]
            items = problems or [Item(OK, "All set - press PLAY.")]
        while len(self._labels) < len(items):
            lbl = WrapLabel("", muted=False)
            lbl.setTextFormat(Qt.RichText)
            lbl.setOpenExternalLinks(True)
            self._layout.addWidget(lbl)
            self._labels.append(lbl)
        for lbl, item in zip(self._labels, items):
            lbl.setText(f'<span style="color:{COLOR[item.level]}">{ICON[item.level]}</span>  {item.text}')
            lbl.setVisible(True)
        for lbl in self._labels[len(items):]:
            lbl.setVisible(False)
