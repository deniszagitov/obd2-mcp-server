"""Incomplete transactions may expose only explicitly requested, bounded fields."""

from types import SimpleNamespace

import pytest

from obd2_mcp.ble_connection import BLEOBDConnection

# Synthetic 35-byte application message, with the last (one-byte) CF missing.
# The first 14 two-byte fields are present; the missing tail must stay unknown.
PREFIX = (
    "7EA1023618132003200\n7EA2132003200320032\n"
    "7EA2200320032003200\n7EA2332003200320032\n7EA2400320011223344"
)


def connection(raw):
    conn = BLEOBDConnection()
    conn._client = SimpleNamespace(is_connected=True)

    async def send(command, **kwargs):
        return "OK" if command.startswith("AT") else raw

    conn._send_command = send
    return conn


@pytest.mark.asyncio
async def test_partial_recovery_is_off_by_default():
    result = await connection(PREFIX).read_snapshot(pids=["CT200H_BAT_BLOCK_V_V01"])
    assert result["readings"] == []
    assert result["requests"][0]["status"] == "incomplete_response"


@pytest.mark.asyncio
async def test_opt_in_returns_only_fields_fully_present_in_ordered_prefix():
    result = await connection(PREFIX).read_snapshot(
        pids=["CT200H_BAT_BLOCK_V_V01", "CT200H_BAT_BLOCK_V_V14"], allow_partial_prefix=True,
    )
    assert len(result["readings"]) == 2
    for value in result["readings"]:
        assert value["status"] == "decoded_partial_unverified"
        assert value["transport_complete"] is False
        assert value["field_complete"] is True
        assert value["value"] == pytest.approx(0x3200 * 0.0012205692)
    request = result["requests"][0]
    assert request["declared_message_bytes"] == 35
    assert request["received_message_bytes"] == 34
    assert request["status"] == "partial_prefix"
    assert result["partial_reading_count"] == 2
    assert result["complete_reading_count"] == 0
    assert all(r["application_integrity"] == "unknown" for r in result["readings"])


@pytest.mark.asyncio
async def test_tail_fields_and_other_layouts_are_not_recovered():
    result = await connection(PREFIX).read_snapshot(
        pids=["CT200H_BAT_BLOCK_V_V01", "CT200H_AUXILIARY_BAT_V"], allow_partial_prefix=True,
    )
    assert len(result["readings"]) == 1
    assert result["unavailable"][0]["status"] == "prefix_field_not_allowed"
    other = await connection(PREFIX.replace("7EA1023", "7EA1024")).read_snapshot(
        pids=["CT200H_BAT_BLOCK_V_V01"], allow_partial_prefix=True,
    )
    assert other["readings"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [PREFIX.replace("7EA24", "7EA25"),
                                 PREFIX.replace("7EA23", "7EA22"),
                                 PREFIX + "\nBUFFER FULL"])
async def test_discontinuity_or_overrun_never_qualifies_for_prefix_recovery(raw):
    result = await connection(raw).read_snapshot(
        pids=["CT200H_BAT_BLOCK_V_V01"], allow_partial_prefix=True,
    )
    assert not result["readings"]
    assert result["unavailable"]
