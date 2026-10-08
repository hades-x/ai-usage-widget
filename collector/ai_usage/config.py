"""Path resolution. Honours HOME, CODEX_HOME, CLAUDE_CONFIG_DIR, XDG_RUNTIME_DIR, XDG_CACHE_HOME.

CLI overrides win over the environment. ``env`` is injectable so tests never touch the
real environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class Paths:
    codex_home: Path
    claude_homes: Tuple[Path, ...]
    state: Path
    cache_dir: Path

    @property
    def codex_session_dirs(self) -> Tuple[Path, ...]:
        return (self.codex_home / "sessions", self.codex_home / "archived_sessions")

    @property
    def claude_project_dirs(self) -> Tuple[Path, ...]:
        return tuple(home / "projects" for home in self.claude_homes)

    def claude_credentials(self) -> Optional[Path]:
        """First existing ``.credentials.json`` among the Claude homes (read-only use)."""
        for home in self.claude_homes:
            candidate = home / ".credentials.json"
            if candidate.is_file():
                return candidate
        return None

    @property
    def index_path(self) -> Path:
        return self.cache_dir / "index.json"

    @property
    def claude_cache_path(self) -> Path:
        return self.cache_dir / "claude_usage.json"


def _split_paths(value: str) -> Tuple[Path, ...]:
    return tuple(Path(part.strip()).expanduser() for part in value.split(",") if part.strip())


def resolve_paths(
    env: Optional[Mapping[str, str]] = None,
    *,
    home: Optional[Path] = None,
    uid: Optional[int] = None,
    codex_home: Optional[str] = None,
    claude_home: Optional[str] = None,
    state: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> Paths:
    env = dict(os.environ) if env is None else dict(env)
    if home is None:
        home = Path(env.get("HOME") or Path.home())
    if uid is None:
        uid = os.getuid() if hasattr(os, "getuid") else 0

    if codex_home:
        codex = Path(codex_home).expanduser()
    elif env.get("CODEX_HOME"):
        codex = Path(env["CODEX_HOME"]).expanduser()
    else:
        codex = home / ".codex"

    if claude_home:
        claude_homes: Tuple[Path, ...] = (Path(claude_home).expanduser(),)
    elif env.get("CLAUDE_CONFIG_DIR"):
        claude_homes = _split_paths(env["CLAUDE_CONFIG_DIR"])
    else:
        claude_homes = (home / ".claude", home / ".config" / "claude")

    if state:
        state_path = Path(state).expanduser()
    else:
        runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        state_path = Path(runtime) / "ai-usage" / "state.json"

    if cache_dir:
        cache = Path(cache_dir).expanduser()
    else:
        xdg_cache = env.get("XDG_CACHE_HOME") or str(home / ".cache")
        cache = Path(xdg_cache) / "ai-usage"

    return Paths(codex_home=codex, claude_homes=claude_homes, state=state_path, cache_dir=cache)
