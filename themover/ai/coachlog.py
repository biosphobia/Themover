"""Persistent log of everything the AI Coach does, for later review and improvement.

Every analysis and every chat turn is appended to ``coach_log.jsonl`` in the
user's data folder; analyses also get a readable Markdown report next to the
recording.  Past feedback for the same game is fed back into new analyses.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from themover.config import app_data_dir

log = logging.getLogger(__name__)


def coach_dir() -> Path:
    d = app_data_dir() / "coach_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _thinking_text(message: Any) -> str:
    """Summarised thinking blocks of a response (empty unless display=summarized)."""
    parts = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", "") == "thinking":
            text = getattr(block, "thinking", "") or ""
            if text.strip():
                parts.append(text.strip())
    return "\n\n".join(parts)


class CoachLog:
    def __init__(self, folder: Optional[Path] = None) -> None:
        self.folder = Path(folder) if folder else coach_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "coach_log.jsonl"

    # ------------------------------------------------------------ writing
    def _append(self, entry: dict[str, Any]) -> None:
        entry = dict(entry, time=time.strftime("%Y-%m-%d %H:%M:%S"), ts=time.time())
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("could not write coach log: %s", exc)

    def log_analysis(self, *, game_hint: str, notes: str, recording_summary: str, recording_folder: str,
                     analysis: dict[str, Any], profile: dict[str, Any], problems: list[str], thinking: str,
                     model: str, usage: Optional[dict[str, Any]], seconds: float, raw_text: str = "") -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self._append({
            "kind": "analysis", "game_hint": game_hint, "notes": notes, "recording_folder": recording_folder,
            "recording_summary": recording_summary, "analysis": analysis, "profile": profile, "problems": problems,
            "thinking": thinking, "model": model, "usage": usage, "seconds": round(seconds, 1),
        })
        report = self.folder / f"analysis-{stamp}.md"
        try:
            report.write_text(self._render_report(game_hint, notes, recording_summary, analysis, profile, problems, thinking, model, usage, seconds), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write report: %s", exc)
        return report

    def log_chat(self, *, game: str, profile_name: str, user_text: str, reply: str, tool_calls: list[dict[str, Any]],
                 thinking: str, model: str, usage: Optional[dict[str, Any]], seconds: float) -> None:
        self._append({
            "kind": "chat", "game": game, "profile": profile_name, "user": user_text, "reply": reply,
            "tools": tool_calls, "thinking": thinking, "model": model, "usage": usage, "seconds": round(seconds, 1),
        })

    def log_error(self, kind: str, error: str, **extra: Any) -> None:
        self._append({"kind": f"{kind}_error", "error": error, **extra})

    @staticmethod
    def _render_report(game_hint, notes, summary, analysis, profile, problems, thinking, model, usage, seconds) -> str:
        lines = [f"# Coach analysis - {profile.get('name', '?')}", ""]
        lines.append(f"- Game hint: {game_hint or '(none)'}")
        if notes:
            lines.append(f"- Player notes: {notes}")
        lines.append(f"- Model: {model}  ·  {seconds:.0f} s  ·  usage: {usage}")
        lines += ["", "## What the coach saw", "", "```", summary, "```", ""]
        lines += ["## Analysis", ""]
        for key, value in (analysis or {}).items():
            if isinstance(value, list):
                lines.append(f"**{key}**")
                lines += [f"- {v}" for v in value]
            else:
                lines.append(f"**{key}**: {value}")
            lines.append("")
        if thinking:
            lines += ["## Reasoning summary", "", thinking, ""]
        lines += ["## Play style", "", profile.get("play_style", ""), "", "## Notes", "", profile.get("notes", ""), ""]
        lines += ["## Bindings", ""]
        for b in profile.get("bindings", []):
            lines.append(f"- {b.get('source')} -> {b.get('target')} ({b.get('mode', 'auto')}) {b.get('comment', '')}")
        if problems:
            lines += ["", "## Dropped entries", ""] + [f"- {p}" for p in problems]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------ reading
    def entries(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        try:
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            continue
        except OSError:
            return []
        return out[-limit:]

    def lessons_for(self, game: str, limit: int = 8) -> str:
        """Past player feedback about this game (chat requests + what changed)."""
        game = (game or "").strip().lower()
        if not game:
            return ""
        lines = []
        for e in self.entries():
            if e.get("kind") != "chat":
                continue
            if game not in (e.get("game") or "").lower() and game not in (e.get("profile") or "").lower():
                continue
            changes = ", ".join(t.get("name", "") for t in e.get("tools", []) if t.get("name") not in ("get_profile", "read_live_signals"))
            lines.append(f"- player asked: \"{e.get('user', '')[:160]}\"" + (f" -> coach did: {changes}" if changes else ""))
        if not lines:
            return ""
        return "PAST FEEDBACK from this player about this game (avoid repeating the same mistakes):\n" + "\n".join(lines[-limit:])
