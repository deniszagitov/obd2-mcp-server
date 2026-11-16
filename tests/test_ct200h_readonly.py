import pytest

from obd2_mcp.ble_connection import READ_ONLY_COMMANDS, BLEOBDConnection


@pytest.mark.asyncio
async def test_mutation_and_injection_blocked_before_bluetooth():
    conn = BLEOBDConnection()
    # Known catalogue ECU headers are now permitted; arbitrary ones stay blocked.
    for command in ("04", "ATPP 0E SV 00", "ATPP 0E ON", "ATSH7FF", "010C\r04", "2701", "2E1234"):
        with pytest.raises(ValueError):
            await conn._send_command(command)
    with pytest.raises(PermissionError):
        await conn.clear_dtcs()
    with pytest.raises(ValueError):
        await conn.get_live_data(["RPM", "CLEAR_DTC"])


@pytest.mark.asyncio
async def test_can_dtc_count_is_not_decoded_as_part_of_code():
    conn = BLEOBDConnection()
    async def response(*args, **kwargs):
        return "7E8044301042000"
    conn._send_command = response
    result = await conn.get_dtcs()
    assert [item.code for item in result] == ["P0420"]


@pytest.mark.asyncio
async def test_no_data_is_unknown_not_healthy():
    conn = BLEOBDConnection()
    async def response(*args, **kwargs):
        return "NO DATA"
    conn._send_command = response
    with pytest.raises(RuntimeError, match="unknown"):
        await conn.get_dtcs()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "7E8100A430104200000",  # The code fits, but the declared ISO-TP message does not.
    "7E8044301000000",  # A nonzero count containing an empty DTC is invalid.
    "7E802430000000000\nBUFFER FULL",  # An overrun cannot prove an empty list.
])
async def test_legacy_dtc_tool_rejects_incomplete_or_corrupt_messages(raw):
    conn = BLEOBDConnection()

    async def response(*args, **kwargs):
        return raw

    conn._send_command = response
    with pytest.raises(ValueError):
        await conn.get_dtcs()


def test_no_programmable_parameter_writes_or_clear_in_allowlist():
    assert "04" not in READ_ONLY_COMMANDS
    assert not any(cmd.startswith("ATPP") for cmd in READ_ONLY_COMMANDS)
    assert "ATCRA7E8" in READ_ONLY_COMMANDS


def test_clone_ignoring_receive_filter_cannot_mix_two_ecus():
    raw = ['7EA064100983A801300', '7E8064100BE3FA81300']
    assert BLEOBDConnection._extract_mode_payload(raw, '4100') == 'BE3FA81300'
    assert BLEOBDConnection._extract_mode_payload(raw[:1], '4100') == ''
