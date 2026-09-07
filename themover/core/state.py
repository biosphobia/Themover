"""Controller and camera state shared between the device layer and the engine."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Iterator

BUTTON_NAMES = (
    "square",
    "triangle",
    "cross",
    "circle",
    "move",
    "start",
    "select",
    "ps",
    "trigger_click",
)


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def magnitude(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def __iter__(self) -> Iterator[float]:
        yield self.x
        yield self.y
        yield self.z


@dataclass
class TrackerState:
    """Where the glowing sphere is in the camera image (normalised)."""

    tracked: bool = False
    x: float = 0.0  # -1 (left) .. +1 (right)
    y: float = 0.0  # -1 (bottom) .. +1 (top)
    radius: float = 0.0  # pixels
    depth: float = 0.0  # 0 (far) .. 1 (near)
    vx: float = 0.0  # normalised units / second
    vy: float = 0.0
    last_seen: float = 0.0


@dataclass
class MoveState:
    """Everything The Mover knows about one PS Move controller."""

    index: int
    connected: bool = False
    model: str = "simulated"  # zcm1 | zcm2 | simulated
    serial: str = ""
    battery: float = 0.0  # 0..1 ; 1.0 while charging
    charging: bool = False
    buttons: dict[str, bool] = field(default_factory=lambda: {n: False for n in BUTTON_NAMES})
    trigger: float = 0.0  # 0..1
    accel: Vec3 = field(default_factory=Vec3)  # in g
    gyro: Vec3 = field(default_factory=Vec3)  # rad/s
    mag: Vec3 = field(default_factory=Vec3)
    # Orientation in degrees (from fusion)
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    tracker: TrackerState = field(default_factory=TrackerState)
    led: tuple[int, int, int] = (0, 0, 0)
    rumble: float = 0.0
    output_status: str = ""  # human readable LED/rumble write health
    last_update: float = 0.0

    def button(self, name: str) -> bool:
        return bool(self.buttons.get(name, False))

    def touch(self) -> None:
        self.last_update = time.monotonic()


@dataclass
class WorldState:
    """Snapshot handed to the mapping engine each tick."""

    controllers: list[MoveState]
    t: float = 0.0
    dt: float = 0.01

    def controller(self, index: int) -> MoveState:
        return self.controllers[index]
