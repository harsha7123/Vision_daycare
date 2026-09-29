"""YAML config loading with profile overlays and a tiny .env reader."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]   # repo root (src/daycare/config.py -> ../../)


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve(path: str | os.PathLike) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_yaml(path: str | os.PathLike) -> dict:
    with open(resolve(path), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_rules(profile: str = "production") -> dict:
    """rules.yaml, optionally overlaid with rules.<profile>.yaml."""
    cfg = load_yaml("configs/rules.yaml")
    if profile and profile != "production":
        overlay = resolve(f"configs/rules.{profile}.yaml")
        if not overlay.exists():
            raise FileNotFoundError(f"unknown rules profile {profile!r}: {overlay}")
        cfg = deep_merge(cfg, load_yaml(overlay))
    return cfg


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal KEY=VALUE loader; existing environment variables win."""
    p = resolve(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
