"""Pure decoders for standard read services; no ECU action commands."""

from __future__ import annotations

import re

SECTIONS = ("dtc", "identity", "freeze_frame", "monitors")
DTC_SERVICES = {"03": "stored", "07": "pending", "0A": "permanent"}
INFO_NAMES = {1: "VIN message count", 2: "VIN", 3: "Calibration message count",
              4: "Calibration IDs", 5: "CVN message count", 6: "Calibration verification numbers",
              7: "Performance tracking message count", 8: "Performance tracking",
              9: "ECU name message count", 10: "ECU names"}

# Numeric UASID facts cross-checked against the independently published table:
# https://github.com/shinyorg/obd/blob/0352328690724e220702f43be0b036e33e2aa38c/src/Shiny.Obd/Commands/UnitAndScaling.cs
# Restrict normalization to the identifiers observed on this CT200h. Unknown
# identifiers retain raw values and no inferred numeric/health result.
MONITOR_SCALING = {
    0x0B: (0.001, False, "V"),
    0x8D: (0.00390625, True, "mA"),
    0x86: (0.000305, True, "scaled_raw"),
    0x17: (0.01, False, "kPa"),
    0x24: (1, False, "count"),
}
MONITOR_NAMES = {0x01: "Oxygen sensor bank 1 sensor 1", 0x02: "Oxygen sensor bank 1 sensor 2",
                 0x21: "Catalyst bank 1", 0x31: "EGR bank 1", 0xA1: "General misfire"}
MONITOR_NAMES.update({0xA1 + cylinder: f"Misfire cylinder {cylinder}" for cylinder in range(1, 5)})


def standard_read_commands() -> frozenset[str]:
    # Freeze-frame requests are explicitly frame 0. Other frames need separate support.
    return frozenset(DTC_SERVICES) | frozenset(
        f"{service}{pid:02X}" for service in ("06", "09") for pid in range(256)
    ) | frozenset(f"02{pid:02X}00" for pid in range(256))


def dtc_code(value: bytes) -> str:
    if len(value) != 2:
        raise ValueError("A DTC must contain exactly two bytes")
    first, second = value
    return f"{'PCBU'[first >> 6]}{(first >> 4) & 3}{first & 15:X}{second:02X}"


def decode_dtcs(data: bytes, service: str) -> dict:
    if not data:
        raise ValueError("Missing CAN DTC count; fault state is unknown")
    count = data[0]
    if len(data) < 1 + count * 2:
        raise ValueError("Truncated CAN DTC list; fault state is unknown")
    codes = []
    for i in range(count):
        pair = data[1 + 2 * i:3 + 2 * i]
        if pair == b"\x00\x00":
            raise ValueError("DTC count includes an empty record; fault state is unknown")
        codes.append({"code": dtc_code(pair), "status": DTC_SERVICES[service]})
    return {"dtc_status": DTC_SERVICES[service], "reported_count": count, "dtcs": codes}


def decode_info(data: bytes, pid: int) -> dict:
    result = {"info_type": f"{pid:02X}", "name": INFO_NAMES.get(pid, "Uninterpreted information"),
              "raw_hex": data.hex().upper()}
    if pid in (1, 3, 5, 7, 9):
        if not data:
            raise ValueError("Missing information message count")
        return {**result, "message_count": data[0]}
    if pid in (2, 4, 6, 10):
        if not data:
            raise ValueError("Missing information record count")
        count, content = data[0], data[1:]
        result["record_count"] = count
        if pid == 2:
            if count == 1 and len(content) == 17 and set(content) <= {0, 255}:
                return {**result, "vin": None, "availability": "not_available",
                        "note": "ECU returned filler bytes instead of a VIN; identity is unknown."}
            # Do not impose the North American check-digit rule on EU/Japan VINs.
            vin = content.decode("ascii", errors="strict").strip("\x00 ")
            if count != 1 or not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
                raise ValueError("VIN response is not one complete 17-character record")
            result["vin"] = vin
        elif pid == 6:
            if len(content) < count * 4:
                raise ValueError("Truncated calibration verification numbers")
            result["cvns"] = [content[i * 4:(i + 1) * 4].hex().upper() for i in range(count)]
        else:
            size = 16 if pid == 4 else 20
            if len(content) < count * size:
                raise ValueError("Truncated fixed-width identification records")
            result["records"] = [content[i * size:(i + 1) * size].decode("ascii", errors="strict")
                                 .strip("\x00 ") for i in range(count)]
    return result


def decode_monitor(data: bytes, mid: int) -> dict:
    # response_data stripped the first MID along with service 46. Each following
    # record repeats MID/TID/UASID, so reconstruct it before splitting records.
    records = bytes((mid,)) + data
    if len(records) % 9:
        raise ValueError("Incomplete Mode 06 record (expected MID/TID/UASID/value/min/max)")
    tests = []
    for start in range(0, len(records), 9):
        record = records[start:start + 9]
        if record[0] != mid:
            raise ValueError("Mode 06 response contains a different monitor ID")
        test = {"mid": f"{mid:02X}", "tid": f"{record[1]:02X}",
                      "unit_scaling_id": f"{record[2]:02X}",
                      "value_raw": int.from_bytes(record[3:5], "big"),
                      "minimum_raw": int.from_bytes(record[5:7], "big"),
                      "maximum_raw": int.from_bytes(record[7:9], "big"),
                      "raw_hex": record.hex().upper(), "value": None, "unit": None,
                      "passed": None, "within_reported_limits": None, "scaling_known": False,
                      "note": "Raw values retained; unit scaling/signedness is not validated. "
                              "No pass/fail conclusion is inferred from unsigned raw values."}
        scaling = MONITOR_SCALING.get(record[2])
        if scaling:
            multiplier, signed, unit = scaling

            def scale(raw, multiplier=multiplier, signed=signed):
                value = raw - 65536 if signed and raw >= 32768 else raw
                return round(value * multiplier, 9)

            value, minimum, maximum = (scale(test[k]) for k in
                                       ("value_raw", "minimum_raw", "maximum_raw"))
            test.update(value=value, minimum=minimum, maximum=maximum, unit=unit,
                        scaling_known=True, note="Scaled numeric result; TID meaning/test completion "
                                                 "and overall vehicle health are not inferred.")
            if 0xA1 <= mid <= 0xA5 and record[1] in (0x0B, 0x0C):
                # These standardized misfire accumulators have no threshold limits.
                test.update(minimum=None, maximum=None, limit_semantics="not_applicable_accumulator")
            elif minimum <= maximum:
                test["within_reported_limits"] = minimum <= value <= maximum
        tests.append(test)
    return {"monitor_id": f"{mid:02X}", "monitor_name": MONITOR_NAMES.get(mid), "tests": tests}
