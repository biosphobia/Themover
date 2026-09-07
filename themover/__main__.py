"""Entry point: ``python -m themover`` or the packaged executable."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--headless" in argv:
        # Run the engine without a window (useful for testing or for scripts).
        from themover.headless import run_headless

        return run_headless(argv)
    from themover.ui.app import run_app

    return run_app(argv)


if __name__ == "__main__":  # pragma: no cover - exercised by the packaged exe
    sys.exit(main())
