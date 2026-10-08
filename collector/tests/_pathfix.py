"""Makes ``ai_usage`` importable however unittest discovery is invoked (repo root or collector/)."""

import sys
from pathlib import Path

_PKG_ROOT = str(Path(__file__).resolve().parents[1])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
