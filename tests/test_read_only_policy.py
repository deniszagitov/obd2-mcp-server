"""Read-only protection must hold even if a client ignores tool discovery."""

import json
from types import SimpleNamespace

import obd
import pytest

from obd2_mcp import server
from obd2_mcp.obd_connection import RealOBDConnection


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_clear_is_blocked_before_connecting_or_calling_backend(monkeypatch, confirm):
    def forbidden():
        pytest.fail("A blocked action must not access the connection")

    monkeypatch.setattr(server, "connection", SimpleNamespace(is_connected=forbidden))
    result = await server.call_tool("clear_dtc", {"module": "engine", "confirm": confirm})
    data = json.loads(result[0].text)
    assert "read-only" in data["error"].lower()
    assert data["server"]["read_only"] is True


@pytest.mark.asyncio
async def test_action_tool_is_not_advertised():
    names = {tool.name for tool in await server.list_tools()}
    assert "clear_dtc" not in names
    assert "get_server_status" in names


def test_serial_clear_is_blocked_without_any_transport_query():
    conn = RealOBDConnection.__new__(RealOBDConnection)
    with pytest.raises(PermissionError, match="read-only"):
        conn.clear_dtcs()


@pytest.mark.parametrize("command", [obd.commands.CLEAR_DTC, obd.commands.ELM_VOLTAGE])
def test_serial_query_boundary_rejects_actions_and_adapter_passthrough(command):
    conn = RealOBDConnection.__new__(RealOBDConnection)
    conn._obd = obd
    with pytest.raises(PermissionError, match="read-only"):
        conn._query_read_only(command)


def test_explicit_read_only_flag_and_safe_default():
    assert server.parse_args([]).read_only is True
    assert server.parse_args(["--read-only"]).read_only is True
    with pytest.raises(SystemExit):
        server.parse_args(["--no-read-only"])


@pytest.mark.asyncio
async def test_runtime_status_is_offline_and_identifies_loaded_source(monkeypatch):
    monkeypatch.setattr(server, "connection", None)
    result = await server.call_tool("get_server_status", {})
    data = json.loads(result[0].text)["server"]
    assert data["read_only"] is True
    assert data["source_matches_disk"] is True
    assert len(data["loaded_source_sha256"]) == 64
    assert data["pid"] > 0


def test_runtime_does_not_relabel_a_stale_process_as_new(monkeypatch):
    from obd2_mcp import runtime

    loaded = runtime.LOADED_SOURCE_SHA256
    monkeypatch.setattr(runtime, "source_fingerprint", lambda: "0" * 64)
    status = runtime.runtime_status()
    assert status["loaded_source_sha256"] == loaded
    assert status["source_matches_disk"] is False


@pytest.mark.asyncio
async def test_programmatic_start_cannot_disable_read_only():
    with pytest.raises(ValueError, match="read-only"):
        await server.run_server(SimpleNamespace(read_only=False))
