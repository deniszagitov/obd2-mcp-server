# CT200h read-only BLE diagnostics

This branch extends the ELM327/Bleak backend, not a dashboard. It uses fixed
read requests and explicit decoder/transport status. It does not implement
arbitrary CAN transmission, active tests, ECU coding, security access or firmware
updates. The serial live-data path also validates the whole requested batch as
known Mode 01 commands before it queries anything.

Read-only is enforced by default and can be declared explicitly with
`--read-only`. This fork provides no write mode. `clear_dtc` is not advertised;
stale calls are denied before connecting, regardless of `confirm`. Both real
drivers reject fault clearing directly. Every serial query passes a read-service
guard; BLE uses its fixed command allowlist. Adapter setup (reset, headers,
timing and notifications) remains necessary for diagnostics and is session-local.
This policy covers this server's commands, not arbitrary programs with separate
operating-system access to the adapter.

## Tools

| Tool | Behavior |
| --- | --- |
| `list_sensors` | Paged ECU-advertised Mode 01 inventory, or an offline pinned OEM candidate catalogue. |
| `get_live_data` | Paged sequential standard/OEM reads, exact field IDs, optional registered-responder filter. |
| `get_diagnostics` | Standard diagnostic reads for engine or hybrid emissions ECU, independently paged. |
| `read_dtc` | Stored emissions codes from `7E8`, using the same strict transport and count decoder. |
| `get_server_status` | Offline runtime version/PID, enforced read-only policy and loaded-source fingerprint. |

Examples of MCP arguments:

```json
{"scope":"extended","response_ecus":["7EA"],"offset":0,"limit":16}
```

```json
{"section":"monitors","ecu":"engine","offset":0,"limit":32}
```

The first example is for `get_live_data`; the second is for `get_diagnostics`.
The sensor page limit counts unique ECU requests, not returned fields. Follow
`coverage.next_offset` until null, retaining previous pages. Discovery requests
are separately reported. A selected catalogue being exhausted never means
vehicle coverage is complete.

## Standard diagnostic services

* `03`, `07`, `0A`: stored, pending and permanent DTCs, with CAN record counts.
  A complete zero count is different from `NO DATA` or an invalid response.
* `09`: supported InfoTypes, VIN, calibration IDs, CVNs and ECU names. Malformed
  records are rejected. VIN filler bytes yield `availability=not_available`.
* `02`: supported PIDs and actual data for freeze frame **0**, including its
  triggering DTC. No snapshot is fabricated when the ECU returns no data.
* `06`: supported MIDs and complete MID/TID/UASID/value/min/max records. Five
  observed scaling IDs are normalized; unknown identifiers retain raw numbers.
  Signedness applies equally to values and limits. `passed` remains null:
  mathematical bounds alone do not establish test completion or vehicle health.
  Misfire TID `0B/0C` accumulators have no threshold verdict.

Support bitmaps follow continuation bits, separately per selected ECU. These
standard services cover two emissions responders, not every Toyota diagnostic
system. OEM enhanced DTCs and their subcodes are not yet implemented.

## Transport and ownership

The BLE write boundary permits only fixed initialization/status commands,
registered ECU routes, standard read services and pinned catalogue reads.
Programmable-parameter writes and command injection are rejected. ECU selection,
query pages and engine-route restoration share one session lock. Cooperating
processes on the same host must hold an OS-backed lease for the adapter; it
cannot exclude independent applications or a phone.

The parser checks exact responder, service/PID, ISO-TP declared length and
consecutive-frame sequence. It does not join different ECU payloads, pad short
messages with zeros, clamp invalid values, or turn transport errors into empty
fault lists. `BUFFER FULL` invalidates a transaction. NRC `78` waits for a final
reply within a bounded interval without resending the request. A timeout closes
the link so late fragments cannot become a subsequent sample.

Developer opt-in `connection.capture_trace = True` adds raw notification chunks,
UTC and monotonic times, requested route, write completion and prompt evidence.
BLE write completion is not confirmation that an ECU received the requested CAN
frame. Traces may contain vehicle identifiers; they are not bundled as fixtures.

## Measured coverage and unresolved limits

Controlled reads on one CT200h 2013 with a BLE adapter identifying as ELM327
v2.1, on 16/17 November 2026:

* Mode 01 advertised 57 ECU/PID records: 35 engine and 22 hybrid. Some describe
  the same quantity, so this is not 57 independent physical sensors.
* All 215 applicable, supported-transport OEM catalogue requests were attempted.
  There were 44 complete responses, 2 incomplete responses, 31 selected-ECU
  negative responses, and 138 responses without the requested responder.
  The complete replies yielded 228 fields marked `decoded_unverified`.
  Successful framing does not validate a community formula on this calibration.
  Some other fields exceed the received payload, violate defined ranges, or are
  unavailable; they stay outside the successful readings list.
* Engine Mode 06 returned 17 records across 9 MIDs. Stored/pending emissions
  DTCs reported zero in both selected ECUs. Permanent DTC `0A` returned no data,
  so its fault state remains unknown. This is not a vehicle-wide health claim.
* `ATSH`/`ATCRA` returned `OK`, but responses still contained both `7E8` and
  `7EA`. No requested body/brake/TPMS responder was observed. A software header
  setting therefore does not prove actual physical addressing or filtering on
  this adapter. The cause remains unresolved.
* `7E8 / 2106` declared 12 bytes and returned 6. `7EA / 2181` repeatedly
  declared 35 and returned 34, missing its final consecutive frame. The latter
  arrived in one BLE notification including the prompt: splitting host-side
  notifications did not cause that observed truncation. Flow-control, timing,
  spaced AT commands and long-message probes did not recover the missing frame.
  An adapter limit, ECU behavior and a CAN-side capture remain separate questions.

`allow_partial_prefix=true` is an experimental exception only for the exact
`7E2 -> 7EA / 2181`, 35-declared/34-received case. All received frames must be in
sequence. Only the first 224 data bits (14 voltage fields) may be decoded, with
`decoded_partial_unverified`, `transport_complete=false`, `field_complete=true`,
`application_integrity=unknown`, and `checksum_valid=null`. No missing tail byte
is synthesized and no application checksum is asserted. Default strict mode
returns `incomplete_response` and no voltage readings for this message.

The pinned OBDb source contains 650 candidate fields across 221 commands, not a
list of supported sensors. Year filtering and unsupported special-address routes
reduce the attempted set to 215 requests. Five battery fields with block labels
outside the expected 14-block layout are quarantined. Other community formulas
and labels still require independent validation; apparent physical plausibility
is not sufficient. Mode 01 PID `5B` must not be interpreted as an independently
measured battery state of health.

## Offline validation

The tests exercise whole-batch read validation, command injection, process
ownership, notification buffer lifetime, pending responses, exact ECU matching,
ISO-TP truncation/order, partial-prefix bounds, signed scaling, diagnostic counts,
pagination and decoder replay. OBDb fixtures come from other model years and
validate decoding only. They do not substitute for hardware acceptance.

The local hardware traces remain separate from the public source tree. Before
upstream publication, split the serial safety fix, transport changes, standard
diagnostics and licensed CT200h definitions into independently reviewable changes.

The installed package is checked end-to-end by `scripts/verify_read_only_mcp.py`:
real stdio MCP initialization/version, tool discovery and offline BLE catalogue;
action-denial checks use a separate **mock** process only. Runtime fingerprints
identify the source captured at process startup. `source_matches_disk=false`
means the files have changed since that process loaded them. Restart that MCP
connection and verify a fresh fingerprint rather than inferring a restart from
an editable install or a configuration edit.

Server code remains MIT. OBDb definitions and derivative replay fixtures retain
their separate CC BY-SA 4.0 attribution in `profiles/` and `tests/fixtures/`.
