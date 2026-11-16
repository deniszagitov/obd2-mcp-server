# obd2-mcp-server

An MCP server that bridges your car's OBD-II diagnostic port to Claude via the [Model Context Protocol](https://modelcontextprotocol.io).

This Lexus/ELM327 fork enforces **read-only operation** for MCP clients including
Codex. Fault clearing and ECU actions are disabled, even with confirmation.
See the [CT200h extension](#local-lexus-ct200h-ble-extension) and
[saved progress](docs/progress.md) for supported reads and measured limits.

```
┌──────────┐    Bluetooth/USB    ┌───────────────┐    python-obd    ┌──────────────┐    MCP (stdio)    ┌─────────┐
│   Car    │ ◄─────────────────► │  ELM327       │ ◄──────────────► │  MCP Server  │ ◄───────────────► │  Claude │
│  OBD-II  │                     │  Adapter      │                  │  (Python)    │                   │   AI    │
└──────────┘                     └───────────────┘                  └──────────────┘                   └─────────┘
```

## What it does

Claude can read your car's fault codes, live sensor data, and vehicle info — then explain what it all means in plain language. Built for the ["I Let AI Diagnose My Car"](https://bitvea.com) PR stunt using a 2010 Ford Focus CC convertible with a broken passenger window and non-functional roof.

## Status: Alpha (for inspiration, not production)

This is an early alpha built in a weekend. I'm a JavaScript/TypeScript developer who vibe-coded this in Python with Claude's help. It works, it's tested, but it's not production-grade. Use it as inspiration, fork it, improve it — PRs welcome.

## Tools

| Tool | Description |
|------|-------------|
| `read_dtc` | Read stored emissions DTCs from the selected supported ECU |
| `get_server_status` | Offline version, process, source fingerprint and enforced read-only policy |
| `get_live_data` | Paged sequential standard and CT200h candidate sensor readings |
| `list_sensors` | Advertised standard PID inventory or offline OEM candidate catalogue |
| `get_diagnostics` | Standard stored/pending/permanent DTC, identity, freeze frame 0 and Mode 06 reads |
| `get_vehicle_info` | VIN, calibration IDs, ECU name |
| `list_modules` | Legacy backend-specific module information; not exhaustive ECU discovery |
| `get_freeze_frame` | Sensor snapshot from when a DTC was set |
| `explain_dtc` | Plain-language DTC explanation with vehicle-specific context |

## Quick start

### One-command setup (macOS & Linux)

```bash
./setup.sh
```

The script will find (or install via Homebrew/apt) Python 3.10+, create a virtual environment, and install everything. Then:

```bash
source .venv/bin/activate
obd2-mcp --mock
```

### Manual setup

```bash
# Requires Python 3.10+ (the MCP SDK needs it)
# macOS with Homebrew: brew install python@3.12
# Ubuntu/Debian:       sudo apt install python3.12 python3.12-venv

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
obd2-mcp --mock
```

### With a real car

```bash
source .venv/bin/activate

# Auto-detect adapter
obd2-mcp --read-only

# Or specify a port (see "Bluetooth pairing" below for finding the port)
obd2-mcp --read-only --port /dev/tty.vLinkerFD-Android
```

### Bluetooth pairing (vLinker FD)

The Vgate vLinker FD connects via Bluetooth. Here's how to pair it:

1. Plug the vLinker FD into your car's OBD-II port (under the steering wheel)
2. Turn the ignition to ON (engine can be off)
3. On your Mac: **System Settings → Bluetooth** — look for "vLinkerFD-Android"
4. Click **Connect** — enter PIN `1234` (not `0000` — the default `0000` does not work)
5. Once paired, find the serial port:
   ```bash
   ls /dev/tty.*vLinker*
   # Expected output: /dev/tty.vLinkerFD-Android
   ```
6. Use that port name with `--port`

### Claude Desktop / Cowork configuration

The config file lives at:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Open it (create if it doesn't exist) and add:

```json
{
  "mcpServers": {
    "obd2": {
      "command": "/full/path/to/obd2-mcp-server/.venv/bin/obd2-mcp",
      "args": ["--mock"]
    }
  }
}
```

**Demo mode** — use `["--mock"]` as shown above. No car needed.

**Real car** — replace the args with your Bluetooth port:
```json
"args": ["--read-only", "--port", "/dev/tty.vLinkerFD-Android"]
```

After saving, **Cmd+Q** Claude Desktop and reopen it. The read-only OBD tools will appear automatically (look for the hammer icon).

Use the full path to the `obd2-mcp` binary inside the venv so Claude Desktop can find it without activating the environment. The setup script prints the exact path at the end.

## Requirements

- Python 3.10+ (the `mcp` SDK requires it — macOS ships with 3.9 which won't work; use `brew install python@3.12`)
- Any ELM327-compatible OBD-II adapter (for real mode)
- Tested with: Vgate vLinker FD (Bluetooth) on macOS

## DTC Code Database

The server includes **1,937 Ford-specific DTC codes** covering all four categories:

- **321** Powertrain (P) + Network (U) codes
- **797** Body (B) codes — doors, windows, mirrors, seats, roof, HVAC
- **819** Chassis (C) codes — ABS, traction control, suspension, steering

Plus a curated set of common SAE-standard codes and Ford Focus CC-specific diagnostic context for convertible roof troubleshooting.

## Development

```bash
./setup.sh                    # or manual venv setup with Python 3.10+
source .venv/bin/activate
pip install -e ".[dev]"

pytest                        # includes offline CAN replay tests
ruff check src/ tests/        # lint
```

## The story behind this

A 2010 Ford Focus CC convertible — passenger window regulator broke, the roof stopped working. Claude predicted the diagnosis before the scan confirmed it: the roof controller does a real-time window position check (not a stored fault), so the Folding Top Control Module shows zero DTCs even though the roof won't move. The real codes (B1310, B166A) were on the Passenger Door Control Unit.

The mock adapter simulates this exact scenario.

## Local Lexus CT200h BLE extension

This fork enforces **read-only operation** on both BLE and serial backends.
Use `obd2-mcp --read-only --ble ADDRESS` to make the policy explicit in a client
configuration. Read-only is also the default; there is no write-mode option or
MCP argument to disable it. Fault clearing is absent from tool discovery and
rejected even if a stale client calls `clear_dtc(confirm=true)`. Serial queries
and BLE writes have independent read-command guards. Normal transient adapter
initialization and diagnostic read requests still transmit data to the adapter.
It is not a complete replacement for Car Scanner or Techstream.

`get_server_status` is offline. Every JSON response also includes `server` with
the running version, PID, enforced `read_only`, startup source fingerprint, and
`source_matches_disk`. Editing an editable installation does not update a
running process; restart its MCP connection and verify this status afterwards.

* `list_sensors(scope="standard")` discovers advertised Mode 01 PIDs, following
  `0100/0120/0140/...` continuation bits separately for engine `7E0 -> 7E8` and
  hybrid `7E2 -> 7EA`. It reports unknown PIDs even without a decoder.
* `list_sensors(scope="extended")` lists the pinned OBDb candidate definitions
  offline. Follow `next_offset`; the 650 entries are not 650 supported sensors.
* `get_live_data()` reads a page of discovered standard requests. Follow
  `coverage.next_offset`. `scope="extended"` or `"all"` adds applicable candidate
  reads. `limit` bounds ECU requests (default 32, maximum 64), not field count.
* Target exact IDs such as `RPM`, `7EA:HYBRID_BATTERY_REMAINING`, or
  `CT200H_T_BATT_TB1` through `pids`. Several fields from one extended command
  share a single ECU query. Only explicitly requested fields are returned.
* `response_ecus=["7EA"]` restricts candidate sensor reads to the registered
  hybrid responder. Pagination applies after this filter; standard PID
  discovery still checks both emissions ECUs.
* `get_diagnostics(section="all", ecu="engine")` reads stored/pending/permanent
  emissions DTCs (`03/07/0A`), supported identity InfoTypes (`09`), actual freeze
  frame 0 data (`02`), and supported monitor records (`06`). Select `ecu="hybrid"`
  separately and follow `coverage.next_offset`. This is not a scan of all ECUs.

Samples include ECU addresses and timestamps; requests retain raw responses.
`unavailable` distinguishes no data, negative responses, invalid or short
payloads, decoder errors and out-of-range fields. Readings are sequential.
The inventory does not prove exhaustive vehicle coverage; dynamic conditions,
manufacturer definitions and derived Car Scanner metrics can differ.

The profile's local model-year assumption is **2013**. Many upstream definitions
are only validated for other years, so extended readings are marked
`decoded_unverified` even after a successful transport response. Five fields
with block numbers outside the expected 14-block battery layout are quarantined.
Source: [OBDb CT200h at the pinned commit](https://github.com/OBDb/Lexus-CT-200h/blob/d5067c8578d05b0e224df19232798caa660a11b9/signalsets/v3/default.json).
Special ECU subaddress/flow-control routes remain catalogued but cannot be sent
by this transport implementation. No formula is executed as Python code.

ECU selection plus queries are serialized together; each operation restores the
engine route. ISO-TP parsing checks responder, declared length and frame sequence.
A timed-out BLE session is closed to prevent late fragments becoming new samples.
Replay fixtures from CT200h 2011/2016 validate decoding, not this specific car.
Cooperating server processes on one host share an OS-backed adapter lease;
the lease does not exclude an iPhone or another application. Optional developer
tracing (`connection.capture_trace = True`) records BLE notification chunks,
monotonic times, prompts and completed writes in per-request `transport_trace`.
An AT `OK` records acceptance, not proof of actual CAN addressing or filtering.

Strict mode rejects incomplete ISO-TP replies. An explicit
`allow_partial_prefix=true` opt-in handles only the observed `7E2 -> 7EA / 2181`
case with 34 of 35 bytes arriving in order. It exposes the first 14 bounded
voltage fields as `decoded_partial_unverified`, with `transport_complete=false`
and unknown application integrity. It never fills missing bytes or treats this
as a complete response. Leave the option off for ordinary diagnostics.

See [diagnostic behavior and validation limits](docs/ct200h-readonly-diagnostics.md)
for the wire checks, observed coverage, and remaining transport limitations.

The installed package is editable. After changing it, restart the `lexus_obd`
MCP connection to load the new code/tool schemas. The Codex enabled-tools list
must include `list_sensors` and `get_diagnostics`; retain existing disabled
write/action tools. Existing running processes keep the code they already loaded.

The installed package can be checked without accessing a vehicle:

```bash
python scripts/verify_read_only_mcp.py
```

See [saved progress and next steps](docs/progress.md). Private traces, previous
experiments and machine configuration are preserved locally under ignored
`local/`; they are not public replay fixtures.

## License

Server code: MIT. Bundled OBDb definitions and derivative replay fixtures:
CC BY-SA 4.0, copyright OBDb/Lexus-CT-200h contributors. See
`src/obd2_mcp/profiles/ATTRIBUTION.txt` and `OBDb-LICENSE.txt` for source and terms.
