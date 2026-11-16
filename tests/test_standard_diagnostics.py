from types import SimpleNamespace

import pytest

from obd2_mcp.ble_connection import READ_ONLY_COMMANDS, BLEOBDConnection
from obd2_mcp.standard_diagnostics import decode_dtcs, decode_info, decode_monitor


def frames(payload, rx="7E8"):
    if len(payload) <= 7:
        return rx + bytes((len(payload),)).hex().upper() + payload.hex().upper()
    result = [rx + bytes((0x10 | (len(payload) >> 8), len(payload) & 255)).hex().upper()
              + payload[:6].hex().upper()]
    for index, start in enumerate(range(6, len(payload), 7), 1):
        result.append(rx + bytes((0x20 | (index & 15),)).hex().upper()
                      + payload[start:start + 7].hex().upper())
    return "\n".join(result)


def test_dtc_status_and_unknown_are_distinct():
    assert decode_dtcs(bytes.fromhex("010420"), "07")["dtcs"] == [
        {"code": "P0420", "status": "pending"}]
    assert decode_dtcs(b"\x00", "0A")["dtcs"] == []
    for data in (b"", b"\x01", b"\x01\x00\x00"):
        with pytest.raises(ValueError):
            decode_dtcs(data, "03")


def test_identity_records_use_lengths_and_do_not_impose_na_check_digit():
    vin = "JTHKD5BH0D0000001"
    assert decode_info(b"\x01" + vin.encode(), 2)["vin"] == vin
    cals = b"\x02" + b"CAL_ONE".ljust(16, b"\x00") + b"CAL_TWO".ljust(16, b"\x00")
    assert decode_info(cals, 4)["records"] == ["CAL_ONE", "CAL_TWO"]
    assert decode_info(bytes.fromhex("02AABBCCDD12345678"), 6)["cvns"] == ["AABBCCDD", "12345678"]
    assert decode_info(b"\x01" + b"\xFF" * 17, 2)["availability"] == "not_available"
    for data, pid in ((b"\x01TOOSHORT", 2), (b"\x01SHORT", 4), (b"\x01\xFF", 6)):
        with pytest.raises(ValueError):
            decode_info(data, pid)


def test_mode06_preserves_raw_and_never_invents_scaled_limits_or_pass():
    # Independently encoded SAE wire vectors, not vehicle measurements.
    result = decode_monitor(bytes.fromhex("010A024E0064044C"), 1)
    item = result["tests"][0]
    assert (item["value_raw"], item["minimum_raw"], item["maximum_raw"]) == (590, 100, 1100)
    assert item["passed"] is None and item["value"] is None
    signed = decode_monitor(bytes.fromhex("808CFFFFFF9C0064"), 1)["tests"][0]
    assert signed["unit_scaling_id"] == "8C"
    assert signed["passed"] is None  # Unsigned comparison would be misleading.
    with pytest.raises(ValueError):
        decode_monitor(bytes.fromhex("010A024E006404"), 1)
    with pytest.raises(ValueError):
        decode_monitor(bytes.fromhex("010A024E0064044C02010A024E0064044C"), 1)


def test_observed_scaling_uses_signed_values_and_limits_consistently():
    signed = decode_monitor(bytes.fromhex("018DFFFFFF9C0064"), 1)["tests"][0]
    assert signed["value"] == -0.00390625
    assert signed["minimum"] == -0.390625 and signed["maximum"] == 0.390625
    assert signed["within_reported_limits"] is True
    voltage = decode_monitor(bytes.fromhex("010B03E8000007D0"), 1)["tests"][0]
    assert voltage["value"] == 1 and voltage["unit"] == "V"
    misfires = decode_monitor(bytes.fromhex("0C2400000000FFFF"), 0xA2)["tests"][0]
    assert misfires["value"] == 0 and misfires["unit"] == "count"
    assert misfires["minimum"] is None and misfires["within_reported_limits"] is None
    assert misfires["passed"] is None


@pytest.mark.asyncio
async def test_all_standard_sections_with_pagination_and_missing_service():
    conn = BLEOBDConnection()
    conn._client = SimpleNamespace(is_connected=True)
    replies = {
        "03": frames(bytes.fromhex("4300")),
        "07": frames(bytes.fromhex("47010420")),
        "0A": "NO DATA",
        "0900": frames(bytes.fromhex("490054400000")),
        "0902": frames(b"\x49\x02\x01JTHKD5BH0D0000001"),
        "0904": frames(b"\x49\x04\x01" + b"CAL_ONE".ljust(16, b"\x00")),
        "0906": frames(bytes.fromhex("490601AABBCCDD")),
        "090A": frames(b"\x49\x0A\x01" + b"FAKE_ECU".ljust(20, b"\x00")),
        "020000": frames(bytes.fromhex("42000048000000")),
        "020200": frames(bytes.fromhex("4202000420")),
        "020500": frames(bytes.fromhex("4205007B")),
        "0600": frames(bytes.fromhex("460080000000")),
        "0601": frames(bytes.fromhex("4601010A024E0064044C")),
    }
    sent = []

    async def send(command, **kwargs):
        sent.append(command)
        return "OK" if command.startswith("AT") else replies.get(command, "NO DATA")

    conn._send_command = send
    first = await conn.get_diagnostics("all", limit=3)
    assert len(first["records"]) == 3
    assert first["coverage"]["next_offset"] == 3
    assert first["records"][2]["status"] == "no_data"
    assert "data" not in first["records"][2]  # Unknown permanent faults, not empty healthy.
    rest = await conn.get_diagnostics("all", offset=3, limit=64)
    identity = next(r for r in rest["records"] if r["command"] == "0902")
    assert identity["data"]["vin"] == "JTHKD5BH0D0000001"
    trigger = next(r for r in rest["records"] if r["command"] == "020200")
    assert trigger["data"]["triggering_dtc"] == "P0420"
    temperature = next(r for r in rest["records"] if r["command"] == "020500")
    assert temperature["data"]["value"] == 83
    assert next(r for r in rest["records"] if r["command"] == "0601")["data"]["tests"]
    assert rest["coverage"]["next_offset"] is None
    assert "04" not in sent


def test_service_expansion_never_enables_actions_or_security_access():
    assert {"07", "0A", "020500", "0601", "0904", "0906"} <= READ_ONLY_COMMANDS
    assert not ({"04", "08", "1003", "1101", "2701", "2E1234", "2F1234", "3101"}
                & READ_ONLY_COMMANDS)
