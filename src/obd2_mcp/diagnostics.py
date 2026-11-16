"""Read-only CT200h sensor catalogue, strict CAN framing, and value decoding.

The bundled OBDb data is a candidate catalogue, NOT a claim of vehicle support.
No formula is evaluated as code and no command is accepted from MCP callers.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCE_COMMIT = "d5067c8578d05b0e224df19232798caa660a11b9"
SOURCE_URL = f"https://github.com/OBDb/Lexus-CT-200h/blob/{SOURCE_COMMIT}/signalsets/v3/default.json"
STANDARD_ECUS = (("7E0", "7E8"), ("7E2", "7EA"))
MODEL_YEAR = 2013
# These upstream labels describe blocks beyond the CT200h's 14-block layout.
# Keep their definitions/raw responses visible, but never publish them as measurements.
QUARANTINED_IDS = frozenset({
    "CT200H_BAT_BLOCK_V_V16", "CT200H_BAT_BLOCK_V_V17",
    "CT200H_INTERNAL_RES_R15", "CT200H_INTERNAL_RES_R16", "CT200H_INTERNAL_RES_R17",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class NegativeResponse(ValueError):
    def __init__(self, code: int):
        self.code = code
        super().__init__(f"ECU negative response NRC 0x{code:02X}")


class IncompleteResponse(ValueError):
    """Only an ordered, contiguous prefix; never constructed after a gap/duplicate."""

    def __init__(self, prefix: bytes, expected_length: int):
        self.prefix = prefix
        self.expected_length = expected_length
        super().__init__(f"Incomplete ISO-TP response: {len(prefix)}/{expected_length} bytes")


class ResponsePending(ValueError):
    """The ECU asked for more time; there is no final application response yet."""


def can_message(raw: str, response_ecu: str) -> bytes:
    """Reassemble exactly one headers-on, 11-bit CAN ISO-TP message.

    Reject missing/out-of-order/truncated frames. Trim CAN padding using the
    declared ISO-TP length. Replies from other ECUs never enter the assembly.
    """
    payload = bytearray()
    expected = None
    sequence = 1
    for line in raw.replace("\r", "\n").splitlines():
        flat = re.sub(r"\s+", "", line).upper()
        if not re.fullmatch(r"[0-7][0-9A-F]{2}(?:[0-9A-F]{2})+", flat):
            continue
        if flat[:3] != response_ecu:
            continue
        frame = bytes.fromhex(flat[3:])
        if len(frame) > 8:
            raise ValueError("Classic CAN frame exceeds eight bytes")
        kind = frame[0] >> 4
        if kind == 0:
            if expected is not None:
                raise ValueError("Single frame interrupted a multi-frame response")
            size = frame[0] & 15
            if not 1 <= size <= 7 or len(frame) < size + 1:
                raise ValueError("Truncated/invalid ISO-TP single frame")
            return frame[1:1 + size]
        if kind == 1:
            if expected is not None or len(frame) < 3:
                raise ValueError("Invalid/repeated ISO-TP first frame")
            expected = ((frame[0] & 15) << 8) | frame[1]
            if expected <= 7:
                raise ValueError("Invalid ISO-TP multi-frame length")
            payload.extend(frame[2:])
        elif kind == 2:
            if expected is None or (frame[0] & 15) != sequence:
                raise ValueError("Missing/out-of-order ISO-TP consecutive frame")
            if len(frame) < 2:
                raise ValueError("Empty ISO-TP consecutive frame")
            sequence = (sequence + 1) & 15
            payload.extend(frame[1:])
        else:
            raise ValueError("Unexpected ISO-TP frame type")
        if expected is not None and len(payload) >= expected:
            return bytes(payload[:expected])
    if expected is not None:
        raise IncompleteResponse(bytes(payload), expected)
    raise ValueError(f"No framed response from ECU {response_ecu}")


def response_data(raw: str, response_ecu: str, command: str) -> bytes:
    if "BUFFER FULL" in raw.upper():
        raise ValueError("Adapter buffer overrun; transaction integrity is unknown")
    service = int(command[:2], 16)
    pending = False
    lines = []
    for line in raw.replace(">", "\n").replace("\r", "\n").splitlines():
        flat = re.sub(r"\s+", "", line).upper()
        if re.fullmatch(re.escape(response_ecu) + r"(?:[0-9A-F]{2})+", flat):
            frame = bytes.fromhex(flat[3:])
            if len(frame) >= 4 and frame[0] == 3 and frame[1:4] == bytes((0x7F, service, 0x78)):
                pending = True
                continue
        lines.append(line)
    try:
        message = can_message("\n".join(lines), response_ecu)
    except ValueError as error:
        if pending and not any(re.sub(r"\s+", "", line).upper().startswith(response_ecu)
                               for line in lines):
            raise ResponsePending("ECU response pending; no final response received") from error
        raise
    if message[:2] == bytes((0x7F, service)) and len(message) >= 3:
        raise NegativeResponse(message[2])
    tag = bytes((service + 0x40,)) + bytes.fromhex(command[2:])
    if not message.startswith(tag):
        raise ValueError(f"Response does not match request {command}")
    return message[len(tag):]


def filter_matches(spec: dict, year: int) -> bool:
    """OBDb year selectors; inverted bounds denote two outside ranges."""
    if year in spec.get("years", []):
        return True
    low, high = spec.get("from"), spec.get("to")
    if low is not None and high is not None:
        if low <= high:
            return low <= year <= high
        return year >= low or year <= high
    if low is not None:
        return year >= low
    if high is not None:
        return year <= high
    return not spec.get("years")


@lru_cache(maxsize=1)
def extended_catalogue() -> tuple[dict, ...]:
    path = Path(__file__).with_name("profiles") / "lexus_ct200h.json"
    data = json.loads(path.read_text())
    result = []
    for command in data["commands"]:
        command = dict(command)
        service, pid = next(iter(command["cmd"].items()))
        request = service + pid
        supported_transport = (
            len(command["cmd"]) == 1
            and re.fullmatch(r"(?:21[0-9A-F]{2}|22[0-9A-F]{4})", request) is not None
            and re.fullmatch(r"[0-7][0-9A-F]{2}", command["hdr"]) is not None
            and re.fullmatch(r"[0-7][0-9A-F]{2}", command["rax"]) is not None
            and not any(k in command for k in ("eax", "fcm1"))
        )
        command["request"] = request
        command["transport_supported"] = supported_transport
        command["applicable_year"] = filter_matches(command.get("filter", {}), MODEL_YEAR)
        # A successful read validates transport; it does not validate a formula.
        command["verification"] = (
            "unverified_for_model_year"
            if command.get("dbg") or (
                command.get("dbgfilter")
                and filter_matches(command["dbgfilter"], MODEL_YEAR)
            ) else "community_definition"
        )
        result.append(command)
    return tuple(result)


@lru_cache(maxsize=1)
def standard_commands() -> dict[int, Any]:
    import obd
    return {
        c.pid: c for c in obd.commands[1]
        if c and re.fullmatch(rb"01[0-9A-F]{2}", c.command)
    }


def request_allowlist() -> frozenset[str]:
    commands = {f"01{pid:02X}" for pid in range(256)}
    for tx, rx in STANDARD_ECUS:
        commands.update((f"ATSH{tx}", f"ATCRA{rx}"))
    for definition in extended_catalogue():
        if definition["transport_supported"]:
            commands.update((definition["request"], f"ATSH{definition['hdr']}",
                             f"ATCRA{definition['rax']}"))
    return frozenset(commands)


def supported_pids(data: bytes, base: int) -> list[int]:
    if len(data) != 4:
        raise ValueError("Supported-PID bitmap must contain exactly four bytes")
    bits = int.from_bytes(data, "big")
    return [base + bit + 1 for bit in range(32) if bits & (1 << (31 - bit))]


def standard_definition(pid: int, tx: str, rx: str) -> dict:
    cmd = standard_commands().get(pid)
    name = cmd.name if cmd else f"PID_{pid:02X}"
    return {
        "hdr": tx, "rax": rx, "request": f"01{pid:02X}", "standard_pid": pid,
        "id": name if rx == "7E8" else f"{rx}:{name}",
        "name": cmd.desc if cmd else f"Standard PID 0x{pid:02X} (raw only)",
        "verification": "advertised_by_ecu", "scope": "standard",
    }


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if hasattr(value, "magnitude") and hasattr(value, "units"):
        return {"value": json_value(value.magnitude), "unit": format(value.units, "~")}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, bytes):
        return value.hex().upper()
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items() if k is not None}
    if hasattr(value, "__dict__"):
        return {str(k): json_value(v) for k, v in vars(value).items()
                if isinstance(k, str) and not k.startswith("_")}
    return str(value)


def decode_standard(definition: dict, data: bytes) -> dict:
    from obd.protocols.protocol import Message
    cmd = standard_commands().get(definition["standard_pid"])
    if cmd is None:
        return {"value": data.hex().upper(), "unit": "", "status": "raw_only"}
    # python-OBD otherwise pads a truncated response with zeros. Never do that.
    if cmd.bytes > 0 and len(data) + 2 < cmd.bytes:
        raise ValueError(f"Truncated PID: {len(data) + 2}/{cmd.bytes} bytes")
    message = Message([])
    message.ecu = cmd.ecu
    message.data = bytearray.fromhex("41" + definition["request"][2:]) + data
    value = cmd.decode([message])
    if value is None:
        raise ValueError("Decoder returned no value")
    converted = json_value(value)
    if hasattr(value, "magnitude"):
        return {**converted, "status": "ok"}
    return {"value": converted, "unit": "", "status": "ok"}


def decode_signal(signal: dict, data: bytes) -> dict:
    if signal.get("id") in QUARANTINED_IDS:
        return {"value": None, "unit": signal["fmt"].get("unit", ""),
                "status": "definition_quarantined",
                "note": "Upstream block label conflicts with the expected CT200h battery layout."}
    fmt = signal["fmt"]
    bix, length = fmt.get("bix", 0), fmt["len"]
    if fmt.get("blsb"):
        raise ValueError("Little-endian OBDb bit format is not implemented")
    if bix < 0 or length <= 0 or bix + length > len(data) * 8:
        raise ValueError(f"Field exceeds payload: bits {bix}:{bix + length}/{len(data) * 8}")
    raw = (int.from_bytes(data, "big") >> (len(data) * 8 - bix - length)) & ((1 << length) - 1)
    if fmt.get("sign") and raw & (1 << (length - 1)):
        raw -= 1 << length
    value = raw * fmt.get("mul", 1) / fmt.get("div", 1) + fmt.get("add", 0)
    result = {"value": round(value, 6), "unit": fmt.get("unit", ""),
              "raw_value": raw, "status": "decoded_unverified"}
    if (("nullmin" in fmt and value <= fmt["nullmin"])
            or ("nullmax" in fmt and value >= fmt["nullmax"])):
        result.update(value=None, status="not_available")
    elif value < fmt.get("min", -math.inf) or value > fmt.get("max", math.inf):
        result["status"] = "out_of_range"
    elif str(raw) in fmt.get("map", {}):
        result["label"] = fmt["map"][str(raw)]
    return result


def eligible_extended() -> list[dict]:
    return [d for d in extended_catalogue()
            if d["transport_supported"] and d["applicable_year"]]


def resolve_sensor_ids(ids: list[str]) -> list[dict]:
    """Resolve exact catalogue IDs; untrusted strings never become commands."""
    available = {}
    for tx, rx in STANDARD_ECUS:
        for pid in standard_commands():
            if pid % 32:
                d = standard_definition(pid, tx, rx)
                d["verification"] = "support_not_probed"
                available[d["id"]] = d
    for d in eligible_extended():
        for signal in d["signals"]:
            available[signal["id"]] = d
    results = []
    for sensor_id in ids:
        key = sensor_id.upper()
        if key not in available:
            # Unknown standard PID may be advertised even without a decoder.
            match = re.fullmatch(r"(?:(7E8|7EA):)?PID_([0-9A-F]{2})", key)
            if not match or int(match[2], 16) % 32 == 0:
                raise ValueError(f"Unknown read-only sensor ID: {sensor_id}")
            rx = match[1] or "7E8"
            d = standard_definition(int(match[2], 16), "7E0" if rx == "7E8" else "7E2", rx)
            d["verification"] = "support_not_probed"
        else:
            d = available[key]
        if d not in results:
            results.append(d)
    return results
