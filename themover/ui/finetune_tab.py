"""Fine-tune tab: record full controller data (+ video), tag the timeline, hand it to the coach."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from themover.ai.motion_capture import MotionRecorder, MotionSession, TAG_KINDS, auto_fit
from themover.core.hits import hit_config_from_dict
from themover.ui.context import AppContext
from themover.ui.timeline import TimelineWidget, VideoView
from themover.ui.widgets import WrapLabel
from themover.ui.workers import Worker, run_in_background

log = logging.getLogger(__name__)


class FinetuneTab(QWidget):
    send_to_coach = Signal(object, str, bool)  # session, explanation, complete
    rec_progress = Signal(float, float)
    rec_done = Signal(object)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder: Optional[MotionRecorder] = None
        self.session: Optional[MotionSession] = None
        self._playing = False
        self._advanced_widgets: list[QWidget] = []
        root = QVBoxLayout(self)

        # ------------------------------------------------------ recorder
        rec = QFrame(); rec.setObjectName("card"); rl = QVBoxLayout(rec)
        title = QLabel("Record real strokes, tag where each hit should register, let the coach fit the detector")
        title.setObjectName("h2"); title.setWordWrap(True)
        rl.addWidget(title)
        row = QHBoxLayout()
        self.seconds = QSpinBox(); self.seconds.setRange(5, 300); self.seconds.setValue(20); self.seconds.setSuffix(" s")
        self.cam_cb = QCheckBox("camera footage"); self.cam_cb.setChecked(True)
        self.screen_cb = QCheckBox("screen footage"); self.screen_cb.setChecked(False)
        self.input_cb = QCheckBox("keyboard / mouse"); self.input_cb.setChecked(False)
        self.record_btn = QPushButton("●  Record"); self.record_btn.setObjectName("danger")
        self.load_combo = QComboBox(); self.load_combo.setMinimumWidth(200)
        self.load_btn = QPushButton("Load")
        row.addWidget(QLabel("Length")); row.addWidget(self.seconds); row.addWidget(self.cam_cb); row.addWidget(self.screen_cb); row.addWidget(self.input_cb)
        row.addWidget(self.record_btn); row.addStretch(1); row.addWidget(QLabel("Previous:")); row.addWidget(self.load_combo); row.addWidget(self.load_btn)
        rl.addLayout(row)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000)
        rl.addWidget(self.progress)
        self.status = WrapLabel("Pick the profile you want to tune on the Play tab first (e.g. osu! taiko), then Record and play a few clear strokes.")
        rl.addWidget(self.status)
        root.addWidget(rec)

        # ------------------------------------------------------ viewer
        view = QHBoxLayout()
        self.cam_view = VideoView("camera")
        self.screen_view = VideoView("screen")
        view.addWidget(self.cam_view, 1); view.addWidget(self.screen_view, 1)
        root.addLayout(view, 1)
        self.timeline = TimelineWidget()
        root.addWidget(self.timeline, 2)

        transport = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play"); self.play_btn.setCheckable(True)
        self.step_back = QPushButton("◀ 10 ms"); self.step_fwd = QPushButton("10 ms ▶")
        self.zoom_in = QPushButton("Zoom +"); self.zoom_out = QPushButton("Zoom −")
        self.time_lbl = QLabel("0.000 s")
        for w in (self.play_btn, self.step_back, self.step_fwd, self.zoom_in, self.zoom_out, self.time_lbl):
            transport.addWidget(w)
        transport.addSpacing(20)
        transport.addWidget(QLabel("Tag at cursor:"))
        self.tag_kind = QComboBox(); self.tag_kind.addItems(list(TAG_KINDS))
        self.tag_hand = QComboBox(); self.tag_hand.addItems(["either hand", "right hand", "left hand"])
        self.tag_note = QLineEdit(); self.tag_note.setPlaceholderText("note (optional)")
        self.tag_btn = QPushButton("+ Tag"); self.tag_btn.setObjectName("accent2")
        transport.addWidget(self.tag_kind); transport.addWidget(self.tag_hand); transport.addWidget(self.tag_note, 1); transport.addWidget(self.tag_btn)
        root.addLayout(transport)
        hint = WrapLabel("Click / drag the timeline to move, wheel to zoom, Shift+wheel to pan. Keys: ← → step, Space play, D = don tag, K = kat tag, Delete removes the selected tag.")
        root.addWidget(hint)

        bottom = QHBoxLayout()
        self.tag_table = QTableWidget(0, 4)
        self.tag_table.setHorizontalHeaderLabels(["Time", "Tag", "Hand", "Note"])
        self.tag_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tag_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tag_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tag_table.verticalHeader().setVisible(False)
        self.tag_table.setMaximumHeight(140)
        bottom.addWidget(self.tag_table, 2)
        side = QVBoxLayout()
        self.del_tag_btn = QPushButton("Remove selected tag")
        self.explain = QLineEdit(); self.explain.setPlaceholderText("Explain to the coach, e.g. 'the second hit is a kat, it was missed'")
        self.complete_cb = QCheckBox("I tagged every real hit (extra detections count as mistakes)")
        self.autofit_btn = QPushButton("Auto-fit locally (no AI)")
        self.send_btn = QPushButton("✨  Send everything to the coach"); self.send_btn.setObjectName("accent2")
        self.result_lbl = WrapLabel("")
        side.addWidget(self.del_tag_btn); side.addWidget(self.explain); side.addWidget(self.complete_cb)
        brow = QHBoxLayout(); brow.addWidget(self.autofit_btn); brow.addWidget(self.send_btn); side.addLayout(brow)
        side.addWidget(self.result_lbl)
        bottom.addLayout(side, 3)
        root.addLayout(bottom)

        # ------------------------------------------------------ wiring
        self.record_btn.clicked.connect(self._toggle_record)
        self.load_btn.clicked.connect(self._load_selected)
        self.rec_progress.connect(self._on_progress)
        self.rec_done.connect(self._on_done)
        self.timeline.cursor_changed.connect(self._on_cursor)
        self.play_btn.toggled.connect(self._toggle_play)
        self.step_back.clicked.connect(lambda: self._step(-0.01))
        self.step_fwd.clicked.connect(lambda: self._step(0.01))
        self.zoom_in.clicked.connect(lambda: self.timeline.zoom(0.7))
        self.zoom_out.clicked.connect(lambda: self.timeline.zoom(1.4))
        self.tag_btn.clicked.connect(self._add_tag)
        self.del_tag_btn.clicked.connect(self._remove_tag)
        self.tag_table.itemSelectionChanged.connect(self._select_tag)
        self.autofit_btn.clicked.connect(self._autofit)
        self.send_btn.clicked.connect(self._send)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._play_tick)
        self._set_session_controls(False)
        self.refresh_sessions()
        for w in (self.input_cb, self.screen_cb):
            self._advanced_widgets.append(w)
        ctx.advanced_changed.connect(self.set_advanced)
        self.set_advanced(ctx.advanced)

    def set_advanced(self, on: bool) -> None:
        for w in self._advanced_widgets:
            w.setVisible(on)

    # ------------------------------------------------------------ record
    def refresh_sessions(self) -> None:
        self.load_combo.clear()
        for p in MotionSession.list_sessions()[:30]:
            self.load_combo.addItem(p.name, str(p))

    def _toggle_record(self) -> None:
        if self.recorder is not None and self.recorder.active:
            self.recorder.stop()
            return
        self.recorder = MotionRecorder(
            self.ctx.runtime, seconds=self.seconds.value(), camera=self.cam_cb.isChecked(),
            screen=self.screen_cb.isChecked(), inputs=self.input_cb.isChecked(), explanation=self.explain.text().strip(),
        )
        self.recorder.on_progress = self.rec_progress.emit
        self.recorder.on_done = self.rec_done.emit
        try:
            self.recorder.start()
        except Exception as exc:
            self.status.setText(f"Could not start recording: {exc}")
            return
        self.record_btn.setText("■  Stop")
        self.status.setText("Recording… play your strokes now.")

    def _on_progress(self, elapsed: float, total: float) -> None:
        self.progress.setValue(int(1000 * elapsed / max(total, 1e-3)))

    def _on_done(self, session: MotionSession) -> None:
        self.record_btn.setText("●  Record")
        self.progress.setValue(1000)
        self.set_session(session)
        self.refresh_sessions()
        n = sum(len(t) for t in session.t)
        self.status.setText(f"Recorded {session.duration:.1f} s: {n} controller samples, {len(session.hits)} hits detected, video: camera={'yes' if session.camera_video else 'no'} screen={'yes' if session.screen_video else 'no'}. Saved to {session.folder}. Now drop tags where hits should register.")

    def _load_selected(self) -> None:
        path = self.load_combo.currentData()
        if not path:
            return
        try:
            self.set_session(MotionSession.load(Path(path)))
            self.status.setText(f"Loaded {Path(path).name}.")
        except Exception as exc:
            QMessageBox.warning(self, "Load failed", str(exc))

    # ------------------------------------------------------------ session
    def set_session(self, session: Optional[MotionSession]) -> None:
        if self.session is not None and self.session is not session:
            self.session.close()
        self.session = session
        self.timeline.set_session(session)
        self._set_session_controls(session is not None)
        self._refresh_tags()
        self._on_cursor(0.0)
        self.result_lbl.setText("")

    def _set_session_controls(self, on: bool) -> None:
        for w in (self.play_btn, self.step_back, self.step_fwd, self.zoom_in, self.zoom_out, self.tag_btn, self.del_tag_btn, self.autofit_btn, self.send_btn):
            w.setEnabled(on)

    def _on_cursor(self, t: float) -> None:
        self.time_lbl.setText(f"{t:.3f} s")
        if self.session is None:
            return
        frame = self.session.frame_at("camera", t)
        if frame is not None:
            self.cam_view.show_frame(frame)
        frame = self.session.frame_at("screen", t)
        if frame is not None:
            self.screen_view.show_frame(frame)

    def _step(self, dt: float) -> None:
        self.timeline.set_cursor(self.timeline.cursor + dt)

    def _toggle_play(self, on: bool) -> None:
        self._playing = on
        self.play_btn.setText("❚❚ Pause" if on else "▶ Play")
        if on:
            self._timer.start(33)
        else:
            self._timer.stop()

    def _play_tick(self) -> None:
        if self.session is None:
            return
        t = self.timeline.cursor + 0.033
        if t >= self.session.duration:
            self.play_btn.setChecked(False)
            t = self.session.duration
        self.timeline.set_cursor(t)

    # ------------------------------------------------------------ tags
    def _add_tag(self, kind: Optional[str] = None) -> None:
        if self.session is None:
            return
        kind = kind or self.tag_kind.currentText()
        hand = {0: -1, 1: 0, 2: 1}[self.tag_hand.currentIndex()]
        self.session.add_tag(self.timeline.cursor, kind, hand, self.tag_note.text().strip())
        self.session.save_tags()
        self.tag_note.clear()
        self._refresh_tags()
        self.timeline.update()

    def _remove_tag(self) -> None:
        if self.session is None:
            return
        rows = self.tag_table.selectionModel().selectedRows()
        if not rows:
            return
        self.session.remove_tag(rows[0].row())
        self.session.save_tags()
        self._refresh_tags()
        self.timeline.update()

    def _select_tag(self) -> None:
        if self.session is None:
            return
        rows = self.tag_table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self.session.tags):
            self.timeline.set_cursor(self.session.tags[rows[0].row()].t)

    def _refresh_tags(self) -> None:
        tags = self.session.tags if self.session else []
        self.tag_table.setRowCount(len(tags))
        for r, tag in enumerate(tags):
            for c, text in enumerate([f"{tag.t:.3f}", tag.kind, {0: "right", 1: "left"}.get(tag.hand, "either"), tag.note]):
                self.tag_table.setItem(r, c, QTableWidgetItem(text))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key_Left:
            self._step(-0.1 if event.modifiers() & Qt.ShiftModifier else -0.01)
        elif key == Qt.Key_Right:
            self._step(0.1 if event.modifiers() & Qt.ShiftModifier else 0.01)
        elif key == Qt.Key_Space:
            self.play_btn.toggle()
        elif key == Qt.Key_D:
            self._add_tag("don")
        elif key == Qt.Key_K:
            self._add_tag("kat")
        elif key == Qt.Key_Delete:
            self._remove_tag()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------ actions
    def _autofit(self) -> None:
        if self.session is None:
            return
        if not any(t.kind in ("don", "kat") for t in self.session.tags):
            self.result_lbl.setText("Add at least one don / kat tag first.")
            return
        self.result_lbl.setText("Fitting…")
        session, complete = self.session, self.complete_cb.isChecked()
        base = dict(self.ctx.profile.hit_config)

        def job(signals):
            return auto_fit(session, base, complete, progress=lambda d, n: signals.progress.emit(d, n))

        w = Worker(job)
        w.signals.finished.connect(self._on_autofit)
        w.signals.error.connect(lambda e: self.result_lbl.setText("Auto-fit failed: " + e.split("\n")[0]))
        run_in_background(w)

    def _on_autofit(self, result) -> None:
        cfg, res = result
        applied = self.ctx.runtime.apply_hit_config(cfg)
        self.ctx.apply_profile(self.ctx.profile, reason="edited")
        self.timeline.replay_hits = self.session.replay(hit_config_from_dict(applied), 0) + self.session.replay(hit_config_from_dict(applied), 1)
        self.timeline.update()
        self.result_lbl.setText(f"Applied: {res.text()}  (hollow markers = hits with the new settings). Settings: " + ", ".join(f"{k}={v:g}" for k, v in applied.items() if k in ("onset_g", "stop_g", "kat_angle_deg", "refractory_s")))

    def _send(self) -> None:
        if self.session is None:
            return
        self.session.meta["explanation"] = self.explain.text().strip()
        try:
            self.session.save()
        except Exception as exc:
            log.warning("save failed: %s", exc)
        self.send_to_coach.emit(self.session, self.explain.text().strip(), self.complete_cb.isChecked())
        self.result_lbl.setText("Sent to the coach - see the AI Coach tab.")
