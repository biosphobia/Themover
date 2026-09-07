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


class OpenCVCamera(CameraSource):
    """Any camera visible to OpenCV - including a PS3 Eye with the CL-Eye driver."""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: int = 60) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.fps = fps
        self.name = f"camera {index}"
        self._cap = None

    def open(self) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed")
        backend = getattr(cv2, "CAP_DSHOW", 0) if _is_windows() else 0
        cap = cv2.VideoCapture(self.index, backend) if backend else cv2.VideoCapture(self.index)
        if not cap.isOpened():
            raise RuntimeError(f"camera {self.index} could not be opened")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # Low exposure makes the glowing sphere pop against the room.
        try:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            cap.set(cv2.CAP_PROP_EXPOSURE, -7)
        except Exception:
            pass
        self._cap = cap
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.width)
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.height)

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

    def __init__(self, index: int = 0, fps: int = 60) -> None:
        self.index = index
        self.fps = fps
        self.name = "PS3 Eye"
        self._cam = None

    def open(self) -> None:
        try:
            from pseyepy import Camera  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("pseyepy is not installed") from exc
        self._cam = Camera(self.index, fps=self.fps, resolution=Camera.RES_LARGE, colour=True, gain=20, exposure=60)
        self.width, self.height = 640, 480

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


def open_camera(backend: str = "auto", index: int = 0, colors=None) -> CameraSource:
    """Open the best available camera according to ``backend``."""
    attempts: list[str] = []
    if backend == "synthetic":
        cam = SyntheticCamera(colors)
        cam.open()
        return cam
    if backend in ("auto", "pseye"):
        try:
            cam = PSEyeCamera(index)
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
                cam = OpenCVCamera(i)
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
