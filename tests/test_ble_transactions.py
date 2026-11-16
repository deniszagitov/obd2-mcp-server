"""Exercise notify lifetime, prompts and guarded ELM command formatting."""

from types import SimpleNamespace

import pytest

from obd2_mcp.ble_connection import BLEOBDConnection


def test_notify_owns_a_copy_before_marshalling_to_the_loop():
    scheduled = []
    conn = BLEOBDConnection()
    conn._loop = SimpleNamespace(is_running=lambda: True,
                                 call_soon_threadsafe=lambda fn: scheduled.append(fn))
    data = bytearray(b"7EA03415B80\r>")
    conn._on_notify(None, data)
    data[:] = b"REUSED BUFFER"
    scheduled.pop()()
    assert bytes(conn._response_buffer) == b"7EA03415B80\r>"
    assert conn._response_event.is_set()


@pytest.mark.asyncio
async def test_trace_preserves_notify_chunks_prompt_and_write_outcome():
    conn = BLEOBDConnection()
    conn.capture_trace = True

    async def write(_uuid, payload, **kwargs):
        assert payload == b"0100\r"
        conn._on_notify(None, bytearray(b"7E8064100BE3FA"))
        conn._on_notify(None, bytearray(b"813\r>"))

    conn._client = SimpleNamespace(is_connected=True, write_gatt_char=write)
    assert await conn._send_command("0100") == "7E8064100BE3FA813"
    trace = conn.last_transaction
    assert trace["ble_write_done"] is True
    assert trace["prompt_seen"] is True
    assert trace["raw_response"] == "7E8064100BE3FA813\r>"
    assert [c["ascii"] for c in trace["chunks"]] == ["7E8064100BE3FA", "813\r>"]
    assert trace["chunks"][0]["received_at"] <= trace["chunks"][1]["received_at"]


@pytest.mark.asyncio
async def test_spaces_do_not_bypass_allowlist_and_are_preserved_on_wire():
    conn = BLEOBDConnection()
    written = []

    async def write(_uuid, payload, **kwargs):
        written.append(payload)
        conn._on_notify(None, bytearray(b"OK\r>"))

    conn._client = SimpleNamespace(is_connected=True, write_gatt_char=write)
    assert await conn._send_command("AT SH 7E2") == "OK"
    assert written == [b"AT SH 7E2\r"]
    for command in ("0 4", "AT SH 7FF", "AT SH 7E2\r04", "AT SH 7E2\n04", "2E 1234"):
        with pytest.raises(ValueError):
            await conn._send_command(command)
    assert len(written) == 1
