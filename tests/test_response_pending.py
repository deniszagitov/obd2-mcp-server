import asyncio
from types import SimpleNamespace

import pytest

from obd2_mcp.ble_connection import BLEOBDConnection
from obd2_mcp.diagnostics import ResponsePending, response_data


def test_pending_is_not_a_final_negative_response():
    raw = "7E8037F0178\r>7E804410C1AF8\r>"
    assert response_data(raw, "7E8", "010C") == bytes.fromhex("1AF8")
    with pytest.raises(ResponsePending):
        response_data("7E8037F0178", "7E8", "010C")


@pytest.mark.asyncio
async def test_final_response_after_pending_prompt_does_not_resend_command():
    conn = BLEOBDConnection()
    conn.capture_trace = True
    writes = []

    async def write(_uuid, payload, **kwargs):
        writes.append(payload)
        conn._on_notify(None, bytearray(b"7E8037F0178\r>"))
        asyncio.get_running_loop().call_later(0.001, conn._on_notify, None,
                                             bytearray(b"7E804410C1AF8\r>"))

    conn._client = SimpleNamespace(is_connected=True, write_gatt_char=write)
    result = await conn._send_command("010C")
    assert writes == [b"010C\r"]
    assert response_data(result, "7E8", "010C") == bytes.fromhex("1AF8")
    assert conn.last_transaction["response_pending_seen"] is True


@pytest.mark.asyncio
async def test_pending_without_final_response_closes_the_session():
    conn = BLEOBDConnection()
    conn.PENDING_RESPONSE_TIMEOUT = 0.001

    async def write(*args, **kwargs):
        conn._on_notify(None, bytearray(b"7E8037F0178\r>"))

    async def disconnect():
        pass

    conn._client = SimpleNamespace(is_connected=True, write_gatt_char=write, disconnect=disconnect)
    with pytest.raises(TimeoutError):
        await conn._send_command("010C")
    assert conn._client is None
