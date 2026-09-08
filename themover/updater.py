"""Update The Mover straight from GitHub pushes, without rebuilding the executable.

The executable is a *launcher*: it bundles Python and every dependency plus a
copy of the ``themover`` package.  On start (see ``launcher.py``) it prefers a
newer ``themover`` package found in the user's app folder.  This module fills
that folder: it downloads the branch archive from GitHub (the zip comment is
the commit SHA, so no API call or token is needed), verifies it, checks that
every dependency the new code needs is already bundled, and swaps it in.
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from themover import __version__
from themover.config import Settings, app_data_dir

log = logging.getLogger(__name__)

DEFAULT_OWNER = "biosphobia"
DEFAULT_REPO = "Themover"
PACKAGE = "themover"
OVERRIDE_DIRNAME = "app"

# requirement name -> module to import (only names that differ)
_IMPORT_NAMES = {"opencv-python": "cv2", "opencv-python-headless": "cv2", "pillow": "PIL", "hidapi": "hid", "pyside6": "PySide6"}
_OPTIONAL = {"vgamepad", "pseyepy"}


def override_dir() -> Path:
    return app_data_dir() / OVERRIDE_DIRNAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #
@dataclass
class VersionInfo:
    sha: str = ""
    branch: str = ""
    date: str = ""
    source: str = ""  # override | bundle | git | unknown
    message: str = ""

    @property
    def short(self) -> str:
        return self.sha[:7] if self.sha else "unknown"

    def describe(self) -> str:
        parts = [f"v{__version__}", self.short]
        if self.branch:
            parts.append(self.branch)
        if self.date:
            parts.append(self.date[:10])
        if self.source == "override":
            parts.append("(updated in-app)")
        return " · ".join(parts)


def current_version(package_dir: Optional[Path] = None) -> VersionInfo:
    """Where does the code we are running come from, and which commit is it?"""
    pkg = package_dir or Path(__file__).resolve().parent
    # 1. Installed by the updater.
    vfile = pkg.parent / "version.json"
    if vfile.exists() and pkg.parent.resolve() == override_dir().resolve():
        try:
            data = json.loads(vfile.read_text(encoding="utf-8"))
            return VersionInfo(data.get("sha", ""), data.get("branch", ""), data.get("date", ""), "override", data.get("message", ""))
        except (OSError, ValueError):
            pass
    # 2. Written by CI into the bundle.
    bfile = pkg / "_build.json"
    if bfile.exists():
        try:
            data = json.loads(bfile.read_text(encoding="utf-8"))
            return VersionInfo(data.get("sha", ""), data.get("branch", ""), data.get("date", ""), "bundle", data.get("message", ""))
        except (OSError, ValueError):
            pass
    # 3. Running from a git checkout.
    try:
        root = pkg.parent
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        if sha:
            return VersionInfo(sha, branch, "", "git")
    except Exception:
        pass
    return VersionInfo(source="unknown")


# --------------------------------------------------------------------------- #
# Remote archive
# --------------------------------------------------------------------------- #
def archive_url(owner: str, repo: str, branch: str) -> str:
    return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"


def fetch(url: str, token: str = "", timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"TheMover/{__version__}"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return resp.read()


@dataclass
class RemoteArchive:
    sha: str
    branch: str
    data: bytes = field(repr=False)
    top: str = ""


def inspect_archive(data: bytes, branch: str) -> RemoteArchive:
    """Validate a GitHub branch archive and read the commit SHA from its comment."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("downloaded file is not a zip archive") from exc
    names = zf.namelist()
    if not names:
        raise ValueError("archive is empty")
    top = names[0].split("/", 1)[0]
    if not any(n == f"{top}/{PACKAGE}/__init__.py" for n in names):
        raise ValueError(f"archive does not contain the {PACKAGE} package (wrong branch?)")
    sha = zf.comment.decode("ascii", "ignore").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("archive has no commit SHA")
    return RemoteArchive(sha=sha, branch=branch, data=data, top=top)


def missing_requirements(requirements_text: str) -> list[str]:
    """Names from requirements.txt whose module cannot be imported in this runtime."""
    missing = []
    for line in requirements_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~;\[ ]", line, 1)[0].strip().lower()
        if not name or name in _OPTIONAL:
            continue
        module = _IMPORT_NAMES.get(name, name.replace("-", "_"))
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return missing


def install_archive(archive: RemoteArchive, target_root: Optional[Path] = None, message: str = "") -> Path:
    """Unpack ``themover/`` from the archive into the override folder (atomic swap)."""
    root = target_root or override_dir()
    root.mkdir(parents=True, exist_ok=True)
    zf = zipfile.ZipFile(io.BytesIO(archive.data))
    prefix = f"{archive.top}/{PACKAGE}/"
    req_name = f"{archive.top}/requirements.txt"
    if req_name in zf.namelist():
        missing = missing_requirements(zf.read(req_name).decode("utf-8", "ignore"))
        if missing:
            raise RuntimeError(
                "this update needs packages that are not in the executable: " + ", ".join(missing)
                + ". Download a new TheMover.exe for it."
            )
    new_dir = root / f"{PACKAGE}.new"
    old_dir = root / f"{PACKAGE}.old"
    for d in (new_dir, old_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    for name in zf.namelist():
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = name[len(prefix):]
        if ".." in rel.split("/"):
            continue
        dest = new_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(name))
    if not (new_dir / "__init__.py").exists():
        shutil.rmtree(new_dir, ignore_errors=True)
        raise RuntimeError("update archive was incomplete")
    live = root / PACKAGE
    if live.exists():
        os.replace(live, old_dir)
    os.replace(new_dir, live)
    shutil.rmtree(old_dir, ignore_errors=True)
    (root / "version.json").write_text(
        json.dumps({"sha": archive.sha, "branch": archive.branch, "date": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message}, indent=2),
        encoding="utf-8",
    )
    return live


def remove_override(root: Optional[Path] = None) -> None:
    """Go back to the code bundled in the executable."""
    root = root or override_dir()
    shutil.rmtree(root / PACKAGE, ignore_errors=True)
    try:
        (root / "version.json").unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# High-level updater used by the UI
# --------------------------------------------------------------------------- #
@dataclass
class UpdateStatus:
    current: VersionInfo
    remote_sha: str = ""
    available: bool = False
    message: str = ""
    error: str = ""

    def text(self) -> str:
        if self.error:
            return f"Update check failed: {self.error}"
        if self.available:
            return f"Update available: {self.remote_sha[:7]} (you run {self.current.short}). " + (self.message or "")
        return f"Up to date ({self.current.short})."


class Updater:
    def __init__(self, settings: Settings, fetcher: Optional[Callable[[str, str], bytes]] = None) -> None:
        self.settings = settings
        self._fetch = fetcher or (lambda url, token: fetch(url, token))
        self.last_archive: Optional[RemoteArchive] = None
        self.status: Optional[UpdateStatus] = None

    @property
    def branch(self) -> str:
        return self.settings.update_branch or current_version().branch or "main"

    @property
    def can_install(self) -> bool:
        """In-app code swaps only make sense for the packaged executable (or an explicit override)."""
        return is_frozen() or bool(os.environ.get("THEMOVER_ALLOW_SOURCE_UPDATE"))

    def check(self) -> UpdateStatus:
        cur = current_version()
        try:
            data = self._fetch(archive_url(self.settings.update_owner, self.settings.update_repo, self.branch), self.settings.github_token)
            archive = inspect_archive(data, self.branch)
        except Exception as exc:
            self.status = UpdateStatus(current=cur, error=str(exc))
            return self.status
        self.last_archive = archive
        self.status = UpdateStatus(current=cur, remote_sha=archive.sha, available=(archive.sha != cur.sha), message=self._commit_message(archive.sha))
        return self.status

    def _commit_message(self, sha: str) -> str:
        """Best effort: the commit subject from the API (may be rate limited / blocked)."""
        try:
            raw = self._fetch(
                f"https://api.github.com/repos/{self.settings.update_owner}/{self.settings.update_repo}/commits/{sha}",
                self.settings.github_token,
            )
            data = json.loads(raw)
            return (data.get("commit", {}).get("message", "") or "").splitlines()[0][:120]
        except Exception:
            return ""

    def install(self) -> Path:
        if self.last_archive is None:
            self.check()
        if self.status is None or self.status.error:
            raise RuntimeError(self.status.error if self.status else "no archive")
        if not self.can_install:
            raise RuntimeError("You are running from source: use `git pull` instead of the in-app updater.")
        return install_archive(self.last_archive, message=self.status.message)

    @staticmethod
    def restart_command() -> list[str]:
        if is_frozen():
            return [sys.executable] + sys.argv[1:]
        return [sys.executable, "-m", PACKAGE] + sys.argv[1:]

    def relaunch(self) -> None:
        """Start a fresh process; the caller quits the current one afterwards."""
        subprocess.Popen(self.restart_command(), close_fds=True)  # noqa: S603
