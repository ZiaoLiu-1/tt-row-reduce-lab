#!/usr/bin/env python3
"""Exercise the linked Metal CLI's input rejection path before device creation.

This is C1 host validation. It requires no simulator asset and never establishes
device JIT or device execution. Invoke only with the project's real Metal host
binary; test fixtures used in unit tests are not execution evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import platform
import struct
import sys

from run_matrix import RESULT_PREFIX, run_subprocess, sha256_file, source_identity, utc_now

ERROR_PREFIX = "TT_ROW_REDUCE_ERROR="


@dataclass(frozen=True)
class RejectionCase:
    case_id: str
    arguments: tuple[str, ...]
    input_text: str | None = None


def required_cases() -> tuple[RejectionCase, ...]:
    size_max = str((1 << (8 * struct.calcsize("P"))) - 1)
    return (
        RejectionCase("rows_zero", ("--rows", "0", "--cols", "32")),
        RejectionCase("cols_zero", ("--rows", "32", "--cols", "0")),
        RejectionCase("rows_129", ("--rows", "129", "--cols", "1")),
        RejectionCase("cols_129", ("--rows", "1", "--cols", "129")),
        RejectionCase("shape_product_overflow", ("--rows", size_max, "--cols", "2")),
        RejectionCase("nodes_zero", ("--rows", "1", "--cols", "1", "--nodes", "0")),
        RejectionCase("nodes_two", ("--rows", "1", "--cols", "1", "--nodes", "2")),
        RejectionCase("input_nan", ("--rows", "1", "--cols", "1"), "nan\n"),
        RejectionCase("input_inf", ("--rows", "1", "--cols", "1"), "inf\n"),
        RejectionCase("input_above_range", ("--rows", "1", "--cols", "1"), "2\n"),
        RejectionCase("input_below_range", ("--rows", "1", "--cols", "1"), "0.001\n"),
        RejectionCase("host_length_short", ("--rows", "1", "--cols", "2"), "1\n"),
        RejectionCase("host_length_long", ("--rows", "1", "--cols", "1"), "1 0\n"),
        RejectionCase("host_length_empty", ("--rows", "1", "--cols", "1"), ""),
    )


def classify_rejection(exit_code: int, timed_out: bool, stdout: str, stderr: str) -> dict:
    """Exit 2 alone is insufficient: the host must identify its pre-device path."""
    if timed_out:
        return {"status": "timeout", "error": "host did not reject within the timeout"}
    combined = stdout + "\n" + stderr
    if RESULT_PREFIX in combined:
        return {"status": "validation-fail", "error": "invalid input produced an execution result"}
    if exit_code != 2:
        return {"status": "validation-fail" if exit_code == 0 else "crash",
                "error": f"expected input rejection exit 2; received {exit_code}"}
    lines = [line[len(ERROR_PREFIX):] for line in combined.splitlines() if line.startswith(ERROR_PREFIX)]
    if len(lines) != 1:
        return {"status": "protocol-error", "error": "expected exactly one structured host rejection"}
    try:
        error = json.loads(lines[0])
    except (ValueError, TypeError) as problem:
        return {"status": "protocol-error", "error": f"malformed host rejection: {problem}"}
    if not isinstance(error, dict):
        return {"status": "protocol-error", "error": "host rejection must be a JSON object"}
    if error.get("before_device") is not True or error.get("status") != "invalid-input":
        return {"status": "validation-fail", "error": "host did not reject before device creation",
                "host_error": error}
    if not isinstance(error.get("message"), str) or not error["message"].strip():
        return {"status": "protocol-error", "error": "host rejection is missing its reason"}
    return {"status": "pass", "host_error": error}


def run_case(case: RejectionCase, binary: Path, cwd: Path, output: Path,
             timeout: float, env: dict, identity: dict) -> dict:
    arguments = list(case.arguments)
    recorded_arguments = list(arguments)
    record = {"schema_version": 1, "case_id": case.case_id, "started_at_utc": utc_now(),
              "identity": identity, "validation_stage": "C1",
              "validation_kind": "host_pre_device_rejection", "device_execution": False,
              "measurement_kind": "host_rejection_process_wall_time", "hardware": "none"}
    if case.input_text is not None:
        fixture = output / "inputs" / f"{case.case_id}.txt"
        fixture.write_text(case.input_text)
        arguments.extend(("--input-file", str(fixture)))
        recorded_arguments.extend(("--input-file", str(fixture.relative_to(output))))
        record.update(input_file=str(fixture.relative_to(output)), input_sha256=sha256_file(fixture))
    stdout = output / "raw" / f"{case.case_id}.stdout.log"
    stderr = output / "raw" / f"{case.case_id}.stderr.log"
    record.update(command=["<binary>", *recorded_arguments],
                  stdout=str(stdout.relative_to(output)), stderr=str(stderr.relative_to(output)))
    try:
        code, timed_out, elapsed = run_subprocess([str(binary), *arguments], cwd, env, timeout, stdout, stderr)
        record.update(exit_code=code, timed_out=timed_out, wall_seconds=elapsed)
        record.update(classify_rejection(code, timed_out, stdout.read_text(errors="replace"),
                                         stderr.read_text(errors="replace")))
    except OSError as error:
        record.update(status="unsupported", exit_code=None, timed_out=False, wall_seconds=None, error=str(error))
    record["log_sha256"] = {path.name: sha256_file(path) for path in (stdout, stderr) if path.is_file()}
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path, help="actual linked tt_row_reduce_metal binary")
    parser.add_argument("--output-dir", required=True, type=Path, help="new evidence directory; must not exist")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cwd", type=Path, help="process working directory; defaults to source root")
    parser.add_argument("--timeout", type=float, default=15, help="seconds per rejection process (default: 15)")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("--output-dir already exists; refusing to overwrite evidence")
    binary, source = args.binary.resolve(), args.source_root.resolve()
    cwd = args.cwd.resolve() if args.cwd else source
    output.mkdir(parents=True)
    (output / "raw").mkdir()
    (output / "inputs").mkdir()
    environment = {"schema_version": 1, "recorded_at_utc": utc_now(), "os": platform.system(),
                   "machine": platform.machine(), "python": platform.python_version(),
                   "host_size_t_bits": struct.calcsize("P") * 8, "source": source_identity(source),
                   "validation_stage": "C1", "validation_kind": "host_pre_device_rejection",
                   "device_execution": False, "simulator_required": False, "hardware": "none",
                   "timeout_seconds": args.timeout}
    if not binary.is_file() or not os.access(binary, os.X_OK) or not cwd.is_dir():
        error = "binary is missing/not executable, or process cwd does not exist"
        environment.update(preflight_status="unsupported", error=error)
        (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
        (output / "summary.json").write_text(json.dumps({"schema_version": 1, "status": "unsupported",
            "validation_stage": "C1", "validation_kind": "host_pre_device_rejection",
            "rejection_matrix_complete": False, "executed_processes": 0, "device_execution": False,
            "error": error}, indent=2) + "\n")
        print(error, file=sys.stderr)
        return 2
    environment.update(preflight_status="pass", binary_sha256=sha256_file(binary))
    (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    identity = {"source_commit": environment["source"]["commit"],
                "source_tree_sha256": environment["source"]["tree_sha256"],
                "source_dirty": environment["source"]["dirty"], "binary_sha256": environment["binary_sha256"]}
    records = []
    with (output / "rejections.jsonl").open("w") as handle:
        for case in required_cases():
            record = run_case(case, binary, cwd, output, args.timeout, os.environ.copy(), identity)
            handle.write(json.dumps(record, allow_nan=False) + "\n")
            handle.flush()
            records.append(record)
            print(f"{case.case_id}: {record['status']}", flush=True)
    unchanged = (source_identity(source)["tree_sha256"] == environment["source"]["tree_sha256"]
                 and sha256_file(binary) == environment["binary_sha256"])
    passed = unchanged and all(record["status"] == "pass" for record in records)
    summary = {"schema_version": 1, "recorded_at_utc": utc_now(), "identity": identity,
               "status": "pass" if passed else "fail", "validation_stage": "C1",
               "validation_kind": "host_pre_device_rejection", "rejection_matrix_complete": passed,
               "executed_processes": len(records), "counts": dict(Counter(r["status"] for r in records)),
               "sources_and_binary_unchanged": unchanged, "device_execution": False,
               "simulator_required": False, "hardware": "none",
               "limitation": "host rejection paths only; no evidence of JIT or device execution"}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
