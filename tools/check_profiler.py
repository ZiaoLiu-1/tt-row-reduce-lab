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
REQUIRED_COLUMNS = ("core_x", "core_y", "risc processor type", "timer_id",
                    "time[cycles since reset]", "stat value", "run id", "zone name",
                    "zone phase", "source line", "source file")


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
                if any(name not in normalized for name in REQUIRED_COLUMNS):
                    raise ValueError("profiler header does not match the pinned Metal CSV fields")
                if len(set(normalized)) != len(normalized):
                    raise ValueError("duplicate profiler header fields")
                if normalized[0] not in {"pcie slot", "chip id", "device id"}:
                    raise ValueError("unknown profiler device-id column")
                header = normalized
                continue
            if len(row) != len(header):
                raise ValueError(f"CSV line {line_number} has {len(row)} columns, expected {len(header)}")
            event = dict(zip(header, (cell.strip() for cell in row)))
            event["device"] = event[header[0]]
            for name in (header[0], "core_x", "core_y", "timer_id", "time[cycles since reset]",
                         "stat value", "run id", "source line"):
                try:
                    numeric = int(event[name])
                except ValueError as error:
                    raise ValueError(f"CSV line {line_number}: non-integer {name}") from error
                if numeric < 0:
                    raise ValueError(f"CSV line {line_number}: negative {name}")
                event[name] = numeric
            if not event["risc processor type"] or not event["source file"]:
                raise ValueError(f"CSV line {line_number}: empty processor/source field")
            events.append(event)
    if header is None or not events:
        raise ValueError("profiler CSV has no header or no event rows")
    opened: dict[tuple, list[int]] = defaultdict(list)
    counts: Counter = Counter()
    intervals = []
    for event in events:
        zone = event["zone name"]
        if zone not in expected_zones:
            continue
        key = (event["device"], event["core_x"], event["core_y"], event["risc processor type"],
               event["run id"], zone, event["source file"], event["source line"])
        phase = event["zone phase"].lower()
        cycle = event["time[cycles since reset]"]
        if phase == "begin":
            opened[key].append(cycle)
        elif phase == "end":
            if not opened[key]:
                raise ValueError(f"zone {zone}: end without a matching begin")
            begin = opened[key].pop()
            if cycle < begin:
                raise ValueError(f"zone {zone}: end counter precedes begin counter")
            counts[zone] += 1
            intervals.append({"zone": zone, "device": event["device"], "core_x": event["core_x"],
                              "core_y": event["core_y"], "processor": event["risc processor type"],
                              "run_id": event["run id"], "begin_raw_counter": begin,
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
