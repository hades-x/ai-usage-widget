"""Atomic JSON writes (tmp + fsync + os.replace), modes 0600 files / 0700 dirs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, obj: Any, *, private_dir: bool = True) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    if private_dir:
        os.chmod(directory, 0o700)
    tmp = directory / (path.name + ".tmp")
    data = (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def read_json(path: Path) -> Any:
    """Return parsed JSON or None when the file is missing or unreadable/corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
