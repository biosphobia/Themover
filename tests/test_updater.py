"""In-app updater: archive validation, install swap, launcher override finder."""
import importlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from themover import updater as U
from themover.config import Settings

SHA_A = "a" * 40
SHA_B = "b" * 40


def make_archive(sha: str, top: str = "Themover-main", init_body: str = "VERSION = 'x'\n", requirements: str = "numpy\n", extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/README.md", "hi")
        zf.writestr(f"{top}/requirements.txt", requirements)
        zf.writestr(f"{top}/themover/__init__.py", init_body)
        zf.writestr(f"{top}/themover/ui/__init__.py", "")
        zf.writestr(f"{top}/themover/ui/app.py", "def run_app(argv): return 0\n")
        for name, body in (extra or {}).items():
            zf.writestr(f"{top}/{name}", body)
        zf.comment = sha.encode()
    return buf.getvalue()


def test_inspect_archive_reads_sha_and_rejects_junk():
    a = U.inspect_archive(make_archive(SHA_A), "main")
    assert a.sha == SHA_A and a.top == "Themover-main" and a.branch == "main"
    with pytest.raises(ValueError):
        U.inspect_archive(b"not a zip", "main")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Themover-main/README.md", "only a readme")
        zf.comment = SHA_A.encode()
    with pytest.raises(ValueError, match="does not contain"):
        U.inspect_archive(buf.getvalue(), "main")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Themover-main/themover/__init__.py", "")
    with pytest.raises(ValueError, match="no commit SHA"):
        U.inspect_archive(buf.getvalue(), "main")


def test_missing_requirements_maps_names():
    assert U.missing_requirements("numpy>=1.24\nopencv-python\npillow\nhidapi\nvgamepad; sys_platform == 'win32'\n# comment\n") == []
    assert U.missing_requirements("numpy\nsome-package-that-does-not-exist>=1\n") == ["some-package-that-does-not-exist"]


def test_install_swaps_package_and_writes_version(tmp_path):
    root = tmp_path / "app"
    a = U.inspect_archive(make_archive(SHA_A), "main")
    live = U.install_archive(a, root, message="first")
    assert (live / "__init__.py").read_text() == "VERSION = 'x'\n"
    assert (live / "ui" / "app.py").exists()
    v = json.loads((root / "version.json").read_text())
    assert v["sha"] == SHA_A and v["branch"] == "main" and v["message"] == "first"
    # A second install replaces the tree atomically and removes stale files.
    (live / "stale.py").write_text("old")
    b = U.inspect_archive(make_archive(SHA_B, init_body="VERSION = 'y'\n"), "main")
    U.install_archive(b, root)
    assert (live / "__init__.py").read_text() == "VERSION = 'y'\n"
    assert not (live / "stale.py").exists() and not (root / "themover.old").exists()
    assert json.loads((root / "version.json").read_text())["sha"] == SHA_B
    U.remove_override(root)
    assert not live.exists() and not (root / "version.json").exists()


def test_install_refuses_when_dependencies_are_missing(tmp_path):
    a = U.inspect_archive(make_archive(SHA_A, requirements="numpy\nbrand-new-dependency\n"), "main")
    with pytest.raises(RuntimeError, match="brand-new-dependency"):
        U.install_archive(a, tmp_path / "app")
    assert not (tmp_path / "app" / "themover").exists()


def test_updater_check_and_install(tmp_path, monkeypatch):
    calls = []

    def fetcher(url, token):
        calls.append((url, token))
        if "codeload" in url:
            return make_archive(SHA_B, top="Themover-feature-x")
        raise OSError("api blocked")

    monkeypatch.setattr(U, "current_version", lambda package_dir=None: U.VersionInfo(SHA_A, "feature/x", "", "bundle"))
    monkeypatch.setattr(U, "override_dir", lambda: tmp_path / "app")
    s = Settings(update_branch="", github_token="tok")
    up = U.Updater(s, fetcher=fetcher)
    assert up.branch == "feature/x"
    st = up.check()
    assert st.available and st.remote_sha == SHA_B and not st.error and "Update available" in st.text()
    assert calls[0][0] == U.archive_url("biosphobia", "Themover", "feature/x") and calls[0][1] == "tok"
    # Not frozen: refuses to swap code under a source checkout unless explicitly allowed.
    with pytest.raises(RuntimeError, match="running from source"):
        up.install()
    monkeypatch.setenv("THEMOVER_ALLOW_SOURCE_UPDATE", "1")
    live = up.install()
    assert live == tmp_path / "app" / "themover" and (live / "__init__.py").exists()
    # Same SHA -> up to date; network failure -> error status, never an exception.
    monkeypatch.setattr(U, "current_version", lambda package_dir=None: U.VersionInfo(SHA_B, "feature/x", "", "override"))
    assert not up.check().available and "Up to date" in up.status.text()
    bad = U.Updater(s, fetcher=lambda u, t: (_ for _ in ()).throw(OSError("offline")))
    assert "offline" in bad.check().error


def test_current_version_from_build_stamp_and_override(tmp_path, monkeypatch):
    pkg = tmp_path / "bundle" / "themover"
    pkg.mkdir(parents=True)
    (pkg / "_build.json").write_text(json.dumps({"sha": SHA_A, "branch": "main", "date": "2026-09-08 10:00:00"}))
    v = U.current_version(pkg)
    assert v.sha == SHA_A and v.source == "bundle" and "aaaaaaa" in v.describe() and "main" in v.describe()
    root = tmp_path / "app"
    monkeypatch.setattr(U, "override_dir", lambda: root)
    live = U.install_archive(U.inspect_archive(make_archive(SHA_B), "main"), root, message="m")
    v2 = U.current_version(live)
    assert v2.sha == SHA_B and v2.source == "override" and "updated in-app" in v2.describe()


def test_launcher_override_finder_prefers_downloaded_package(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import launcher

    # A package on sys.path (the "bundled" one) and a newer copy in the override root.
    bundled = tmp_path / "bundled" / "fakepkg"
    bundled.mkdir(parents=True)
    (bundled / "__init__.py").write_text("WHERE = 'bundled'\n")
    (bundled / "sub.py").write_text("WHERE = 'bundled'\n")
    override = tmp_path / "app" / "fakepkg"
    override.mkdir(parents=True)
    (override / "__init__.py").write_text("WHERE = 'override'\n")
    (override / "sub.py").write_text("WHERE = 'override'\n")
    monkeypatch.syspath_prepend(str(tmp_path / "bundled"))
    for m in [m for m in sys.modules if m.startswith("fakepkg")]:
        del sys.modules[m]
    assert launcher.install_override(str(tmp_path / "app"), "fakepkg")
    try:
        mod = importlib.import_module("fakepkg")
        sub = importlib.import_module("fakepkg.sub")
        assert mod.WHERE == "override" and sub.WHERE == "override"
    finally:
        launcher._drop_override("fakepkg")
    mod = importlib.import_module("fakepkg")
    assert mod.WHERE == "bundled"
    assert not launcher.install_override(str(tmp_path / "nothing-here"), "fakepkg")
    assert launcher.app_root().endswith("app")
