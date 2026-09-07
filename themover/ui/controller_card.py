"""Live status card for one PS Move controller."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QProgressBar, QVBoxLayout

from themover.core.state import MoveState


class Gauge(QProgressBar):
    def __init__(self, bipolar: bool = False) -> None:
        super().__init__()
        self.bipolar = bipolar
        self.setRange(0, 1000)
        self.setTextVisible(False)
        self.setFixedHeight(10)
        self.setObjectName("gauge")

    def set_value(self, v: float, lo: float, hi: float) -> None:
        v = max(lo, min(hi, v))
        self.setValue(int((v - lo) / (hi - lo) * 1000))


class ControllerCard(QFrame):
    def __init__(self, index: int) -> None:
        super().__init__()
        self.index = index
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        self.title = QLabel(f"Controller {index + 1}")
        self.title.setObjectName("h2")
        self.subtitle = QLabel("not connected")
        self.subtitle.setObjectName("muted")
        self.swatch = QLabel()
        self.swatch.setFixedSize(26, 26)
        self.swatch.setStyleSheet("border-radius:13px; background:#333;")
        head = QGridLayout()
        head.addWidget(self.title, 0, 0)
        head.addWidget(self.swatch, 0, 1, 2, 1, Qt.AlignRight)
        head.addWidget(self.subtitle, 1, 0)
        layout.addLayout(head)

        grid = QGridLayout()
        self.gauges: dict[str, Gauge] = {}
        self.labels: dict[str, QLabel] = {}
        rows = [("trigger", 0.0, 1.0), ("motion", 0.0, 3.0), ("roll", -90.0, 90.0), ("pitch", -90.0, 90.0), ("yaw", -90.0, 90.0), ("track x", -1.0, 1.0), ("track y", -1.0, 1.0), ("depth", 0.0, 1.0)]
        self.simple_rows = {"trigger", "motion"}
        self.ranges = {name: (lo, hi) for name, lo, hi in rows}
        for r, (name, lo, hi) in enumerate(rows):
            lbl = QLabel(name)
            lbl.setObjectName("muted")
            g = Gauge(lo < 0)
            grid.addWidget(lbl, r, 0)
            grid.addWidget(g, r, 1)
            self.gauges[name] = g
            self.labels[name] = lbl
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.buttons = QLabel("")
        self.buttons.setWordWrap(True)
        self.buttons.setMinimumHeight(22)
        layout.addWidget(self.buttons)
        self.output = QLabel("")
        self.output.setObjectName("muted")
        self.output.setWordWrap(True)
        layout.addWidget(self.output)
        self.set_advanced(False)

    def set_advanced(self, on: bool) -> None:
        for name in self.gauges:
            self.gauges[name].setVisible(on)
            self.labels[name].setVisible(on)
        self.output.setVisible(on)
        self.buttons.setVisible(on)

    def update_state(self, st: Optional[MoveState]) -> None:
        if st is None:
            self.subtitle.setText("not connected")
            return
        model = {"zcm1": "PS Move", "zcm2": "PS Move (PS4 model)", "simulated": "simulated"}.get(st.model, st.model)
        batt = "charging" if st.charging else f"battery {int(st.battery * 100)}%"
        track = "tracked" if st.tracker.tracked else "not tracked"
        hits = f" · hits {st.hit_count} ({st.last_hit})" if st.hit_count else ""
        rate = f" · {st.report_rate:.0f} Hz" if st.report_rate else ""
        self.subtitle.setText(f"{model} · {batt} · {track}{hits}{rate}")
        r, g, b = st.led
        self.swatch.setStyleSheet(f"border-radius:13px; background: rgb({r},{g},{b});")
        vals = {
            "trigger": st.trigger, "roll": st.roll, "pitch": st.pitch, "yaw": st.yaw,
            "track x": st.tracker.x, "track y": st.tracker.y, "depth": st.tracker.depth,
            "motion": st.accel.magnitude() - 1.0 if st.connected else 0.0,
        }
        for name, v in vals.items():
            lo, hi = self.ranges[name]
            self.gauges[name].set_value(abs(v) if name == "motion" else v, lo, hi)
        pressed = [n for n, d in st.buttons.items() if d]
        self.buttons.setText(("pressed: " + ", ".join(pressed)) if pressed else "")
        self.output.setText(st.output_status)
