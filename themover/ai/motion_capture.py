"""Record full-rate PS Move data (plus optional camera / screen video), tag it, replay it.

A *motion session* is a folder:

    motion.npz      per-controller float32 arrays at the controller's report rate
    events.json     hits / gestures the detectors fired, actions the mapping sent, keyboard/mouse input
    tags.json       the player's tags ("this is where the don / swing / key should have fired")
    meta.json       duration, sources, profile, detector + gesture tuning in use
    camera.mp4      PS3 Eye footage (small: 320x240, <= 20 fps) with camera_ts.json
    screen.mp4      screen footage (640 px wide, <= 10 fps) with screen_ts.json

Tags are free text.  Kinds the app understands are scored automatically by
replaying the recording through the same detectors the game uses:

* ``don`` / ``kat`` (drum hits)            -> :class:`DrumHitDetector` with candidate ``hit_config``
* any gesture name (``swing_left``, ...)  -> :class:`GestureDetector` with candidate sensitivity / cooldown
* an output action (``key.space``, ...)   -> compared with what the mapping actually sent while recording
* ``nothing``                             -> nothing should have fired here (false triggers)

Anything else is a note for the coach.  That makes the same recording +
timeline useful for a rhythm game, a sword game, a racing wheel or a shooter.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Optional

import numpy as np

from themover.config import app_data_dir
from themover.core.gestures import GESTURE_NAMES, GestureConfig, GestureDetector
from themover.core.hits import HIT_CONFIG_FIELDS, DrumHitDetector, HitConfig, hit_config_from_dict, hit_config_to_dict
from themover.core.state import Vec3

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

log = logging.getLogger(__name__)

COLUMNS = ("ax", "ay", "az", "gx", "gy", "gz", "trigger", "move", "roll", "pitch", "yaw", "tx", "ty", "depth", "tracked")
COL = {name: i for i, name in enumerate(COLUMNS)}
HIT_KINDS = ("don", "kat")
NONE_KINDS = ("nothing", "none", "false")
ACTION_PREFIXES = ("key.", "mouse.", "gamepad.")
TAG_KINDS = HIT_KINDS + ("nothing", "note")  # default suggestions; any text is allowed
GESTURE_FIELDS = ("gesture_sensitivity", "gesture_cooldown_ms")
TICK_HZ = 100.0  # the engine tick rate gestures are evaluated at (see Settings.tick_hz)
CAMERA_SIZE = (320, 240)
CAMERA_FPS = 20.0
SCREEN_WIDTH = 640
SCREEN_FPS = 10.0


def sessions_dir() -> Path:
    d = app_data_dir() / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Tag:
    t: float
    kind: str = "don"  # don | kat | a gesture name | an output action (key.z) | nothing | free text
    hand: int = -1  # 0 right, 1 left, -1 either
    note: str = ""

    @property
    def family(self) -> str:
        """Which detector family this tag is scored against ('' = note only)."""
        return tag_family(self.kind)

    def label(self) -> str:
        hand = {0: "R", 1: "L"}.get(self.hand, "")
        return f"{self.kind}{(' ' + hand) if hand else ''}" + (f" · {self.note}" if self.note else "")


@dataclass
class EvalResult:
    matched: int = 0
    missed: int = 0
    wrong_kind: int = 0
    false_positives: int = 0
    detections: int = 0
    tags: int = 0
    mean_offset_ms: float = 0.0
    std_offset_ms: float = 0.0
    per_tag: list[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        return 3.0 * self.matched - 3.0 * self.missed - 2.0 * self.wrong_kind - 2.0 * self.false_positives - abs(self.mean_offset_ms) / 50.0 - self.std_offset_ms / 40.0

    def text(self) -> str:
        if not self.tags:
            return f"{self.detections} events detected (no scorable tags to compare against)"
        s = f"{self.matched}/{self.tags} tags matched, {self.missed} missed, {self.wrong_kind} wrong kind, {self.false_positives} extra detections"
        if self.matched:
            s += f"; detected {self.mean_offset_ms:+.0f} ms from the tag on average (±{self.std_offset_ms:.0f} ms)"
        return s

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = round(self.score, 2)
        return d


def tag_family(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in HIT_KINDS:
        return "hit"
    if k in GESTURE_NAMES:
        return "gesture"
    if k.startswith(ACTION_PREFIXES):
        return "action"
    if k in NONE_KINDS:
        return "none"
    return ""


def suggested_tag_kinds(profile=None) -> list[str]:
    """Tag kinds worth offering for a profile: its hit kinds, its gestures, its button targets, then the generic ones."""
    kinds: list[str] = []
    if profile is not None:
        for b in profile.bindings:
            if not b.enabled:
                continue
            src = b.source
            if ".hit." in src:
                member = src.rsplit(".", 1)[-1]
                for k in HIT_KINDS if member == "any" else (member,):
                    if k in HIT_KINDS and k not in kinds:
                        kinds.append(k)
            elif ".gesture." in src:
                g = src.rsplit(".", 1)[-1]
                if g in GESTURE_NAMES and g not in kinds:
                    kinds.append(g)
        for b in profile.bindings:
            if b.enabled and b.target.startswith(ACTION_PREFIXES) and b.effective_mode() in ("tap", "hold", "toggle", "repeat") and b.target not in kinds:
                kinds.append(b.target)
    for k in ("nothing", "note"):
        if k not in kinds:
            kinds.append(k)
    return kinds


def split_tuning(data: Optional[dict]) -> tuple[dict, dict]:
    """(hit settings, gesture settings) from one flat tuning dict."""
    data = data or {}
    hit = {k: v for k, v in data.items() if k in HIT_CONFIG_FIELDS}
    ges = {k: v for k, v in data.items() if k in GESTURE_FIELDS}
    return hit, ges


def normalise_tuning(data: Optional[dict]) -> dict:
    hit, ges = split_tuning(data)
    out = hit_config_to_dict(hit_config_from_dict(hit))
    out["gesture_sensitivity"] = max(0.2, min(3.0, float(ges.get("gesture_sensitivity", 1.0) or 1.0)))
    out["gesture_cooldown_ms"] = int(max(30, min(2000, int(ges.get("gesture_cooldown_ms", 220) or 220))))
    return out


def gesture_config_for(sensitivity: float, cooldown_ms: int) -> GestureConfig:
    """Same rule as Runtime.set_profile so replays match the live detector."""
    sens = max(0.2, min(3.0, sensitivity or 1.0))
    cfg = GestureConfig()
    cfg.swing_threshold_g = 1.1 * sens
    cfg.thrust_threshold_g = 1.4 * sens
    cfg.flick_threshold_dps = 400.0 * sens
    cfg.cooldown_s = max(0.03, min(2.0, (cooldown_ms or 220) / 1000.0))
    cfg.pulse_s = min(0.12, cfg.cooldown_s * 0.8)
    return cfg


class MotionSession:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.meta: dict[str, Any] = {}
        self.t: list[np.ndarray] = [np.zeros(0, np.float64), np.zeros(0, np.float64)]
        self.data: list[np.ndarray] = [np.zeros((0, len(COLUMNS)), np.float32), np.zeros((0, len(COLUMNS)), np.float32)]
        self.hits: list[dict] = []
        self.gestures: list[dict] = []  # {t, hand, kind}: gestures the live detector fired
        self.actions: list[dict] = []  # {t, target, down}: button targets the mapping pressed/released
        self.inputs: list[dict] = []
        self.tags: list[Tag] = []
        self.camera_ts: np.ndarray = np.zeros(0)
        self.screen_ts: np.ndarray = np.zeros(0)
        self._caps: dict[str, Any] = {}
        self._cap_pos: dict[str, int] = {}

    # ------------------------------------------------------------ files
    @property
    def duration(self) -> float:
        return float(self.meta.get("duration", 0.0))

    @property
    def camera_video(self) -> Optional[Path]:
        for name in ("camera.mp4", "camera.avi"):
            if (self.folder / name).exists():
                return self.folder / name
        return None

    @property
    def screen_video(self) -> Optional[Path]:
        for name in ("screen.mp4", "screen.avi"):
            if (self.folder / name).exists():
                return self.folder / name
        return None

    def save(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.folder / "motion.npz", t0=self.t[0], d0=self.data[0], t1=self.t[1], d1=self.data[1], camera_ts=self.camera_ts, screen_ts=self.screen_ts)
        (self.folder / "events.json").write_text(json.dumps({"hits": self.hits, "gestures": self.gestures, "actions": self.actions, "inputs": self.inputs}), encoding="utf-8")
        (self.folder / "meta.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")
        self.save_tags()
        return self.folder

    def save_tags(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "tags.json").write_text(json.dumps([asdict(t) for t in self.tags], indent=1), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "MotionSession":
        s = cls(folder)
        with np.load(s.folder / "motion.npz") as z:
            s.t = [z["t0"], z["t1"]]
            s.data = [z["d0"], z["d1"]]
            s.camera_ts = z["camera_ts"] if "camera_ts" in z else np.zeros(0)
            s.screen_ts = z["screen_ts"] if "screen_ts" in z else np.zeros(0)
        try:
            ev = json.loads((s.folder / "events.json").read_text(encoding="utf-8"))
            s.hits, s.gestures, s.inputs = ev.get("hits", []), ev.get("gestures", []), ev.get("inputs", [])
            s.actions = ev.get("actions", [])
        except (OSError, ValueError):
            pass
        try:
            s.meta = json.loads((s.folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            s.meta = {"duration": float(max((t[-1] if len(t) else 0.0) for t in s.t))}
        try:
            s.tags = [Tag(**t) for t in json.loads((s.folder / "tags.json").read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            s.tags = []
        return s

    @staticmethod
    def list_sessions() -> list[Path]:
        return sorted([p for p in sessions_dir().glob("motion-*") if (p / "motion.npz").exists()], reverse=True)

    # ------------------------------------------------------------ tags
    def add_tag(self, t: float, kind: str = "don", hand: int = -1, note: str = "") -> Tag:
        kind = (kind or "").strip() or "note"
        tag = Tag(round(float(t), 4), kind.lower() if tag_family(kind) else kind, int(hand), note)
        self.tags.append(tag)
        self.tags.sort(key=lambda x: x.t)
        return tag

    def remove_tag(self, index: int) -> None:
        if 0 <= index < len(self.tags):
            del self.tags[index]

    # ------------------------------------------------------------ series
    def series(self, hand: int, name: str) -> np.ndarray:
        return self.data[hand][:, COL[name]] if len(self.data[hand]) else np.zeros(0, np.float32)

    def linear_magnitude(self, hand: int) -> np.ndarray:
        """|accel| - 1 g: a cheap 'how hard is it moving' curve for the timeline."""
        d = self.data[hand]
        if not len(d):
            return np.zeros(0, np.float32)
        return np.sqrt(d[:, 0] ** 2 + d[:, 1] ** 2 + d[:, 2] ** 2) - 1.0

    def index_at(self, hand: int, t: float) -> int:
        arr = self.t[hand]
        if not len(arr):
            return 0
        return min(len(arr) - 1, max(0, bisect_left(arr, t)))

    def rate(self, hand: int) -> float:
        arr = self.t[hand]
        if len(arr) < 2:
            return 0.0
        return (len(arr) - 1) / max(1e-6, float(arr[-1] - arr[0]))

    # ------------------------------------------------------------ video
    def _capture(self, kind: str):
        if cv2 is None:
            return None
        if kind in self._caps:
            return self._caps[kind]
        path = self.camera_video if kind == "camera" else self.screen_video
        if path is None:
            self._caps[kind] = None
            return None
        cap = cv2.VideoCapture(str(path))
        self._caps[kind] = cap if cap.isOpened() else None
        self._cap_pos[kind] = -1
        return self._caps[kind]

    def frame_at(self, kind: str, t: float):
        """BGR frame nearest to time ``t`` (None without video)."""
        ts = self.camera_ts if kind == "camera" else self.screen_ts
        cap = self._capture(kind)
        if cap is None or not len(ts):
            return None
        idx = min(len(ts) - 1, max(0, bisect_left(ts, t)))
        if idx > 0 and idx < len(ts) and abs(ts[idx - 1] - t) < abs(ts[idx] - t):
            idx -= 1
        if self._cap_pos.get(kind) != idx - 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        self._cap_pos[kind] = idx
        return frame if ok else None

    def thumbnail(self, kind: str, t: float, width: int = 320) -> Optional[bytes]:
        frame = self.frame_at(kind, t)
        if frame is None or cv2 is None:
            return None
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, int(h * width / w)))
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return bytes(buf) if ok else None

    def close(self) -> None:
        for cap in self._caps.values():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        self._caps.clear()

    # ------------------------------------------------------------ analysis
    def tuning(self) -> dict:
        """The tuning in use while recording (hit settings + gesture sensitivity / cooldown)."""
        base = dict(self.meta.get("hit_config") or {})
        base.update(self.meta.get("tuning") or {})
        return normalise_tuning(base)

    def families_in_tags(self) -> set[str]:
        return {t.family for t in self.tags if t.family and t.family != "none"}

    def replay(self, config: HitConfig, hand: int) -> list[dict]:
        """Run the drum-hit detector over the recorded stream of one hand."""
        det = DrumHitDetector(config=config)
        out: list[dict] = []
        t = self.t[hand]
        d = self.data[hand]
        for i in range(len(t)):
            hit = det.update(Vec3(float(d[i, 0]), float(d[i, 1]), float(d[i, 2])), float(t[i]), float(d[i, 6]), bool(d[i, 7] > 0.5))
            if hit is not None:
                out.append({"t": float(t[i]), "hand": hand, "kind": hit.kind, "family": "hit", "strength": round(hit.strength, 2), "stroke_ms": round(hit.stroke_ms, 1)})
        return out

    def replay_gestures(self, sensitivity: float, cooldown_ms: int, hand: int, tick_hz: float = TICK_HZ) -> list[dict]:
        """Run the gesture detector over one hand the way the engine tick does (sub-sampled to the tick rate)."""
        t = self.t[hand]
        d = self.data[hand]
        if len(t) < 2:
            return []
        det = GestureDetector(config=gesture_config_for(sensitivity, cooldown_ms))
        out: list[dict] = []
        period = 1.0 / max(20.0, tick_hz)
        next_t = float(t[0])
        last_t = next_t - period
        for i in range(len(t)):
            ti = float(t[i])
            if ti < next_t:
                continue
            next_t = ti + period
            dt = max(1e-4, min(0.1, ti - last_t))
            last_t = ti
            fired = det.update(Vec3(float(d[i, 0]), float(d[i, 1]), float(d[i, 2])), Vec3(float(d[i, 3]), float(d[i, 4]), float(d[i, 5])), dt, ti)
            for name in fired:
                if name != "swing_any":
                    out.append({"t": ti, "hand": hand, "kind": name, "family": "gesture", "strength": round(det.strength, 2)})
        return out

    def replay_events(self, config: Optional[dict] = None, families: Optional[set[str]] = None) -> list[dict]:
        """Everything the detectors would fire with ``config`` (hits, gestures) plus the recorded actions."""
        cfg = normalise_tuning(config if config is not None else self.tuning())
        families = families if families is not None else (self.families_in_tags() or {"hit"})
        events: list[dict] = []
        if "hit" in families:
            hit_cfg = hit_config_from_dict(cfg)
            events += self.replay(hit_cfg, 0) + self.replay(hit_cfg, 1)
        if "gesture" in families:
            events += self.replay_gestures(cfg["gesture_sensitivity"], cfg["gesture_cooldown_ms"], 0) + self.replay_gestures(cfg["gesture_sensitivity"], cfg["gesture_cooldown_ms"], 1)
        if "action" in families:
            events += [{"t": float(a["t"]), "hand": -1, "kind": a["target"], "family": "action"} for a in self.actions if a.get("down")]
        events.sort(key=lambda h: h["t"])
        return events

    def evaluate(self, config: Optional[dict] = None, complete: bool = False, window_ms: float = 100.0) -> EvalResult:
        """Score tuning settings against the tags.

        Tags of the hit / gesture families are matched against a replay with
        ``config``; action tags against what the mapping really sent while
        recording; ``nothing`` tags count any event within the window as a
        false positive.  ``complete`` means the player tagged every moment
        that should fire, so unmatched events count as false positives.
        """
        cfg = normalise_tuning(config if config is not None else self.tuning())
        families = self.families_in_tags()
        scorable = [t for t in self.tags if t.family and t.family != "none"]
        detections = self.replay_events(cfg, families) if families else []
        res = EvalResult(config=cfg, tags=len(scorable), detections=len(detections))
        used: set[int] = set()
        offsets: list[float] = []
        win = window_ms / 1000.0
        for tag in self.tags:
            fam = tag.family
            if not fam:
                continue
            if fam == "none":
                near = [i for i, h in enumerate(detections) if abs(h["t"] - tag.t) <= win and (tag.hand < 0 or h["hand"] in (-1, tag.hand))]
                res.false_positives += len(near)
                used.update(near)
                res.per_tag.append({"tag": tag.label(), "t": tag.t, "result": "ok" if not near else f"{len(near)} event(s) fired here", "fired": [detections[i]["kind"] for i in near]})
                continue
            best_i, best_dt = -1, None
            for i, h in enumerate(detections):
                if i in used or h["family"] != fam or (tag.hand >= 0 and h["hand"] not in (-1, tag.hand)):
                    continue
                dt = h["t"] - tag.t
                if abs(dt) <= win and (best_dt is None or abs(dt) < abs(best_dt)):
                    best_i, best_dt = i, dt
            entry = {"tag": tag.label(), "t": tag.t}
            if best_i < 0:
                res.missed += 1
                entry["result"] = "missed"
            else:
                used.add(best_i)
                h = detections[best_i]
                offsets.append(best_dt * 1000.0)
                entry.update({"detected": h["kind"], "offset_ms": round(best_dt * 1000.0, 1)})
                if "strength" in h:
                    entry["strength"] = h["strength"]
                if h["kind"] == tag.kind:
                    res.matched += 1
                    entry["result"] = "ok"
                else:
                    res.wrong_kind += 1
                    entry["result"] = "wrong kind"
            res.per_tag.append(entry)
        if complete:
            res.false_positives += len([i for i in range(len(detections)) if i not in used])
        if offsets:
            res.mean_offset_ms = float(np.mean(offsets))
            res.std_offset_ms = float(np.std(offsets))
        return res

    def tag_window(self, index: int, half_ms: float = 150.0, points: int = 30) -> dict:
        """Compact numbers around one tag for the coach: accel, gyro, orientation, trigger, tracking, events."""
        tag = self.tags[index]
        hands = [tag.hand] if tag.hand in (0, 1) else [0, 1]
        out: dict[str, Any] = {"tag": tag.label(), "kind": tag.kind, "family": tag.family or "note", "t": tag.t, "hands": {}}
        for hand in hands:
            t = self.t[hand]
            if not len(t):
                continue
            i0 = self.index_at(hand, tag.t - half_ms / 1000.0)
            i1 = self.index_at(hand, tag.t + half_ms / 1000.0)
            idx = np.linspace(i0, max(i0, i1), num=min(points, max(1, i1 - i0 + 1)), dtype=int)
            rows = []
            for i in idx:
                d = self.data[hand][i]
                rows.append([round(float(t[i] - tag.t) * 1000.0), round(float(d[0]), 2), round(float(d[1]), 2), round(float(d[2]), 2),
                             round(math.degrees(float(d[3]))), round(math.degrees(float(d[4]))), round(math.degrees(float(d[5]))), round(float(d[6]), 2),
                             round(float(d[8])), round(float(d[9])), round(float(d[10]))])
            at = self.data[hand][self.index_at(hand, tag.t)]
            info: dict[str, Any] = {"columns": ["ms", "ax", "ay", "az", "gx_dps", "gy_dps", "gz_dps", "trigger", "roll", "pitch", "yaw"], "rows": rows}
            if float(at[14]) > 0.5:
                info["camera"] = {"x": round(float(at[11]), 3), "y": round(float(at[12]), 3), "depth": round(float(at[13]), 3)}
            out["hands"][f"c{hand}"] = info
        w = half_ms / 1000.0
        out["detected_hits_nearby"] = [h for h in self.hits if abs(h["t"] - tag.t) <= w]
        out["gestures_nearby"] = [g for g in self.gestures if abs(g["t"] - tag.t) <= w]
        out["actions_nearby"] = [a for a in self.actions if abs(a["t"] - tag.t) <= w]
        if self.inputs:
            out["player_inputs_nearby"] = [i for i in self.inputs if abs(i["t"] - tag.t) <= w][:20]
        return out

    def summary(self) -> str:
        lines = [f"Motion recording {self.folder.name}: {self.duration:.1f} s, profile '{self.meta.get('profile', '?')}'" + (f" for {self.meta['game']}" if self.meta.get("game") else "")]
        for hand in (0, 1):
            n = len(self.t[hand])
            if n:
                lm = self.linear_magnitude(hand)
                d = self.data[hand]
                tracked = int((d[:, 14] > 0.5).sum())
                lines.append(f"  c{hand} ({'right' if hand == 0 else 'left'} hand): {n} samples @ {self.rate(hand):.0f} Hz, peak |accel|-1g {float(lm.max()):.2f} g, "
                             f"trigger used {int((d[:, 6] > 0.5).sum())} samples, roll {float(d[:, 8].min()):.0f}..{float(d[:, 8].max()):.0f}°, pitch {float(d[:, 9].min()):.0f}..{float(d[:, 9].max()):.0f}°, "
                             f"yaw {float(d[:, 10].min()):.0f}..{float(d[:, 10].max()):.0f}°, camera-tracked {100 * tracked // n}% of the time")
        kinds: dict[str, int] = {}
        for h in self.hits:
            kinds[h["kind"]] = kinds.get(h["kind"], 0) + 1
        gk: dict[str, int] = {}
        for g in self.gestures:
            gk[g["kind"]] = gk.get(g["kind"], 0) + 1
        ak: dict[str, int] = {}
        for a in self.actions:
            if a.get("down"):
                ak[a["target"]] = ak.get(a["target"], 0) + 1
        lines.append(f"  fired while recording: {len(self.hits)} hits {kinds}, gestures {gk}, mapping sent {ak}")
        if self.inputs:
            lines.append(f"  player's own keyboard/mouse: {len(self.inputs)} events")
        lines.append(f"  tags: {len(self.tags)} " + ", ".join(t.label() + f"@{t.t:.2f}s" for t in self.tags[:40]))
        lines.append(f"  video: camera={'yes' if self.camera_video else 'no'} screen={'yes' if self.screen_video else 'no'}; tuning in use: {self.tuning()}")
        if self.meta.get("bindings"):
            lines.append("  bindings while recording: " + "; ".join(self.meta["bindings"][:40]))
        if self.meta.get("explanation"):
            lines.append(f"  player's explanation: {self.meta['explanation']}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Auto-fit (no AI): coordinate search over the detector settings
# --------------------------------------------------------------------------- #
def auto_fit(session: MotionSession, base: Optional[dict] = None, complete: bool = False,
             progress: Optional[Callable[[int, int], None]] = None) -> tuple[dict, EvalResult]:
    """Coordinate search over whichever detector families the tags use (hits and/or gestures)."""
    cfg = normalise_tuning(base if base is not None else session.tuning())
    best = session.evaluate(cfg, complete)
    families = session.families_in_tags()
    stages: list[tuple[str, list]] = []
    if "hit" in families:
        has_kat = any(t.kind == "kat" for t in session.tags)
        stages += [
            ("stop_g", [0.8, 1.0, 1.3, 1.6, 2.0, 2.5, 3.2]),
            ("onset_g", [0.5, 0.7, 0.9, 1.2, 1.5]),
        ]
        if has_kat:
            stages.append(("kat_angle_deg", [25.0, 35.0, 45.0, 55.0]))
        stages += [("refractory_s", [0.03, 0.045, 0.07]), ("stop_g", [0.9, 1.1, 1.3, 1.5, 1.8])]
    if "gesture" in families:
        stages += [
            ("gesture_sensitivity", [0.5, 0.65, 0.8, 1.0, 1.2, 1.5, 1.9]),
            ("gesture_cooldown_ms", [80, 120, 180, 220, 300, 450]),
            ("gesture_sensitivity", [0.6, 0.7, 0.9, 1.1, 1.3]),
        ]
    total = sum(len(v) for _, v in stages)
    done = 0
    for key, values in stages:
        for v in values:
            trial = dict(cfg, **{key: v})
            res = session.evaluate(trial, complete)
            done += 1
            if progress:
                progress(done, total)
            if res.score > best.score:
                best, cfg = res, trial
    return normalise_tuning(cfg), best


# --------------------------------------------------------------------------- #
# Recorder
# --------------------------------------------------------------------------- #
class _VideoEncoder:
    """Writes frames from a queue on its own thread (never blocks the camera)."""

    def __init__(self, path: Path, size: tuple[int, int], fps: float) -> None:
        self.path = path
        self.size = size
        self.fps = fps
        self.queue: Queue = Queue(maxsize=64)
        self.timestamps: list[float] = []
        self._writer = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_t = -1e9
        self.frames = 0

    def open(self) -> bool:
        if cv2 is None:
            return False
        for suffix, fourcc in ((".mp4", "mp4v"), (".avi", "MJPG")):
            p = self.path.with_suffix(suffix)
            w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*fourcc), self.fps, self.size)
            if w.isOpened():
                self._writer, self.path = w, p
                break
        if self._writer is None:
            return False
        self._thread = threading.Thread(target=self._run, name=f"encode-{self.path.stem}", daemon=True)
        self._thread.start()
        return True

    def push(self, frame, t: float) -> None:
        if self._writer is None or t - self._last_t < 1.0 / self.fps:
            return
        self._last_t = t
        try:
            self.queue.put_nowait((frame, t))
        except Exception:
            pass  # encoder is behind: drop the frame rather than stall the source

    def _run(self) -> None:
        while not self._stop.is_set() or not self.queue.empty():
            try:
                frame, t = self.queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                h, w = frame.shape[:2]
                if (w, h) != self.size:
                    frame = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
                self._writer.write(frame)
                self.timestamps.append(t)
                self.frames += 1
            except Exception as exc:  # pragma: no cover
                log.debug("encode failed: %s", exc)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._writer is not None:
            self._writer.release()
            self._writer = None


class MotionRecorder:
    """Records everything for ``seconds`` (or until stop()) into a new session folder."""

    def __init__(self, runtime, seconds: float = 30.0, camera: bool = True, screen: bool = False, inputs: bool = False,
                 explanation: str = "") -> None:
        self.runtime = runtime
        self.seconds = seconds
        self.want_camera = camera
        self.want_screen = screen
        self.want_inputs = inputs
        self.explanation = explanation
        self.session: Optional[MotionSession] = None
        self.active = False
        self.on_progress: Optional[Callable[[float, float], None]] = None
        self.on_done: Optional[Callable[[MotionSession], None]] = None
        self._t0 = 0.0
        self._rows: list[list[list[float]]] = [[], []]
        self._times: list[list[float]] = [[], []]
        self._hits: list[dict] = []
        self._gestures: list[dict] = []
        self._actions: list[dict] = []
        self._inputs: list[dict] = []
        self._lock = threading.Lock()
        self._cam_encoder: Optional[_VideoEncoder] = None
        self._screen_encoder: Optional[_VideoEncoder] = None
        self._old_cam_cb = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._listener = None

    # ------------------------------------------------------------ taps
    def _frame_tap(self, index: int, accel: Vec3, gyro: Vec3, t: float, trigger: float, move: bool) -> None:
        if index not in (0, 1):
            return
        st = self.runtime.devices.controllers[index].state if index < len(self.runtime.devices.controllers) else None
        tr = st.tracker if st else None
        row = [accel.x, accel.y, accel.z, gyro.x, gyro.y, gyro.z, trigger, 1.0 if move else 0.0,
               st.roll if st else 0.0, st.pitch if st else 0.0, st.yaw if st else 0.0,
               tr.x if tr else 0.0, tr.y if tr else 0.0, tr.depth if tr else 0.0, 1.0 if (tr and tr.tracked) else 0.0]
        with self._lock:
            self._times[index].append(t - self._t0)
            self._rows[index].append(row)

    def _hit_tap(self, index: int, hit) -> None:
        with self._lock:
            self._hits.append({"t": round(hit.t - self._t0, 4), "hand": index, "kind": hit.kind, "strength": round(hit.strength, 2), "stroke_ms": round(hit.stroke_ms, 1), "modifier": hit.modifier})

    def _gesture_tap(self, index: int, name: str, t: float) -> None:
        if name == "swing_any":
            return
        with self._lock:
            self._gestures.append({"t": round(t - self._t0, 4), "hand": index, "kind": name})

    def _action_tap(self, target: str, down: bool, t: float) -> None:
        with self._lock:
            self._actions.append({"t": round(t - self._t0, 4), "target": target, "down": bool(down)})

    def _camera_tap(self, frame) -> None:
        if self._old_cam_cb is not None:
            try:
                self._old_cam_cb(frame)
            except Exception:
                pass
        if self._cam_encoder is not None:
            self._cam_encoder.push(frame, time.monotonic() - self._t0)

    # ------------------------------------------------------------ control
    def start(self) -> MotionSession:
        folder = sessions_dir() / time.strftime("motion-%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        self.session = MotionSession(folder)
        self._t0 = time.monotonic()
        self.active = True
        dev = self.runtime.devices
        dev.frame_taps.append(self._frame_tap)
        dev.hit_taps.append(self._hit_tap)
        dev.gesture_taps.append(self._gesture_tap)
        self.runtime.engine.action_taps.append(self._action_tap)
        cam = dev.camera
        if self.want_camera and cam is not None and "synthetic" not in cam.source.name.lower() or (self.want_camera and cam is not None and self.runtime.settings.camera_backend == "synthetic"):
            self._cam_encoder = _VideoEncoder(folder / "camera", CAMERA_SIZE, CAMERA_FPS)
            if self._cam_encoder.open():
                self._old_cam_cb = cam.on_frame
                cam.on_frame = self._camera_tap
            else:
                self._cam_encoder = None
        if self.want_screen:
            self._screen_encoder = _VideoEncoder(folder / "screen", (SCREEN_WIDTH, 360), SCREEN_FPS)
            if not self._screen_encoder.open():
                self._screen_encoder = None
        if self.want_inputs:
            from themover.ai.recorder import InputListener

            self._listener = InputListener(lambda ev: self._inputs.append({"t": round(ev.t, 4), "kind": ev.kind, "name": ev.name, "x": ev.x, "y": ev.y}), self._t0)
            self._listener.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motion-recorder", daemon=True)
        self._thread.start()
        return self.session

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        grabber = None
        if self._screen_encoder is not None:
            try:
                from themover.ai.recorder import ScreenGrabber

                grabber = ScreenGrabber(max_width=SCREEN_WIDTH, quality=70)
            except Exception:
                grabber = None
        next_screen = 0.0
        try:
            while not self._stop.is_set():
                elapsed = time.monotonic() - self._t0
                if elapsed >= self.seconds:
                    break
                if grabber is not None and elapsed >= next_screen:
                    next_screen = elapsed + 1.0 / SCREEN_FPS
                    frame = self._grab_screen(grabber)
                    if frame is not None:
                        self._screen_encoder.push(frame, elapsed)
                if self.on_progress:
                    try:
                        self.on_progress(elapsed, self.seconds)
                    except Exception:
                        pass
                time.sleep(0.02)
        finally:
            self._finish(grabber)

    @staticmethod
    def _grab_screen(grabber):
        try:
            monitor = grabber._mss.monitors[1] if len(grabber._mss.monitors) > 1 else grabber._mss.monitors[0]
            shot = grabber._mss.grab(monitor)
            frame = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)[:, :, :3]
            h, w = frame.shape[:2]
            scale = SCREEN_WIDTH / w
            return cv2.resize(np.ascontiguousarray(frame), (SCREEN_WIDTH, max(1, int(h * scale)))) if cv2 is not None else None
        except Exception:
            return None

    def _finish(self, grabber) -> None:
        dev = self.runtime.devices
        duration = time.monotonic() - self._t0
        try:
            dev.frame_taps.remove(self._frame_tap)
        except ValueError:
            pass
        for lst, tap in ((dev.hit_taps, self._hit_tap), (dev.gesture_taps, self._gesture_tap), (self.runtime.engine.action_taps, self._action_tap)):
            try:
                lst.remove(tap)
            except ValueError:
                pass
        if self._old_cam_cb is not None and dev.camera is not None:
            dev.camera.on_frame = self._old_cam_cb
        if self._listener is not None:
            self._listener.stop()
        if grabber is not None:
            grabber.close()
        s = self.session
        for enc, attr in ((self._cam_encoder, "camera_ts"), (self._screen_encoder, "screen_ts")):
            if enc is not None:
                enc.close()
                setattr(s, attr, np.array(enc.timestamps, dtype=np.float64))
        with self._lock:
            for hand in (0, 1):
                s.t[hand] = np.array(self._times[hand], dtype=np.float64)
                s.data[hand] = np.array(self._rows[hand], dtype=np.float32).reshape(-1, len(COLUMNS))
            s.hits = list(self._hits)
            s.gestures = list(self._gestures)
            s.actions = list(self._actions)
            s.inputs = list(self._inputs)
        profile = self.runtime.profile
        hit_cfg = hit_config_to_dict(dev.hits[0].config) if dev.hits else {}
        s.meta = {
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": round(duration, 3),
            "profile": profile.name,
            "game": profile.game,
            "hit_config": hit_cfg,
            "tuning": normalise_tuning(dict(hit_cfg, gesture_sensitivity=profile.gesture_sensitivity, gesture_cooldown_ms=profile.gesture_cooldown_ms)),
            "bindings": [f"{b.source} -> {b.target} ({b.effective_mode()})" for b in profile.bindings if b.enabled][:60],
            "sources": {"camera": self._cam_encoder is not None, "screen": self._screen_encoder is not None, "inputs": self._listener is not None},
            "rates": [round(s.rate(0), 1), round(s.rate(1), 1)],
            "explanation": self.explanation,
            "controllers": [c.state.model + " " + c.state.serial for c in dev.controllers],
        }
        try:
            s.save()
        except Exception as exc:
            log.warning("could not save motion session: %s", exc)
        self.active = False
        if self.on_done:
            try:
                self.on_done(s)
            except Exception as exc:
                log.exception("on_done failed: %s", exc)
