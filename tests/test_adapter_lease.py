import subprocess
import sys

import pytest

from obd2_mcp.adapter_lease import AdapterBusyError, AdapterLease


def test_same_adapter_has_one_owner_and_release_preserves_lock_inode(tmp_path):
    first = AdapterLease("TEST-ADAPTER", tmp_path)
    second = AdapterLease("test-adapter", tmp_path)
    first.acquire()
    inode = first.path.stat().st_ino
    with pytest.raises(AdapterBusyError):
        second.acquire()
    first.release()
    second.acquire()
    assert second.path.stat().st_ino == inode
    second.release()
    second.release()


def test_lease_is_enforced_across_processes(tmp_path):
    lease = AdapterLease("TEST-ADAPTER", tmp_path)
    code = (
        "from pathlib import Path; from obd2_mcp.adapter_lease import AdapterLease,AdapterBusyError;"
        f"lease=AdapterLease('TEST-ADAPTER',Path({str(tmp_path)!r}));"
        "\ntry: lease.acquire()\nexcept AdapterBusyError: raise SystemExit(3)\n"
    )
    lease.acquire()
    try:
        result = subprocess.run([sys.executable, "-c", code], check=False)
        assert result.returncode == 3
    finally:
        lease.release()
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_ble_session_releases_ownership_even_when_link_already_dropped(tmp_path, monkeypatch):
    import obd2_mcp.ble_connection as ble
    monkeypatch.setattr(ble, "AdapterLease", lambda address: AdapterLease(address, tmp_path))
    conn = ble.BLEOBDConnection()
    conn._claim_adapter("test-adapter")
    contender = AdapterLease("test-adapter", tmp_path)
    with pytest.raises(AdapterBusyError):
        contender.acquire()
    conn.close()
    contender.acquire()
    contender.release()
