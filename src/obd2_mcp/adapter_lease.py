"""An OS-backed lease for cooperating MCP processes using one BLE adapter."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class AdapterBusyError(ConnectionError):
    pass


class AdapterLease:
    def __init__(self, adapter_id: str, directory: Path | None = None):
        digest = hashlib.sha256(adapter_id.lower().encode()).hexdigest()[:24]
        identity = str(os.getuid()) if hasattr(os, "getuid") else "user"
        root = directory or Path(tempfile.gettempdir()) / f"obd2-mcp-{identity}"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / f"adapter-{digest}.lock"
        self._file = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\x00")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise AdapterBusyError("This ELM adapter is owned by another OBD MCP process. "
                                   "Release that session before retrying.") from error
        self._file = handle
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode())
        handle.flush()

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        # Never unlink: removing a live lock inode would permit two owners.
