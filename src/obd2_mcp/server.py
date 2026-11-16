"""
OBD-II MCP Server — bridges a car's OBD-II diagnostic port to Claude.

Usage:
    # With a real OBD adapter:
    obd2-mcp

    # Demo mode (mock adapter, no car needed):
    obd2-mcp --mock

    # Specify a serial port:
    obd2-mcp --port /dev/tty.usbserial-1234
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations

from obd2_mcp import __version__
from obd2_mcp.dtc_database import (
    find_codes_for_brand,
    get_dtc_category,
    get_procedures,
    list_brands,
    lookup_dtc_full,
)
from obd2_mcp.mock_obd import MockOBDConnection
from obd2_mcp.obd_connection import OBDConnectionProtocol, RealOBDConnection
from obd2_mcp.runtime import runtime_status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

server = Server("obd2-mcp-server", version=__version__)
connection: OBDConnectionProtocol | None = None


def _json_response(data: Any) -> list[TextContent]:
    """Wrap a dict/list as a JSON TextContent response."""
    if isinstance(data, dict):
        data = {**data, "server": runtime_status()}
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _error_response(message: str) -> list[TextContent]:
    return _json_response({"error": message})


def _ensure_connected() -> OBDConnectionProtocol:
    if connection is None:
        raise RuntimeError("OBD adapter not initialized")
    if not connection.is_connected():
        # Surface the most recent BLE connect error if we have one.
        last = getattr(connection, "last_connect_error", None)
        if last:
            raise RuntimeError(
                f"OBD adapter is not connected. Last connect error:\n{last}"
            )
        raise RuntimeError("OBD adapter is not connected")
    return connection


async def _ensure_ready() -> OBDConnectionProtocol:
    """Async wrapper that triggers a lazy reconnect for BLE if not connected."""
    if connection is None:
        raise RuntimeError("OBD adapter not initialized")
    if not connection.is_connected():
        ensure_ready = getattr(connection, "ensure_ready", None)
        if ensure_ready is not None:
            try:
                await ensure_ready()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"OBD adapter could not be (re)connected: {e}") from e
        else:
            raise RuntimeError("OBD adapter is not connected")
    return connection


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    Tool(
        name="read_dtc",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
        description=(
            "Read Diagnostic Trouble Codes (DTCs) from the vehicle. "
            "This CT200h installation reads stored emissions codes from response ECU 7E8 only. "
            "Returns fault codes with descriptions and status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": (
                        "For this installation omit, or use 'engine' / '7E8'. Other systems are not checked."
                    ),
                },
            },
        },
    ),
    Tool(
        name="get_server_status",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
        description="Inspect running version, process, loaded-source fingerprint and enforced "
                    "read-only policy without connecting to the car. Also included in every JSON response.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_live_data",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
        description=(
            "Read a page of sequential sensor samples. With no PIDs, discover advertised Mode 01 "
            "sensors from engine 7E8 and hybrid 7EA. scope=extended/all also probes the pinned "
            "CT200h candidate catalogue; its formulas remain unverified for this vehicle. "
            "Use list_sensors for exact IDs. Follow coverage.next_offset until null; check "
            "unavailable and per-request raw responses. Never assume complete vehicle coverage."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of PIDs to read (e.g. ['RPM', 'COOLANT_TEMP', 'SPEED']). "
                        "Hybrid standard IDs have prefix 7EA:, extended IDs start CT200H_."
                    ),
                },
                "scope": {"type": "string", "enum": ["standard", "extended", "all"],
                          "default": "standard"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 64, "default": 32,
                          "description": "Maximum ECU requests per page; one request may return many fields."},
                "allow_partial_prefix": {
                    "type": "boolean", "default": False,
                    "description": "Experimental CT200h 7EA/2181 only: when 34 of 35 bytes arrive in order, "
                                   "expose the first 14 bounded voltage fields as decoded_partial_unverified. "
                                   "OEM application integrity remains unknown; default strict mode rejects the response.",
                },
                "response_ecus": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1,
                    "description": "Restrict candidate reads to registered responders, e.g. ['7E8'] "
                                   "for engine or ['7EA'] for hybrid. Does not change PID discovery scope.",
                },
            },
        },
    ),
    Tool(
        name="list_sensors",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
        description=(
            "List a page of sensor IDs, ECU addresses, commands and evidence status. "
            "standard/all discovers ECU-advertised Mode 01 PIDs; extended lists the offline "
            "OBDb CT200h catalogue (650 candidate fields, not 650 supported readings). "
            "Check applicable_year, transport_supported, verification and next_offset."
        ),
        inputSchema={
            "type": "object", "properties": {
                "scope": {"type": "string", "enum": ["standard", "extended", "all"], "default": "all"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                "refresh": {"type": "boolean", "default": False},
            },
        },
    ),
    Tool(
        name="get_diagnostics",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True),
        description=(
            "Read standard diagnostic data from the selected emissions ECU (engine 7E8 or hybrid 7EA). "
            "Sections: stored/pending/permanent DTCs (03/07/0A), identity with supported InfoTypes "
            "(09: VIN, calibration IDs, CVNs, ECU name), freeze frame 0 (02), and supported Mode 06 "
            "monitor records. Five observed Mode 06 scaling IDs are normalized; unknown scaling "
            "and semantic pass/fail remain unknown. Misfire accumulators are not threshold tests. "
            "Follow coverage.next_offset; inspect discovery and per-request status/raw data. "
            "Unavailable data does not mean no faults. This does not scan all vehicle modules."
        ),
        inputSchema={"type": "object", "properties": {
            "section": {"type": "string", "enum": ["dtc", "identity", "freeze_frame", "monitors", "all"],
                        "default": "dtc"},
            "ecu": {"type": "string", "enum": ["engine", "hybrid"], "default": "engine"},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 64, "default": 32},
        }},
    ),
    Tool(
        name="get_vehicle_info",
        description=(
            "Read vehicle identification: VIN, calibration IDs, ECU name, and OBD standard."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="list_modules",
        description=(
            "List all ECUs (Electronic Control Units) found on the vehicle's CAN bus. "
            "Shows module name, address, and communication protocol."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="get_freeze_frame",
        description=(
            "Read freeze-frame data — a snapshot of sensor values captured at the moment "
            "a DTC was set. Useful for understanding the conditions that triggered a fault."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dtc_code": {
                    "type": "string",
                    "description": "The DTC code to retrieve freeze frame for (e.g. 'P0420', 'B1310').",
                },
            },
            "required": ["dtc_code"],
        },
    ),
    Tool(
        name="explain_dtc",
        description=(
            "Get a detailed, plain-language explanation of a Diagnostic Trouble Code. "
            "Includes the code description, category, and manufacturer-specific context "
            "when a brand is specified."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dtc_code": {
                    "type": "string",
                    "description": "The DTC code to explain (e.g. 'P0420', 'B1310', 'U0100').",
                },
                "brand": {
                    "type": "string",
                    "description": (
                        "Vehicle brand slug (e.g. 'ford', 'toyota', 'bmw'). "
                        "Provides brand-specific descriptions when available."
                    ),
                },
            },
            "required": ["dtc_code"],
        },
    ),
    Tool(
        name="list_brands",
        description=(
            "List all vehicle brands in the DTC database. "
            "Returns brand slugs, names, and code counts. "
            "Use this to discover which brands are supported."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="search_dtc_database",
        description=(
            "Search the DTC database for a code with full details. "
            "Returns the complete entry including fault location, probable cause, "
            "detail URL, and which brand/source the code was found in."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "dtc_code": {
                    "type": "string",
                    "description": "The DTC code to look up (e.g. 'P0420', 'B1310').",
                },
                "brand": {
                    "type": "string",
                    "description": "Optional brand slug to prioritize (e.g. 'ford', 'toyota').",
                },
            },
            "required": ["dtc_code"],
        },
    ),
    Tool(
        name="get_brand_procedures",
        description=(
            "Get diagnostic procedures for a vehicle brand — how to access codes, "
            "clear codes, reset service intervals, run self-tests, etc."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "brand": {
                    "type": "string",
                    "description": "Brand slug (e.g. 'ford', 'toyota', 'bmw').",
                },
                "procedure_type": {
                    "type": "string",
                    "description": (
                        "Filter by type: 'accessing', 'clearing', 'reset', 'test', 'general'. "
                        "Omit for all procedures."
                    ),
                },
            },
            "required": ["brand"],
        },
    ),
    Tool(
        name="list_brand_codes",
        description=(
            "List all manufacturer-specific DTC codes for a given brand. "
            "Returns code, fault location, probable cause, and detail URL for each."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "brand": {
                    "type": "string",
                    "description": "Brand slug (e.g. 'ford', 'toyota', 'bmw').",
                },
            },
            "required": ["brand"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


async def _call_method(method_name: str, **kwargs):
    """Call a method on the connection, supporting both sync and async implementations."""
    if method_name not in {"get_dtcs", "get_live_data", "get_vehicle_info", "list_modules", "get_freeze_frame"}:
        raise PermissionError("Method rejected by the read-only policy")
    conn = await _ensure_ready()
    method = getattr(conn, method_name)
    result = method(**kwargs)
    # If the connection returns a coroutine (BLE), await it
    if asyncio.iscoroutine(result):
        return await result
    return result


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "get_server_status":
            return _json_response({"backend": type(connection).__name__ if connection else None})
        elif name == "read_dtc":
            return await _handle_read_dtc(arguments)
        elif name == "clear_dtc":
            return await _handle_clear_dtc(arguments)
        elif name == "get_live_data":
            return await _handle_get_live_data(arguments)
        elif name == "list_sensors":
            return await _handle_list_sensors(arguments)
        elif name == "get_diagnostics":
            return await _handle_get_diagnostics(arguments)
        elif name == "get_vehicle_info":
            return await _handle_get_vehicle_info(arguments)
        elif name == "list_modules":
            return await _handle_list_modules(arguments)
        elif name == "get_freeze_frame":
            return await _handle_get_freeze_frame(arguments)
        elif name == "explain_dtc":
            return _handle_explain_dtc(arguments)
        elif name == "list_brands":
            return _handle_list_brands(arguments)
        elif name == "search_dtc_database":
            return _handle_search_dtc_database(arguments)
        elif name == "get_brand_procedures":
            return _handle_get_brand_procedures(arguments)
        elif name == "list_brand_codes":
            return _handle_list_brand_codes(arguments)
        else:
            return _error_response(f"Unknown tool: {name}")
    except Exception as e:
        logger.exception("Tool error: %s", name)
        return _error_response(str(e))


async def _handle_read_dtc(args: dict[str, Any]) -> list[TextContent]:
    module = args.get("module")
    dtcs = await _call_method("get_dtcs", module=module)

    if not dtcs:
        return _json_response({
            "module": module or "emissions ECU response 7E8",
            "dtc_count": 0,
            "dtcs": [],
            "summary": "No stored emissions DTCs reported by selected ECU 7E8. Other ECU systems were not checked.",
        })

    dtc_list = []
    for dtc in dtcs:
        entry = asdict(dtc)
        entry["category"] = get_dtc_category(dtc.code)
        full = lookup_dtc_full(dtc.code)
        if full.get("faultLocation"):
            entry["description"] = full["faultLocation"]
        if full.get("probableCause"):
            entry["probable_cause"] = full["probableCause"]
        dtc_list.append(entry)

    return _json_response({
        "module": module or "emissions ECU response 7E8",
        "dtc_count": len(dtc_list),
        "dtcs": dtc_list,
    })


async def _handle_clear_dtc(args: dict[str, Any]) -> list[TextContent]:
    # Keep a denial for stale clients even though this action is not advertised.
    return _error_response("Fault clearing is disabled by the read-only policy, including confirm=true")


async def _handle_get_live_data(args: dict[str, Any]) -> list[TextContent]:
    conn = await _ensure_ready()
    if hasattr(conn, "read_snapshot"):
        result = await conn.read_snapshot(
            pids=args.get("pids"), scope=args.get("scope", "standard"),
            offset=args.get("offset", 0), limit=args.get("limit", 32),
            allow_partial_prefix=args.get("allow_partial_prefix", False),
            response_ecus=args.get("response_ecus"),
        )
        return _json_response(result)
    pids = args.get("pids")
    readings = await _call_method("get_live_data", pids=pids)

    data = []
    for r in readings:
        data.append({
            "pid": r.pid,
            "name": r.name,
            "value": r.value,
            "unit": r.unit,
        })

    return _json_response({
        "reading_count": len(data),
        "readings": data,
    })


async def _handle_list_sensors(args: dict[str, Any]) -> list[TextContent]:
    # Extended catalogue lookup is offline: do not connect just to list names.
    conn = connection if args.get("scope") == "extended" else await _ensure_ready()
    if conn is None or not hasattr(conn, "list_sensors"):
        return _error_response("Sensor catalogue is implemented for the CT200h BLE backend only")
    return _json_response(await conn.list_sensors(
        scope=args.get("scope", "all"), offset=args.get("offset", 0),
        limit=args.get("limit", 50), refresh=args.get("refresh", False),
    ))


async def _handle_get_diagnostics(args: dict[str, Any]) -> list[TextContent]:
    conn = await _ensure_ready()
    if not hasattr(conn, "get_diagnostics"):
        return _error_response("Standard diagnostic bundle is implemented for the CT200h BLE backend only")
    return _json_response(await conn.get_diagnostics(
        section=args.get("section", "dtc"), ecu=args.get("ecu", "engine"),
        offset=args.get("offset", 0), limit=args.get("limit", 32),
    ))


async def _handle_get_vehicle_info(args: dict[str, Any]) -> list[TextContent]:
    info = await _call_method("get_vehicle_info")
    return _json_response(asdict(info))


async def _handle_list_modules(args: dict[str, Any]) -> list[TextContent]:
    modules = await _call_method("list_modules")
    return _json_response({
        "module_count": len(modules),
        "modules": [asdict(m) for m in modules],
    })


async def _handle_get_freeze_frame(args: dict[str, Any]) -> list[TextContent]:
    dtc_code = args["dtc_code"]
    frame = await _call_method("get_freeze_frame", dtc_code=dtc_code)

    if frame is None:
        return _json_response({
            "dtc_code": dtc_code,
            "freeze_frame": None,
            "note": (
                f"No freeze-frame data available for {dtc_code}. "
                "Body and network codes often don't capture freeze-frame snapshots."
            ),
        })

    return _json_response({
        "dtc_code": frame.dtc_code,
        "freeze_frame": [asdict(r) for r in frame.readings],
    })


def _handle_explain_dtc(args: dict[str, Any]) -> list[TextContent]:
    code = args["dtc_code"].upper().strip()
    brand = args.get("brand")
    full = lookup_dtc_full(code, brand=brand)
    category = get_dtc_category(code)

    explanation: dict[str, Any] = {
        "code": code,
        "description": full.get("faultLocation") or "Unknown code",
        "category": category,
        "source": full.get("source"),
    }

    if full.get("probableCause"):
        explanation["probable_cause"] = full["probableCause"]
    if full.get("detailUrl"):
        explanation["detail_url"] = full["detailUrl"]

    prefix = code[0] if code else ""
    if prefix == "P":
        explanation["general_advice"] = (
            "Powertrain codes affect engine and transmission. "
            "Check related sensors, wiring, and connectors. "
            "A code reader can clear the code, but the underlying issue should be addressed first."
        )
    elif prefix == "B":
        explanation["general_advice"] = (
            "Body codes relate to comfort, convenience, and safety systems "
            "(doors, windows, mirrors, seats, roof). Check wiring harness connectors, "
            "especially at door jambs where flexing causes wear."
        )
    elif prefix == "C":
        explanation["general_advice"] = (
            "Chassis codes relate to ABS, traction control, and stability systems. "
            "Wheel speed sensors and their wiring are common failure points."
        )
    elif prefix == "U":
        explanation["general_advice"] = (
            "Network codes indicate communication failures between modules on the CAN bus. "
            "Check for damaged wiring, corrosion, or a failed module."
        )

    return _json_response(explanation)


def _handle_list_brands(args: dict[str, Any]) -> list[TextContent]:
    brands = list_brands()
    return _json_response({
        "brand_count": len(brands),
        "brands": brands,
    })


def _handle_search_dtc_database(args: dict[str, Any]) -> list[TextContent]:
    code = args["dtc_code"].upper().strip()
    brand = args.get("brand")
    result = lookup_dtc_full(code, brand=brand)
    return _json_response(result)


def _handle_get_brand_procedures(args: dict[str, Any]) -> list[TextContent]:
    brand = args["brand"]
    procedure_type = args.get("procedure_type")
    procs = get_procedures(brand, procedure_type=procedure_type)
    return _json_response({
        "brand": brand,
        "procedure_count": len(procs),
        "procedures": procs,
    })


def _handle_list_brand_codes(args: dict[str, Any]) -> list[TextContent]:
    brand = args["brand"]
    codes = find_codes_for_brand(brand)
    return _json_response({
        "brand": brand,
        "code_count": len(codes),
        "codes": codes,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="obd2-mcp",
        description="OBD-II MCP Server — connect your car's diagnostics to Claude",
    )
    parser.add_argument(
        "--read-only", action="store_true", default=True,
        help="Explicitly enforce read-only operation (also the default; this fork has no write mode)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the mock adapter (demo mode, no car needed)",
    )
    parser.add_argument(
        "--port",
        type=str,
        default=None,
        help="Serial port for the OBD adapter (e.g. /dev/tty.usbserial-1234)",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=None,
        help="Baud rate for serial connection (default: auto-detect)",
    )
    parser.add_argument(
        "--ble",
        type=str,
        nargs="?",
        const="auto",
        default=None,
        metavar="ADDRESS",
        help=(
            "Connect via BLE. Pass a device address/UUID, or just --ble to "
            "auto-discover the OBD adapter (scans for vLinker, OBDLink, etc.)"
        ),
    )
    parser.add_argument(
        "--ble-name",
        type=str,
        default=None,
        help="Name hint for BLE auto-discovery (e.g. 'vLinker')",
    )
    parser.add_argument(
        "--ble-write-uuid",
        type=str,
        default=None,
        help="BLE GATT write characteristic UUID (auto-detected if omitted)",
    )
    parser.add_argument(
        "--ble-notify-uuid",
        type=str,
        default=None,
        help="BLE GATT notify characteristic UUID (auto-detected if omitted)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Run an end-to-end BLE diagnostic (scan → connect → init → "
            "query) and print a report. Does not start the MCP server. "
            "Combine with --ble / --ble-name to scope the scan."
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


async def run_server(args: argparse.Namespace) -> None:
    global connection
    if args.read_only is not True:
        raise ValueError("This fork requires read-only operation")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,  # MCP uses stdout for protocol; logs go to stderr
    )

    if args.probe:
        from obd2_mcp.ble_connection import probe_adapter
        address = None
        if args.ble and args.ble != "auto":
            address = args.ble
        report = await probe_adapter(
            address=address,
            name_hint=args.ble_name,
            write_uuid=args.ble_write_uuid,
            notify_uuid=args.ble_notify_uuid,
        )
        # Print to stdout (probe is interactive, not MCP)
        print(json.dumps(report, indent=2, default=str))
        if report.get("errors") and report.get("connect") != "ok":
            sys.exit(1)
        return

    if args.mock:
        logger.info("Starting in MOCK mode (simulated vehicle)")
        connection = MockOBDConnection()
    elif args.ble is not None:
        address = args.ble if args.ble != "auto" else None
        if address:
            logger.info("Connecting via BLE to %s ...", address)
        else:
            logger.info("Auto-discovering BLE OBD adapter...")
        from obd2_mcp.ble_connection import BLEOBDConnection
        ble_conn = BLEOBDConnection(
            address=address,
            write_uuid=args.ble_write_uuid,
            notify_uuid=args.ble_notify_uuid,
            name_hint=args.ble_name,
        )
        # Connect lazily from the first read, so MCP starts with the car off.
        connection = ble_conn
    else:
        logger.info("Connecting to real OBD adapter...")
        connection = RealOBDConnection(port=args.port, baudrate=args.baudrate or None)

    logger.info("OBD-II MCP Server ready — %d tools available", len(TOOLS))

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    except asyncio.CancelledError:
        pass
    finally:
        if connection is not None:
            connection.close()
            logger.info("Shutdown complete")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        pass  # Clean exit on Ctrl+C


if __name__ == "__main__":
    main()
