"""Entry point of the packaged executable.

Loads ``themover`` from the user's app folder when the in-app updater has put
a newer copy there, otherwise from the copy bundled in the executable.  A
broken override (import error) is moved aside and the bundled copy is used.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import shutil
import sys
import traceback

PACKAGE = "themover"


def app_root() -> str:
    override = os.environ.get("THEMOVER_HOME")
    if override:
        return os.path.join(override, "app")
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TheMover", "app")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "TheMover", "app")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "themover", "app")


class OverrideFinder(importlib.abc.MetaPathFinder):
    """Serve one package (and its submodules) from a directory ahead of everything else."""

    def __init__(self, root: str, package: str = PACKAGE) -> None:
        self.root = root
        self.package = package

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.package and not fullname.startswith(self.package + "."):
            return None
        search = [self.root] if fullname == self.package else path
        if not search:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, list(search))


def install_override(root: str, package: str = PACKAGE) -> bool:
    if not os.path.exists(os.path.join(root, package, "__init__.py")):
        return False
    sys.meta_path.insert(0, OverrideFinder(root, package))
    return True


def _drop_override(package: str = PACKAGE) -> None:
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, OverrideFinder)]
    for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
        del sys.modules[name]


def main() -> int:
    root = app_root()
    used_override = "--no-update" not in sys.argv and install_override(root)
    try:
        from themover.__main__ import main as app_main
    except Exception:
        if not used_override:
            raise
        # The downloaded code does not import: park it and fall back to the bundled copy.
        traceback.print_exc()
        broken = os.path.join(root, PACKAGE + ".broken")
        shutil.rmtree(broken, ignore_errors=True)
        try:
            os.replace(os.path.join(root, PACKAGE), broken)
        except OSError:
            pass
        _drop_override()
        from themover.__main__ import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(main())
