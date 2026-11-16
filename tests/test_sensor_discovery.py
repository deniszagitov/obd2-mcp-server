"""Replay real upstream frames, and exercise catalogue/routing with no vehicle."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from obd2_mcp.ble_connection import READ_ONLY_COMMANDS, BLEOBDConnection
from obd2_mcp.diagnostics import (
    NegativeResponse,
    decode_signal,
    decode_standard,
    extended_catalogue,
    response_data,
    standard_definition,
)

REPLAYS = json.loads((Path(__file__).parent / "fixtures/obdb_replay.json").read_text())["cases"]


@pytest.mark.parametrize("case", REPLAYS)
def test_real_obdb_replay(case):
    tx, rx, command = case["command_id"].split(".")
    definition = next(d for d in extended_catalogue()
                      if (d["hdr"], d["rax"], d["request"]) == (tx, rx, command))
    payload = response_data(case["response"], rx, command)
    for signal in definition["signals"]:
        if signal["id"] not in case["expected_values"]:
            continue
        result = decode_signal(signal, payload)
        if result["status"] == "definition_quarantined":
            assert result["value"] is None
        elif result["status"] == "out_of_range":
            # Upstream clamps; diagnostics deliberately preserves the bad value.
            assert result["raw_value"] is not None
        else:
            assert result["value"] == pytest.approx(case["expected_values"][signal["id"]], abs=2e-5)


def test_frame_errors_never_become_values():
    with pytest.raises(ValueError, match="Incomplete"):
        response_data("7EA100A6187425745D9", "7EA", "2187")
    with pytest.raises(ValueError, match="out-of-order"):
        response_data("7EA100A6187425745D9\n7EA2248D14635000000", "7EA", "2187")
    with pytest.raises(ValueError, match="Incomplete"):
        response_data("7EA100A6187425745D9\n7E82148D14635000000", "7EA", "2187")
    with pytest.raises(ValueError, match="does not match"):
        response_data("7EA04415B8000", "7EA", "2187")
    with pytest.raises(NegativeResponse) as caught:
        response_data("7EA037F2112", "7EA", "2187")
    assert caught.value.code == 0x12
    # Python-OBD silently pads short inputs; our adapter must reject them first.
    with pytest.raises(ValueError, match="Truncated PID"):
        decode_standard(standard_definition(0x0C, "7E0", "7E8"), bytes.fromhex("10"))


def test_signed_bits_and_null_thresholds():
    signed = {"fmt": {"len": 16, "sign": True, "div": 100}}
    assert decode_signal(signed, bytes.fromhex("FF9C"))["value"] == -1
    unavailable = {"fmt": {"len": 16, "div": 10, "nullmax": 6553}}
    assert decode_signal(unavailable, bytes.fromhex("FFFF"))["status"] == "not_available"
    flag = {"fmt": {"bix": 3, "len": 1}}
    assert decode_signal(flag, bytes.fromhex("10"))["value"] == 1


def fake_connection(replies):
    conn = BLEOBDConnection()
    conn._client = SimpleNamespace(is_connected=True)
    calls = []

    async def send(command, **kwargs):
        calls.append((conn._selected_ecu, command))
        await asyncio.sleep(0)
        if command.startswith("AT"):
            return "OK"
        return replies.get((conn._selected_ecu, command), "NO DATA")

    conn._send_command = send
    return conn, calls


@pytest.mark.asyncio
async def test_discovery_follows_continuation_and_keeps_two_ecus_separate():
    conn, calls = fake_connection({
        (("7E0", "7E8"), "0100"): "7E806410000100001",  # RPM + next page
        (("7E0", "7E8"), "0120"): "7E806412000000010",  # catalyst temperature
        (("7E2", "7EA"), "0100"): "7EA06410000000001",
        (("7E2", "7EA"), "0120"): "7EA06412000000001",
        (("7E2", "7EA"), "0140"): "7EA06414000000020",  # 015B
        (("7E0", "7E8"), "010C"): "7E804410C1AF8",
        (("7E2", "7EA"), "015B"): "7EA03415B80",
    })
    catalogue = await conn.list_sensors(scope="standard")
    ids = {s["pid"] for s in catalogue["sensors"]}
    assert {"RPM", "CATALYST_TEMP_B1S1", "7EA:HYBRID_BATTERY_REMAINING"} <= ids
    snapshot = await conn.read_snapshot(limit=2)
    assert snapshot["coverage"]["next_offset"] == 2
    assert snapshot["readings"][0]["value"] == 1726
    assert snapshot["unavailable"][0]["status"] == "no_data"
    assert conn._selected_ecu == ("7E0", "7E8")
    second = await conn.read_snapshot(offset=2)
    assert second["readings"][0]["response_ecu"] == "7EA"
    assert second["readings"][0]["value"] == pytest.approx(50.196078)
    assert second["coverage"]["vehicle_coverage_complete"] is False
    assert sum(cmd == "0100" for _, cmd in calls) == 2  # inventory cache


@pytest.mark.asyncio
async def test_shared_extended_command_is_read_once_and_invalid_fields_reported():
    conn, calls = fake_connection({
        (("7E2", "7EA"), "2187"):
            "7EA100A6187425745D9\n7EA2148D14635000000",
    })
    snapshot = await conn.read_snapshot(pids=["CT200H_T_BATT_TB1", "CT200H_T_BATT_TB2"])
    assert len(snapshot["readings"]) == 2
    assert sum(cmd == "2187" for _, cmd in calls) == 1
    assert all(r["status"] == "decoded_unverified" for r in snapshot["readings"])
    assert snapshot["requests"][0]["raw_response"].startswith("7EA")
    assert conn._selected_ecu == ("7E0", "7E8")


@pytest.mark.asyncio
async def test_concurrent_tools_do_not_change_header_between_query_and_response():
    conn, calls = fake_connection({
        (("7E2", "7EA"), "2187"): "7EA100A6187425745D9\n7EA2148D14635000000",
        (("7E0", "7E8"), "03"): "7E8024300",
    })
    snapshot, codes = await asyncio.gather(
        conn.read_snapshot(pids=["CT200H_T_BATT_TB1"]), conn.get_dtcs(),
    )
    assert len(snapshot["readings"]) == 1
    assert codes == []
    assert (("7E0", "7E8"), "03") in calls


@pytest.mark.asyncio
async def test_bad_ids_are_rejected_before_any_write_and_actions_remain_blocked():
    conn, calls = fake_connection({})
    for value in ("04", "RPM\r04", "CT200H_FAKE", "ATSH7E2"):
        with pytest.raises(ValueError):
            await conn.read_snapshot(pids=["RPM", value])
    assert calls == []
    assert not ({"04", "1101", "2701", "2E1234", "2F1234", "3101", "ATPP0EON"} & READ_ONLY_COMMANDS)


@pytest.mark.asyncio
async def test_extended_catalogue_does_not_connect_or_claim_support():
    conn, calls = fake_connection({})
    result = await conn.list_sensors(scope="extended", limit=100)
    assert calls == []
    assert result["catalogue_count"] == 650
    assert result["next_offset"] == 100
    assert all(s["support"] == "not_probed" for s in result["sensors"])


@pytest.mark.asyncio
async def test_catalogue_ecu_filter_never_routes_to_other_modules():
    conn, _calls = fake_connection({})
    result = await conn.read_snapshot(scope="extended", response_ecus=["7EA"], limit=2)
    assert len(result["requests"]) == 2
    assert all(r["response_ecu"] == "7EA" for r in result["requests"])
    assert result["coverage"]["response_ecu_filter"] == ["7EA"]
    with pytest.raises(ValueError):
        await conn.read_snapshot(scope="extended", response_ecus=["7FF"])


@pytest.mark.asyncio
async def test_disconnected_write_fails_without_recursive_reconnect_deadlock():
    conn = BLEOBDConnection()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(conn._send_command("010C"), timeout=0.1)


@pytest.mark.asyncio
async def test_timeout_closes_session_so_late_fragments_cannot_be_next_sample():
    class SilentAdapter:
        is_connected = True

        async def write_gatt_char(self, *args, **kwargs):
            pass

        async def disconnect(self):
            self.is_connected = False

    conn = BLEOBDConnection()
    adapter = SilentAdapter()
    conn._client = adapter
    conn._connected = True
    with pytest.raises(TimeoutError):
        await conn._send_command("010C", timeout=0.001)
    assert conn._client is None
    assert not adapter.is_connected
    assert conn._selected_ecu is None
