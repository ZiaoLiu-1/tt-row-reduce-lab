#!/usr/bin/env python3
"""Validate a real Metal device-profiler CSV without interpreting silicon speed."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

DEFAULT_ZONES = ("row_reduce_reader", "row_reduce_compute", "row_reduce_writer")
# Schema and enum names come from the pinned writer, not the older RST example:
# tt-metal/89e1256c982a5b4739d173bcc446c8c748a44b40/
# tt_metal/impl/profiler/profiler.cpp:1243-1310 and :335-344.
# Consulted source SHA-256: 0ec310b0068a301aa99b6e3aeb54c4811b5def08eaea60c370bd4109dd48be3b
CSV_COLUMNS = ("pcie slot", "core_x", "core_y", "risc processor type", "timer_id",
               "time[cycles since reset]", "data", "run host id", "trace id",
               "trace id counter", "zone name", "type", "source line", "source file", "meta data")


def validate_csv(path: Path, expected_zones: tuple[str, ...] = DEFAULT_ZONES) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("profiler CSV is missing or empty")
    metadata, header, events = [], None, []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for line_number, row in enumerate(csv.reader(handle), start=1):
            if not row or not any(cell.strip() for cell in row):
                continue
            normalized = [cell.strip().lower() for cell in row]
            if header is None:
                if "zone name" not in normalized:
                    metadata.append(",".join(row).strip())
                    continue
                if len(set(normalized)) != len(normalized):
                    raise ValueError("duplicate profiler header fields")
                if tuple(normalized) != CSV_COLUMNS:
                    raise ValueError("profiler header does not match the pinned Metal CSV fields")
                header = normalized
                continue
            if len(row) != len(header):
                raise ValueError(f"CSV line {line_number} has {len(row)} columns, expected {len(header)}")
            event = dict(zip(header, (cell.strip() for cell in row)))
            for name in ("pcie slot", "core_x", "core_y", "timer_id", "time[cycles since reset]",
                         "data", "run host id", "trace id", "trace id counter", "source line"):
                # The pinned writer uses empty strings for absent trace identity.
                if name in {"trace id", "trace id counter"} and not event[name]:
                    event[name] = None
                    continue
                try:
                    numeric = int(event[name])
                except ValueError as error:
                    raise ValueError(f"CSV line {line_number}: non-integer {name}") from error
                if numeric < 0:
                    raise ValueError(f"CSV line {line_number}: negative {name}")
                event[name] = numeric
            event["device"] = event["pcie slot"]
            if not event["risc processor type"] or not event["source file"]:
                raise ValueError(f"CSV line {line_number}: empty processor/source field")
            events.append(event)
    if header is None:
        raise ValueError("profiler CSV has no header")
    if not events:
        raise ValueError("profiler CSV has no event rows")
    opened: dict[tuple, list[int]] = defaultdict(list)
    counts: Counter = Counter()
    intervals = []
    for event in events:
        zone = event["zone name"]
        if zone not in expected_zones:
            continue
        key = (event["device"], event["core_x"], event["core_y"], event["risc processor type"],
               event["run host id"], event["trace id"], event["trace id counter"],
               zone, event["source file"], event["source line"])
        phase = event["type"]
        cycle = event["time[cycles since reset]"]
        if phase == "ZONE_START":
            opened[key].append(cycle)
        elif phase == "ZONE_END":
            if not opened[key]:
                raise ValueError(f"zone {zone}: end without a matching begin")
            begin = opened[key].pop()
            if cycle < begin:
                raise ValueError(f"zone {zone}: end counter precedes begin counter")
            counts[zone] += 1
            intervals.append({"zone": zone, "device": event["device"], "core_x": event["core_x"],
                              "core_y": event["core_y"], "processor": event["risc processor type"],
                              "run_host_id": event["run host id"], "trace_id": event["trace id"],
                              "trace_id_counter": event["trace id counter"], "begin_raw_counter": begin,
                              "end_raw_counter": cycle, "source_file": event["source file"],
                              "source_line": event["source line"]})
        else:
            raise ValueError(f"zone {zone}: unexpected phase {phase!r}")
    if any(stack for stack in opened.values()):
        raise ValueError("profiler CSV contains unclosed project zones")
    absent = [zone for zone in expected_zones if counts[zone] == 0]
    if absent:
        raise ValueError("profiler CSV lacks complete project zones: " + ", ".join(absent))
    return {"schema_version": 1, "status": "pass", "measurement_kind": "simulator_instrumentation",
            "hardware": "none", "csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "metadata": metadata, "event_rows": len(events), "project_zone_intervals": dict(counts),
            "intervals": intervals,
            "interpretation": "CSV structure and project zones only; raw counters are not silicon timing"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, help="write validation JSON; refuses to overwrite")
    parser.add_argument("--zone", action="append", help="required zone; repeat to override project defaults")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("--output already exists")
    try:
        result = validate_csv(args.csv, tuple(args.zone) if args.zone else DEFAULT_ZONES)
    except (ValueError, OSError, UnicodeError, csv.Error) as error:
        result = {"schema_version": 1, "status": "fail", "hardware": "none",
                  "measurement_kind": "simulator_instrumentation", "error": str(error)}
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
