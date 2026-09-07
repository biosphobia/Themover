"""User profile library (JSON files) plus the built-in templates."""
from __future__ import annotations

import re
from pathlib import Path

from themover.config import app_data_dir
from themover.mapping.profile import Profile
from themover.mapping.templates import TEMPLATES, load_template


def profiles_dir() -> Path:
    return app_data_dir() / "profiles"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "profile"


def list_profiles() -> dict[str, Profile]:
    """Built-in templates first, then the user's saved profiles."""
    out: dict[str, Profile] = {k: load_template(k) for k in TEMPLATES}
    for path in sorted(profiles_dir().glob("*.json")):
        try:
            out[f"user:{path.stem}"] = Profile.load(path)
        except Exception:
            continue
    return out


def save_profile(profile: Profile, name: str | None = None) -> Path:
    slug = slugify(name or profile.name)
    path = profiles_dir() / f"{slug}.json"
    profile.save(path)
    return path


def delete_profile(key: str) -> bool:
    if not key.startswith("user:"):
        return False
    path = profiles_dir() / f"{key[5:]}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def load_profile(key: str) -> Profile:
    if key in TEMPLATES:
        return load_template(key)
    if key.startswith("user:"):
        return Profile.load(profiles_dir() / f"{key[5:]}.json")
    raise KeyError(key)
