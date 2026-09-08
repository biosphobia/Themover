"""Run the engine without a window: ``python -m themover --headless --profile driving_wheel``."""
from __future__ import annotations

import argparse
import logging
import time

from themover.config import load_settings
from themover.mapping.runtime import Runtime
from themover.profiles import list_profiles, load_profile


def run_headless(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="themover --headless")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--profile", default=None, help="profile key, e.g. user:driving_wheel (template names are accepted too)")
    parser.add_argument("--dry-run", action="store_true", help="do not send input to the OS")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = until Ctrl+C)")
    parser.add_argument("--list", action="store_true", help="list available profiles")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list:
        for key, p in list_profiles().items():
            print(f"{key:24s} {p.name} - {p.game}")
        return 0
    settings = load_settings()
    rt = Runtime(settings)
    key = args.profile or settings.last_profile
    rt.set_profile(load_profile(key))
    rt.start()
    if not args.dry_run:
        rt.arm()
    print(f"The Mover running profile '{key}' ({'dry run' if args.dry_run else rt.sink.description}). Ctrl+C to stop.")
    t0 = time.monotonic()
    try:
        while True:
            time.sleep(1.0)
            snap = rt.signal_snapshot()
            print({k: v for k, v in snap.items() if k.endswith(("roll", "pitch", "trigger", "track.x", "wheel.angle"))})
            if args.seconds and time.monotonic() - t0 >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        rt.stop()
    return 0
