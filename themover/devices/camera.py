"""Camera sources: PS3 Eye (via ``pseyepy`` or a webcam driver) or any OpenCV camera.

A :class:`SyntheticCamera` renders two moving glowing spheres so the whole
pipeline can be demonstrated and tested without hardware.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Optional

import numpy as np

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - depends on the machine
    cv2 = None  # type: ignore


# PS3 Eye (OV7720/OV534) modes: 640x480 up to 75 fps (60 official), 320x240 up to 187 fps.
FPS_CANDIDATES_LARGE = (75, 60, 50, 40, 30, 15)
FPS_CANDIDATES_SMALL = (187, 150, 125, 100, 75, 60, 50, 40, 30)


def fps_candidates(requested: int, low_res: bool) -> list[int]:
    """Frame rates to try, highest first (``requested`` 0 = highest possible)."""
    base = list(FPS_CANDIDATES_SMALL if low_res else FPS_CANDIDATES_LARGE)
    if requested > 0:
        return [requested] + [f for f in base if f < requested]
    return base


class CameraSource:
    name = "camera"
    width = 640
    height = 480

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def read(self) -> Optional[np.ndarray]:
        """Return a BGR frame or ``None``."""
        return None

    @property
    def is_open(self) -> bool:
        return True

    def set_control(self, name: str, value: float) -> bool:
        """Change 'exposure' or 'gain' if the driver allows it; returns True when accepted."""
        return False


class OpenCVCamera(CameraSource):
    """Any camera visible to OpenCV - including a PS3 Eye with the CL-Eye driver."""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: int = 0, exposure: int = -7, gain: int = 20) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps  # 0 = highest the driver accepts
        self.exposure = exposure
        self.gain = gain
        self.name = f"camera {index}"
        self._cap = None

    def set_control(self, name: str, value: float) -> bool:
        if self._cap is None or cv2 is None:
            return False
        prop = {"exposure": cv2.CAP_PROP_EXPOSURE, "gain": cv2.CAP_PROP_GAIN}.get(name)
        if prop is None:
            return False
        try:
            if name == "exposure" and value > 0:
                value = -min(13.0, math.log2(256.0 / max(1.0, value)) + 1.0)  # pseye-style 0..255 -> DirectShow log2 seconds
            ok = bool(self._cap.set(prop, float(value)))
        except Exception:
            return False
        setattr(self, name, value)
        return ok

    def open(self) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed")
        backend = getattr(cv2, "CAP_DSHOW", 0) if _is_windows() else 0
        cap = cv2.VideoCapture(self.index, backend) if backend else cv2.VideoCapture(self.index)
        if not cap.isOpened():
            raise RuntimeError(f"camera {self.index} could not be opened")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # Highest frame rate the driver accepts (drivers that do not report a rate keep the first request).
        got = 0.0
        for fps in fps_candidates(self.fps, self.width <= 320):
            cap.set(cv2.CAP_PROP_FPS, fps)
            got = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if got <= 0 or got >= fps - 1:
                self.fps = fps if got <= 0 else int(round(got))
                break
        else:
            self.fps = int(round(got)) if got > 0 else self.fps
        # Low exposure makes the glowing sphere pop against the room.
        self._cap = cap
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        except Exception:
            pass
        self.set_control("exposure", self.exposure)
        self.set_control("gain", self.gain)
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.width)
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.height)
        self.name = f"camera {self.index} {self.width}x{self.height} @ {self.fps} fps"

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    def read(self) -> Optional[np.ndarray]:
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None


class PSEyeCamera(CameraSource):
    """PS3 Eye through the libusb driver (``pip install pseyepy`` + Zadig on Windows)."""

    def __init__(self, index: int = 0, fps: int = 0, exposure: int = 60, gain: int = 20, low_res: bool = False) -> None:
        self.index = index
        self.fps = fps  # 0 = highest the PS3 Eye supports at this resolution
        self.low_res = low_res
        self.exposure = exposure if exposure >= 0 else int(max(0, min(255, 2.0 ** (13 + exposure))))
        self.gain = gain
        self.name = "PS3 Eye"
        self._cam = None

    def set_control(self, name: str, value: float) -> bool:
        if self._cam is None or name not in ("exposure", "gain"):
            return False
        if name == "exposure" and value < 0:
            value = int(max(0, min(255, 2.0 ** (13 + value))))
        try:
            setattr(self._cam, name, int(max(0, min(255 if name == "exposure" else 79, value))))
        except Exception:
            return False
        setattr(self, name, int(value))
        return True

    def open(self) -> None:
        try:
            from pseyepy import Camera  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pseyepy is not installed") from exc
        resolution = Camera.RES_SMALL if self.low_res else Camera.RES_LARGE
        errors = []
        for fps in fps_candidates(self.fps, self.low_res):
            try:
                self._cam = Camera(self.index, fps=fps, resolution=resolution, colour=True, gain=int(self.gain), exposure=int(self.exposure))
                self.fps = fps
                break
            except Exception as exc:  # unsupported rate: try the next lower one
                errors.append(f"{fps} fps: {exc}")
                self._cam = None
        if self._cam is None:
            raise RuntimeError("PS3 Eye could not be opened: " + "; ".join(errors[-3:]))
        self.width, self.height = (320, 240) if self.low_res else (640, 480)
        self.name = f"PS3 Eye {self.width}x{self.height} @ {self.fps} fps"

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.end()
            except Exception:
                pass
            self._cam = None

    @property
    def is_open(self) -> bool:
        return self._cam is not None

    def read(self) -> Optional[np.ndarray]:
        if self._cam is None:
            return None
        frame, _ts = self._cam.read()
        if frame is None:
            return None
        # pseyepy returns RGB; the rest of the pipeline is BGR.
        return np.ascontiguousarray(frame[:, :, ::-1])


class SyntheticCamera(CameraSource):
    """Two coloured spheres orbiting on a dark background (demo / tests)."""

    def __init__(self, colors: list[tuple[int, int, int]] | None = None, width: int = 640, height: int = 480) -> None:
        self.width = width
        self.height = height
        self.name = "synthetic camera"
        self.colors = colors or [(255, 0, 255), (0, 255, 255)]
        self._t0 = time.monotonic()
        self.positions: list[tuple[float, float, float]] | None = None  # (x, y, r) override

    def read(self) -> Optional[np.ndarray]:
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame[:] = (24, 20, 18)
        t = time.monotonic() - self._t0
        for i, rgb in enumerate(self.colors[:2]):
            if self.positions and i < len(self.positions):
                x, y, r = self.positions[i]
            else:
                phase = t * 0.8 + i * math.pi
                x = self.width * (0.5 + 0.28 * math.cos(phase))
                y = self.height * (0.5 + 0.22 * math.sin(phase * 1.3))
                r = 22 + 10 * math.sin(t * 0.5 + i)
            bgr = (int(rgb[2]), int(rgb[1]), int(rgb[0]))
            if cv2 is not None:
                cv2.circle(frame, (int(x), int(y)), int(r), bgr, -1, lineType=cv2.LINE_AA)
            else:  # pragma: no cover
                yy, xx = np.ogrid[: self.height, : self.width]
                frame[(xx - x) ** 2 + (yy - y) ** 2 <= r * r] = bgr
        time.sleep(1 / 60)
        return frame


def _is_windows() -> bool:
    import sys

    return sys.platform.startswith("win")


def open_camera(backend: str = "auto", index: int = 0, colors=None, exposure: int = -7, gain: int = 20, fps: int = 0, low_res: bool = False) -> CameraSource:
    """Open the best available camera according to ``backend``."""
    attempts: list[str] = []
    if backend == "synthetic":
        cam = SyntheticCamera(colors)
        cam.open()
        return cam
    if backend in ("auto", "pseye"):
        try:
            cam = PSEyeCamera(index, fps=fps, exposure=exposure, gain=gain, low_res=low_res)
            cam.open()
            return cam
        except Exception as exc:
            attempts.append(f"pseye: {exc}")
            if backend == "pseye":
                raise RuntimeError("; ".join(attempts))
    if backend in ("auto", "opencv"):
        indices = [index] + [i for i in range(0, 4) if i != index] if backend == "auto" else [index]
        for i in indices:
            try:
                cam = OpenCVCamera(i, width=320 if low_res else 640, height=240 if low_res else 480, fps=fps, exposure=exposure, gain=gain)
                cam.open()
                frame = cam.read()
                if frame is None:
                    cam.close()
                    raise RuntimeError("no frame")
                return cam
            except Exception as exc:
                attempts.append(f"opencv {i}: {exc}")
        if backend == "opencv":
            raise RuntimeError("; ".join(attempts))
    cam = SyntheticCamera(colors)
    cam.open()
    cam.name = "synthetic camera (no real camera found)"
    return cam


class CameraThread:
    """Grabs frames continuously and keeps only the newest one."""

    def __init__(self, source: CameraSource) -> None:
        self.source = source
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.frame_count = 0
        self.fps = 0.0
        self.on_frame = None  # callable(frame) executed in the camera thread

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.source.close()

    def _run(self) -> None:
        last = time.monotonic()
        while not self._stop.is_set():
            frame = self.source.read()
            if frame is None:
                time.sleep(0.01)
                continue
            now = time.monotonic()
            dt = now - last
            last = now
            if dt > 0:
                self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
            self.frame_count += 1
            if self.on_frame is not None:
                try:
                    self.on_frame(frame)
                except Exception:
                    pass
            with self._lock:
                self._frame = frame

    def latest(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._frame is None else self._frame.copy()
