from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_root() -> Path:
    env = os.environ.get("T9FOX_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Runtime paths; override with T9FOX_ROOT / T9FOX_CACHE_DIR."""

    root: Path
    cache_dir: Path

    @classmethod
    def load(cls) -> Settings:
        root = _default_root()
        cache = os.environ.get("T9FOX_CACHE_DIR")
        cache_dir = Path(cache).expanduser().resolve() if cache else root / "data" / "cache"
        return cls(root=root, cache_dir=cache_dir)


def ensure_cache_dir(settings: Settings | None = None) -> Path:
    s = settings or Settings.load()
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    return s.cache_dir
