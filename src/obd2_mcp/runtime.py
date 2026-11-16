"""Identify the code captured at process startup, without accessing the car."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from obd2_mcp import __version__

PACKAGE_ROOT = Path(__file__).resolve().parent
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def source_fingerprint(root: Path = PACKAGE_ROOT) -> str:
    """Hash executable source and pinned profile data in a portable order."""
    digest = hashlib.sha256()
    paths = sorted([*root.glob("*.py"), *root.glob("profiles/*.json"), *root.glob("profiles/*.txt")])
    for path in paths:
        name = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


LOADED_SOURCE_SHA256 = source_fingerprint()


def runtime_status() -> dict:
    return {"version": __version__, "pid": os.getpid(), "started_at": STARTED_AT,
            "read_only": True, "write_tools_exposed": False,
            "source_path": str(PACKAGE_ROOT), "loaded_source_sha256": LOADED_SOURCE_SHA256,
            "source_matches_disk": LOADED_SOURCE_SHA256 == source_fingerprint()}
