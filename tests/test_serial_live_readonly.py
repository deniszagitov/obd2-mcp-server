"""Live-data selection must never invoke a diagnostic action command."""

from types import SimpleNamespace

import obd
import pytest

from obd2_mcp.obd_connection import RealOBDConnection


def fake_serial_connection():
    # Skip the hardware-opening constructor, while exercising the actual selector.
    conn = RealOBDConnection.__new__(RealOBDConnection)
    conn._obd = obd
    calls = []

    def query(command):
        calls.append(command.name)
        return SimpleNamespace(is_null=lambda: False, value=750 * obd.Unit.rpm)

    conn._conn = SimpleNamespace(query=query)
    return conn, calls


@pytest.mark.parametrize("name", ["CLEAR_DTC", "GET_DTC", "VIN", "ELM_VOLTAGE", "NOT_A_PID"])
def test_live_selection_rejects_non_mode01_before_query(name):
    conn, calls = fake_serial_connection()
    with pytest.raises(ValueError, match="Mode 01"):
        conn.get_live_data([name])
    assert calls == []


def test_mixed_request_is_fully_validated_before_first_query():
    conn, calls = fake_serial_connection()
    with pytest.raises(ValueError, match="Mode 01"):
        conn.get_live_data(["RPM", "CLEAR_DTC"])
    assert calls == []


def test_valid_mode01_name_remains_supported_case_insensitively():
    conn, calls = fake_serial_connection()
    readings = conn.get_live_data(["rpm"])
    assert calls == ["RPM"]
    assert readings[0].value == 750
