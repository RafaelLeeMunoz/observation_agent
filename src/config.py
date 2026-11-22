"""Pipeline settings from config/settings.yaml (one place for every tunable number)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "settings.yaml"


def load(path: Path = SETTINGS) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def settings_hash(path: Path = SETTINGS) -> str:
    """Part of every later stage's cache fingerprint: a settings change reruns the affected work."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
