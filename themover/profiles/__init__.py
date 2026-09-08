"""The profile library: one JSON file per profile in the user's data folder.

Built-in templates are *seeded* into the library on first run, so they are
ordinary, fully editable profiles like anything the coach or the player
creates.  The pristine template stays available for "Reset to default" and
"New from template".  Keys look like ``user:<slug>``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from themover.config import app_data_dir
from themover.mapping.profile import Profile
from themover.mapping.templates import TEMPLATES, load_template

SEED_MARKER = ".seeded"


def profiles_dir() -> Path:
    d = app_data_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "profile"


def key_for(slug: str) -> str:
    return f"user:{slug}"


def slug_of(key: str) -> str:
    return key[5:] if key.startswith("user:") else key


def path_for(key: str) -> Path:
    return profiles_dir() / f"{slug_of(key)}.json"


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while path_for(slug).exists():
        slug = f"{base}_{n}"
        n += 1
    return slug


# ------------------------------------------------------------------ seeding
def seed_templates(force: bool = False) -> list[str]:
    """Copy built-in templates into the library (first run, or on demand).

    Returns the keys that were written.  Without ``force`` this only runs once
    per library, so a template the player deleted stays deleted.
    """
    d = profiles_dir()
    marker = d / SEED_MARKER
    if marker.exists() and not force:
        return []
    written = []
    for key in TEMPLATES:
        path = d / f"{key}.json"
        if path.exists():
            continue
        p = load_template(key)
        p.based_on = key
        p.save(path)
        written.append(key_for(key))
    marker.write_text("built-in templates seeded\n", encoding="utf-8")
    return written


def restore_template(key: str) -> str:
    """Overwrite (or recreate) the library copy of a template with the pristine version."""
    tkey = slug_of(key)
    if tkey not in TEMPLATES:
        raise KeyError(key)
    p = load_template(tkey)
    p.based_on = tkey
    p.save(path_for(tkey))
    return key_for(tkey)


# ------------------------------------------------------------------ CRUD
def list_profiles() -> dict[str, Profile]:
    """Every profile in the library, seeding the templates on first use."""
    seed_templates()
    out: dict[str, Profile] = {}
    for path in sorted(profiles_dir().glob("*.json")):
        try:
            out[key_for(path.stem)] = Profile.load(path)
        except Exception:
            continue
    # Templates first (in their canonical order), then everything else by name.
    order = {key_for(k): i for i, k in enumerate(TEMPLATES)}
    return dict(sorted(out.items(), key=lambda kv: (order.get(kv[0], 10_000), kv[1].name.lower())))


def resolve_key(key: str) -> str:
    """Accept old-style template keys ('driving_wheel') and map them to library keys."""
    if not key:
        return ""
    if key.startswith("user:"):
        return key
    if key in TEMPLATES:
        seed_templates()
        if not path_for(key).exists():
            restore_template(key)
        return key_for(key)
    return key_for(key)


def load_profile(key: str) -> Profile:
    seed_templates()
    key = resolve_key(key)
    path = path_for(key)
    if not path.exists():
        raise KeyError(key)
    return Profile.load(path)


def save_profile(profile: Profile, key: Optional[str] = None, name: Optional[str] = None) -> str:
    """Write ``profile``; to the existing ``key`` or as a new file named after ``name``."""
    if name:
        profile.name = name
    if key and key.startswith("user:"):
        path = path_for(key)
    else:
        slug = unique_slug(profile.name)
        path = path_for(slug)
        key = key_for(slug)
    profile.save(path)
    return key


def create_from_template(template_key: str, name: Optional[str] = None) -> tuple[str, Profile]:
    """A brand-new library profile copied from a built-in template."""
    p = load_template(template_key)
    p.based_on = template_key
    if name:
        p.name = name
    else:
        # "Driving wheel" -> "Driving wheel 2" if the plain name is taken.
        existing = {v.name.lower() for v in list_profiles().values()}
        if p.name.lower() in existing:
            n = 2
            while f"{p.name} {n}".lower() in existing:
                n += 1
            p.name = f"{p.name} {n}"
    key = save_profile(p)
    return key, p


def delete_profile(key: str) -> bool:
    path = path_for(key)
    if path.exists():
        path.unlink()
        return True
    return False
