"""Verify the installed MCP package without accessing vehicle hardware."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from obd2_mcp import __version__
from obd2_mcp.runtime import source_fingerprint


async def check_backend(arguments):
    params = StdioServerParameters(command=sys.executable,
                                  args=["-m", "obd2_mcp.server", "--read-only",
                                        "--log-level", "ERROR", *arguments])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        initialized = await session.initialize()
        assert initialized.serverInfo.version == __version__
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"get_server_status", "get_diagnostics", "list_sensors"} <= names
        assert "clear_dtc" not in names
        status_result = await session.call_tool("get_server_status", {})
        status = json.loads(status_result.content[0].text)["server"]
        assert status["read_only"] is True and status["source_matches_disk"] is True
        assert status["loaded_source_sha256"] == source_fingerprint()
        if "--mock" in arguments:
            # Never send an action-denial probe to a vehicle backend.
            denied = await session.call_tool("clear_dtc", {"module": "engine", "confirm": True})
            assert "read-only" in json.loads(denied.content[0].text)["error"]
            faults = await session.call_tool("read_dtc", {})
            assert json.loads(faults.content[0].text)["dtc_count"] == 2
        else:
            catalogue = await session.call_tool("list_sensors", {"scope": "extended", "limit": 3})
            data = json.loads(catalogue.content[0].text)
            assert data["catalogue_count"] == 650 and data["next_offset"] == 3
        return status


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ble_status = await check_backend(["--ble", "auto"])
    await check_backend(["--mock"])
    report = {"version": __version__, "source_sha256": ble_status["loaded_source_sha256"],
              "read_only": True, "stdio_handshake_and_tools": "passed",
              "offline_catalogue": "passed", "mock_action_denial": "passed",
              "hardware_accessed": False}
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    asyncio.run(main())
