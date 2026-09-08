"""AI Coach: record how you play, let Claude design a mapping, then chat to tune it."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from themover.ai.analyzer import GameAnalyzer
from themover.ai.chat import CoachChat
from themover.ai.client import ClaudeClient
from themover.ai.coachlog import CoachLog
from themover.ai.recorder import Recording, SessionRecorder
from themover.config import app_data_dir
from themover.ui.context import AppContext
from themover.ui.workers import Worker, run_in_background

log = logging.getLogger(__name__)


class _Host:
    """Adapts AppContext to the chat's ProfileHost protocol."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def get_profile(self):
        return self.ctx.profile

    def apply_profile(self, profile) -> None:
        # Runs on the chat worker thread.  The runtime is thread-safe and the UI
        # listens to ctx.profile_changed through queued connections, so this is
        # safe and - importantly - synchronous: a following get_profile tool call
        # sees the new profile immediately.
        self.ctx.apply_profile(profile, reason="chat")

    def live_signals(self):
        return self.ctx.live_signals()

    def buzz(self, controller, rumble, led, duration_ms) -> None:
        self.ctx.buzz(controller, rumble, led, duration_ms)

    def save_profile(self, name: str) -> str:
        key = self.ctx.save_as(name)
        return f"Saved a copy as “{self.ctx.profile.name}” ({key}); it is now the active profile."


class AITab(QWidget):
    rec_progress = Signal(float, float)
    rec_done = Signal(object)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.recorder: Optional[SessionRecorder] = None
        self.recording: Optional[Recording] = None
        self.chat: Optional[CoachChat] = None
        self._busy = False
        self._client: Optional[ClaudeClient] = None

        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Vertical)
        root.addWidget(splitter)

        # ------------------------------------------------ recording panel
        rec = QFrame(); rec.setObjectName("card")
        rl = QVBoxLayout(rec)
        title = QLabel("Record yourself playing with keyboard / mouse, then let the coach build motion controls for that game")
        title.setObjectName("h2"); title.setWordWrap(True)
        rl.addWidget(title)
        row = QHBoxLayout()
        self.game_edit = QLineEdit(); self.game_edit.setPlaceholderText("Game name")
        self.notes_edit = QLineEdit(); self.notes_edit.setPlaceholderText("Wishes (optional), e.g. 'swing to attack'")
        self.seconds = QSpinBox(); self.seconds.setRange(10, 300); self.seconds.setValue(ctx.settings.record_seconds); self.seconds.setSuffix(" s")
        self.record_btn = QPushButton("●  Record"); self.record_btn.setObjectName("danger")
        self.analyze_btn = QPushButton("✨  Build controls"); self.analyze_btn.setObjectName("accent2"); self.analyze_btn.setEnabled(False)
        row.addWidget(self.game_edit, 2); row.addWidget(self.notes_edit, 3); row.addWidget(self.seconds); row.addWidget(self.record_btn); row.addWidget(self.analyze_btn)
        rl.addLayout(row)
        self.progress = QProgressBar(); self.progress.setRange(0, 1000); self.progress.setValue(0)
        rl.addWidget(self.progress)
        self.rec_status = QLabel("Start your game, press Record, play normally until the bar fills."); self.rec_status.setObjectName("muted"); self.rec_status.setWordWrap(True)
        rl.addWidget(self.rec_status)
        splitter.addWidget(rec)

        # ---------------------------------------------------- chat panel
        chat = QFrame(); chat.setObjectName("card")
        cl = QVBoxLayout(chat)
        ct = QLabel("Chat to adjust anything  —  “steering is too twitchy”, “make jump a flick up”")
        ct.setObjectName("h2"); ct.setWordWrap(True)
        cl.addWidget(ct)
        self.history = QTextBrowser(); self.history.setOpenExternalLinks(True)
        cl.addWidget(self.history, 1)
        irow = QHBoxLayout()
        self.input = QLineEdit(); self.input.setPlaceholderText("Type a request and press Enter…")
        self.send_btn = QPushButton("Send"); self.send_btn.setObjectName("accent2")
        self.clear_btn = QPushButton("New chat")
        irow.addWidget(self.input, 1); irow.addWidget(self.send_btn); irow.addWidget(self.clear_btn)
        cl.addLayout(irow)
        splitter.addWidget(chat)
        splitter.setSizes([260, 420])

        self.record_btn.clicked.connect(self._toggle_record)
        self.analyze_btn.clicked.connect(self._analyze)
        self.send_btn.clicked.connect(self._send)
        self.input.returnPressed.connect(self._send)
        self.clear_btn.clicked.connect(self._new_chat)
        self.rec_progress.connect(self._on_rec_progress)
        self.rec_done.connect(self._on_rec_done)
        ctx.advanced_changed.connect(self.set_advanced)
        self.set_advanced(ctx.advanced)
        self.coach_log = CoachLog()
        self._recording_folder = ""
        self._append_system("Hi! Record a session and I'll design controls for that game, or tell me what to change in the current profile.")

    def set_advanced(self, on: bool) -> None:
        self.seconds.setVisible(on)
        self.clear_btn.setVisible(on)

    # ------------------------------------------------------------ helpers
    def _client_or_warn(self) -> Optional[ClaudeClient]:
        if not self.ctx.settings.effective_api_key:
            QMessageBox.information(self, "API key needed", "Paste your Anthropic API key in the Setup tab first.")
            return None
        if self._client is None:
            self._client = ClaudeClient(self.ctx.settings)
        return self._client

    def reset_client(self) -> None:
        self._client = None
        self.chat = None

    def _append(self, who: str, text: str, color: str) -> None:
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        self.history.append(f'<p style="margin:6px 0"><b style="color:{color}">{who}</b><br>{safe}</p>')
        self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())

    def _append_system(self, text: str) -> None:
        self._append("Coach", text, "#33e6ff")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.setEnabled(not busy)
        self.analyze_btn.setEnabled((not busy) and self.recording is not None)
        self.record_btn.setEnabled(not busy)

    # ----------------------------------------------------------- recording
    def _toggle_record(self) -> None:
        if self.recorder is not None and self.recorder.active:
            self.recorder.stop()
            return
        self.recorder = SessionRecorder(seconds=self.seconds.value(), fps=self.ctx.settings.record_fps)
        self.recorder.on_progress = self.rec_progress.emit  # recorder thread -> queued to GUI
        self.recorder.on_done = self.rec_done.emit
        self.recorder.start()
        self.record_btn.setText("■  Stop")
        self.rec_status.setText("Recording… play the game now!" + ("" if self.recorder.input_capture_ok else "  (keyboard/mouse capture unavailable - screenshots only)"))
        self.analyze_btn.setEnabled(False)

    def _on_rec_progress(self, elapsed: float, total: float) -> None:
        self.progress.setValue(int(1000 * elapsed / max(total, 1e-3)))

    def _on_rec_done(self, recording: Recording) -> None:
        self.recording = recording
        recording.game_hint = self.game_edit.text().strip()
        recording.notes = self.notes_edit.text().strip()
        self.record_btn.setText("●  Record")
        self.progress.setValue(1000)
        stats = recording.stats()
        self.rec_status.setText(
            f"Recorded {stats['duration_seconds']}s, {stats['frames']} screenshots, {len(stats['inputs'])} distinct inputs. "
            f"Mouse: {stats['mouse']['looks_like']}. Click Analyze."
        )
        self.analyze_btn.setEnabled(True)
        try:
            folder = app_data_dir() / "recordings" / time.strftime("%Y%m%d-%H%M%S")
            recording.save(folder)
            self._recording_folder = str(folder)
        except Exception as exc:
            log.warning("could not save recording: %s", exc)

    # ------------------------------------------------------------ analysis
    def _analyze(self) -> None:
        if self.recording is None or self._busy:
            return
        client = self._client_or_warn()
        if client is None:
            return
        self.recording.game_hint = self.game_edit.text().strip()
        self.recording.notes = self.notes_edit.text().strip()
        self._set_busy(True)
        self._append("You", f"Build controls for {self.recording.game_hint or 'my recording'}.", "#ff3fb4")
        self.rec_status.setText("The coach is studying your game… (this can take a minute)")
        current = self.ctx.profile if self.ctx.profile.bindings else None
        rec = self.recording
        max_frames = self.ctx.settings.max_frames_to_send
        folder = self._recording_folder
        clog = self.coach_log

        def job(signals):
            return GameAnalyzer(client, coach_log=clog).analyze(rec, max_frames=max_frames, current_profile=current, recording_folder=folder)

        w = Worker(job)
        w.signals.finished.connect(self._on_analysis)
        w.signals.error.connect(self._on_error)
        run_in_background(w)

    def _on_analysis(self, result) -> None:
        self._set_busy(False)
        p = result.profile
        self.ctx.profile_key = ""  # a fresh analysis always becomes its own library profile
        self.ctx.apply_profile(p, reason="analysis")
        msg = f"“{p.name}” is ready, active and saved to your profiles ({len(p.bindings)} controls).\n\nHow to play: {p.play_style}"
        analysis = result.analysis_text()
        if analysis:
            msg += "\n\n" + analysis
        if self.ctx.advanced:
            if p.notes:
                msg += f"\n\nDesign notes: {p.notes}"
            if result.problems:
                msg += "\n\n(Some entries were dropped: " + "; ".join(result.problems) + ")"
            if result.usage:
                msg += f"\n\n[{result.model}: {result.usage.get('input_tokens', 0)} in / {result.usage.get('output_tokens', 0)} out tokens, {result.seconds:.0f} s; report: {result.report_path}]"
        self._append_system(msg)
        self.rec_status.setText(f"Ready: {p.name}. Go to Play, or chat below to adjust.")

    def _on_error(self, text: str) -> None:
        self._set_busy(False)
        self._append("Error", text.split("\n\n")[0], "#f87171")
        log.error(text)

    # ---------------------------------------------------------------- chat
    def _new_chat(self) -> None:
        self.chat = None
        self.history.clear()
        self._append_system("New conversation. What should we change?")

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy:
            return
        client = self._client_or_warn()
        if client is None:
            return
        if self.chat is None:
            self.chat = CoachChat(client, _Host(self.ctx), coach_log=self.coach_log)
        self.input.clear()
        self._append("You", text, "#ff3fb4")
        self._set_busy(True)
        chat = self.chat
        self._stream_buffer = ""

        def job(signals):
            chat.on_tool = lambda name, args, out: signals.tool.emit(name, args, out)
            return chat.send(text, on_text=lambda t: signals.text.emit(t))

        w = Worker(job)
        w.signals.tool.connect(self._on_tool)
        w.signals.finished.connect(self._on_reply)
        w.signals.error.connect(self._on_error)
        run_in_background(w)

    def _on_tool(self, name: str, args, out: str) -> None:
        pretty = {
            "add_bindings": "added bindings", "modify_binding": "modified a binding", "remove_bindings": "removed bindings",
            "set_feedback": "updated feedback rules", "set_profile_meta": "updated profile info", "replace_profile": "replaced the profile",
            "buzz_controller": "buzzed a controller", "save_profile": "saved the profile", "get_profile": "read the profile",
            "read_live_signals": "read live controller signals",
        }.get(name, name)
        self.history.append(f'<p style="margin:2px 0;color:#9aa0b4"><i>⚙ {pretty}</i></p>')

    def _on_reply(self, text: str) -> None:
        self._set_busy(False)
        self._append_system(text or "(no reply)")
