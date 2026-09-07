#!/usr/bin/env bash
# Builds dist/TheMover (Linux/macOS) with PyInstaller.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
pyinstaller --noconfirm TheMover.spec
echo "Done. Executable in dist/"
