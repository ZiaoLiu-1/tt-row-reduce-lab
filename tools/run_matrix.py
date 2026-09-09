#!/usr/bin/env python3
"""Serial, subprocess-isolated ttsim evidence runner; no CPU execution fallback."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import struct
import subprocess
import sys
import time

METAL_COMMIT = "89e1256c982a5b4739d173bcc446c8c748a44b40"
SIMULATOR_VERSION = "v1.10.6"
SIMULATOR_DIGESTS = {
    "libttsim_wh.so": "2686ebc212ea9753c0aecde74e640aff43efbe54db0322676627c3a0c1b14d81",
    "libttsim_wh_aarch64.so": "d38142c9d94526a5d6b48e5c0711efd718ffaed9270150c5556da0de788214ae",
}
RESULT_PREFIX = "TT_ROW_REDUCE_RESULT="
EXACT_PATTERNS = {"zero", "one_hot", "ones", "cancellation", "small_integers"}
PATTERNS = EXACT_PATTERNS | {"decimals", "random"}


@dataclass(frozen=True)
class Case:
    case_id: str
    rows: int
    cols: int
    pattern: str
    seed: int = 20260909
    repeat: int = 1


MATRIX = (
    Case("m1_n1_zero", 1, 1, "zero", 101),
    Case("m1_n31_one_hot", 1, 31, "one_hot", 102),
    Case("m1_n32_ones", 1, 32, "ones", 103),
    Case("m1_n33_decimals", 1, 33, "decimals", 104),
    Case("m31_n31_cancellation", 31, 31, "cancellation", 105),
    Case("m32_n32_small_integers", 32, 32, "small_integers", 106),
    Case("m32_n33_ones", 32, 33, "ones", 107),
    Case("m33_n32_one_hot", 33, 32, "one_hot", 108),
    Case("m33_n33_small_integers", 33, 33, "small_integers", 109),
    Case("m65_n97_random", 65, 97, "random", 110),
    Case("m97_n65_decimals", 97, 65, "decimals", 111),
    Case("m128_n128_random", 128, 128, "random", 112),
)
REPEAT_CASE = Case("m33_n33_repeat", 33, 33, "random", repeat=2)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def source_identity(root: Path) -> dict:
    """Bind executed source, including uncommitted edits, without collecting secrets."""
    names = ["CMakeLists.txt", "upstream.lock", "NOTICE", "LICENSE"]
    files = [root / name for name in names if (root / name).is_file()]
    for name in ("src", "include", "kernels", "tools", "cmake", "tests"):
        files.extend(p for p in (root / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    manifest = {str(p.relative_to(root)): sha256_file(p) for p in sorted(set(files))}
    tree_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    try:
        commit = git_value(root, "rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = None
    try:
        dirty = bool(git_value(root, "status", "--porcelain", "--untracked-files=normal"))
    except (subprocess.CalledProcessError, FileNotFoundError):
        dirty = None
    return {"commit": commit, "dirty": dirty, "tree_sha256": tree_hash, "files": manifest}


def finite_vector(value: object, count: int, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{name} must contain {count} values")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in value):
        raise ValueError(f"{name} contains a non-finite or non-numeric value")
    return [float(x) for x in value]


def mt19937(seed: int):
    """C++ std::mt19937's specified 32-bit recurrence and tempering."""
    state = [seed & 0xffffffff]
    for index in range(1, 624):
        state.append((1812433253 * (state[-1] ^ (state[-1] >> 30)) + index) & 0xffffffff)
    while True:
        for index in range(624):
            joined = (state[index] & 0x80000000) | (state[(index + 1) % 624] & 0x7fffffff)
            state[index] = state[(index + 397) % 624] ^ (joined >> 1) ^ (0x9908b0df if joined & 1 else 0)
        for value in state:
            value ^= value >> 11
            value ^= (value << 7) & 0x9d2c5680
            value ^= (value << 15) & 0xefc60000
            yield (value ^ (value >> 18)) & 0xffffffff


def expected_input_bytes(rows: int, cols: int, pattern: str, seed: int) -> bytes:
    generator = mt19937(seed)
    raw = bytearray()
    for row in range(rows):
        for col in range(cols):
            if pattern == "zero":
                value = 0.0
            elif pattern == "one_hot":
                value = float(row == rows - 1 and col == cols - 1)
            elif pattern == "ones":
                value = 1.0
            elif pattern == "cancellation":
                value = 0.0 if cols % 2 and col == cols - 1 else (1.0 if col % 2 == 0 else -1.0)
            elif pattern == "small_integers":
                value = float(col < min(row % 17 + 1, cols))
            elif pattern == "decimals":
                value = (0.1, -0.2, 0.3)[(row + col) % 3]
            elif pattern == "random":
                value = (256 + next(generator) % 65281) / 65536.0
                if next(generator) & 1:
                    value = -value
            else:
                raise ValueError("unknown input pattern")
            fp32 = struct.unpack("<I", struct.pack("<f", value))[0]
            bf16 = ((fp32 + 0x7fff + ((fp32 >> 16) & 1)) >> 16) & 0xffff
            raw.extend(struct.pack("<H", bf16))
    return bytes(raw)


def decode_output_bits(encoded: object, rows: int, actual: list[float]) -> bool:
    padded_rows = 32 * ((rows + 31) // 32)
    if not isinstance(encoded, str) or len(encoded) != 4 * padded_rows * 32:
        raise ValueError("output_tiled_bits_hex must contain full padded output tiles")
    raw = bytes.fromhex(encoded)
    padding_zero = True
    for row in range(padded_rows):
        for col in range(32):
            face = ((row % 32) // 16) * 2 + col // 16
            position = (row // 32) * 1024 + face * 256 + (row % 16) * 16 + col % 16
            bits = raw[position * 2:position * 2 + 2]
            if row < rows and col == 0:
                value = struct.unpack("<f", b"\0\0" + bits)[0]
                if not math.isfinite(value) or value != actual[row]:
                    raise ValueError(f"actual row {row} differs from physical BF16 output readback")
            elif int.from_bytes(bits, "little") & 0x7fff:
                padding_zero = False
    return padding_zero


def validate_result(result: object, case: Case, index: int) -> dict:
    """Recompute the oracle and fixed budget from uploaded logical BF16 bytes."""
    if not isinstance(result, dict):
        raise ValueError("result record must be a JSON object")
    for field, expected in (("rows", case.rows), ("cols", case.cols), ("repeat_index", index)):
        if result.get(field) != expected:
            raise ValueError(f"{field} does not match requested execution")
    pattern = result.get("pattern")
    if pattern not in PATTERNS or (index == 0 and pattern != case.pattern):
        raise ValueError("unexpected input pattern")
    if result.get("seed") != case.seed:
        raise ValueError("seed does not match requested execution")
    if result.get("backend") != "ttsim" or result.get("validation_stage") != "C3":
        raise ValueError("host did not report custom-kernel ttsim execution")
    compiled_commit = result.get("compiled_source_commit")
    if not isinstance(compiled_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", compiled_commit):
        raise ValueError("host binary lacks a valid compiled source commit; configure/build committed source")
    for field, expected in (("nodes", 1), ("repeat_count", case.repeat),
                            ("upstream_commit", METAL_COMMIT),
                            ("quantized_bits_encoding", "logical_row_major_bf16_little_endian"),
                            ("output_bits_encoding", "tile_face_bf16_little_endian"),
                            ("input_layout_verified_against_upstream", True),
                            ("input_quantization_verified_against_upstream", True)):
        if result.get(field) != expected:
            raise ValueError(f"host {field} does not satisfy the execution contract")
    encoded = result.get("quantized_bits_hex")
    if not isinstance(encoded, str) or len(encoded) != 4 * case.rows * case.cols:
        raise ValueError("quantized_bits_hex must encode logical M*N BF16 words")
    raw = bytes.fromhex(encoded)
    if raw != expected_input_bytes(case.rows, case.cols, pattern, case.seed):
        raise ValueError("uploaded BF16 bytes do not match the declared pattern and seed")
    values = [struct.unpack("<f", b"\0\0" + raw[i:i + 2])[0]
              for i in range(0, len(raw), 2)]
    if any(not math.isfinite(x) or (x != 0 and not 2**-8 <= abs(x) <= 1)
           for x in values):
        raise ValueError("uploaded bits violate the finite BF16 input contract")
    actual = finite_vector(result.get("actual"), case.rows, "actual")
    padding_zero = decode_output_bits(result.get("output_tiled_bits_hex"), case.rows, actual)
    host_reference = finite_vector(result.get("reference"), case.rows, "reference")
    exact = pattern in EXACT_PATTERNS
    if result.get("exact") is not exact:
        raise ValueError("host exact flag disagrees with the input-pattern contract")
    reference, abs_sums, tolerances, errors, failed_rows = [], [], [], [], []
    gamma = ((case.cols + 64) * 2**-24) / (1 - (case.cols + 64) * 2**-24)
    for row in range(case.rows):
        inputs = values[row * case.cols:(row + 1) * case.cols]
        expected = math.fsum(inputs)
        absolute_sum = math.fsum(abs(x) for x in inputs)
        budget = 0.0 if exact else 2**-8 * abs(expected) + (1 + 2**-8) * gamma * absolute_sum + 2**-20
        if host_reference[row] != expected:
            raise ValueError(f"host reference differs from BF16 oracle at row {row}")
        error = abs(actual[row] - expected)
        reference.append(expected)
        abs_sums.append(absolute_sum)
        tolerances.append(budget)
        errors.append(error)
        if error > budget:
            failed_rows.append(row)
    host_status = result.get("status")
    if host_status not in {"pass", "numerical-fail"}:
        raise ValueError("unexpected host result status")
    return {
        "repeat_index": index, "pattern": pattern, "seed": case.seed,
        "compiled_source_commit": compiled_commit,
        "input_sha256": hashlib.sha256(raw).hexdigest(), "input_hash_encoding": "logical BF16 little-endian bytes",
        "actual": actual, "reference": reference, "abs_sums": abs_sums,
        "abs_errors": errors, "tolerances": tolerances, "exact": exact,
        "max_abs_error": max(errors),
        "max_scaled_error": max(e / max(a, 2**-20) for e, a in zip(errors, abs_sums)),
        "failed_rows": failed_rows,
        "output_sha256": hashlib.sha256(bytes.fromhex(result["output_tiled_bits_hex"])).hexdigest(),
        "padding_zero": padding_zero,
        "status": "numerical-fail" if failed_rows or host_status != "pass" or not padding_zero or result.get("padding_zero") is not True else "pass",
    }


def run_subprocess(command: list[str], cwd: Path, env: dict, timeout: float,
                   stdout_path: Path, stderr_path: Path) -> tuple[int | None, bool, float]:
    start = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                returncode = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                returncode = process.wait()
    return returncode, timed_out, time.monotonic() - start


def run_case(case: Case, binary: Path, metal_home: Path, env: dict,
             timeout: float, output: Path, identity: dict) -> dict:
    stdout = output / "raw" / f"{case.case_id}.stdout.log"
    stderr = output / "raw" / f"{case.case_id}.stderr.log"
    options = ["--rows", str(case.rows), "--cols", str(case.cols), "--pattern", case.pattern,
               "--seed", str(case.seed), "--nodes", "1", "--repeat", str(case.repeat)]
    if env.get("TT_METAL_DEVICE_PROFILER") == "1":
        options.append("--profile")
    record = {"schema_version": 1, "case": asdict(case), "started_at_utc": utc_now(),
              "identity": identity, "command": ["<binary>", *options],
              "measurement_kind": "simulator_process_wall_time", "hardware": "none",
              "nodes": 1, "backend": "ttsim", "stdout": str(stdout.relative_to(output)),
              "stderr": str(stderr.relative_to(output)), "executions": []}
    try:
        code, timed_out, elapsed = run_subprocess([str(binary), *options], metal_home, env,
                                                 timeout, stdout, stderr)
        record.update(exit_code=code, timed_out=timed_out, wall_seconds=elapsed)
        if timed_out:
            record["status"] = "timeout"
            return record
        if code == 77:
            record["status"] = "unsupported"
            return record
        entries = []
        for line in stdout.read_text(errors="replace").splitlines():
            if line.startswith(RESULT_PREFIX):
                entries.append(json.loads(line[len(RESULT_PREFIX):]))
        if len(entries) != case.repeat:
            record["status"] = "crash" if code != 0 else "protocol-error"
            record["error"] = f"expected {case.repeat} result records, received {len(entries)}"
            return record
        record["executions"] = [validate_result(r, case, i) for i, r in enumerate(entries)]
        compiled_commits = {r["compiled_source_commit"] for r in record["executions"]}
        if len(compiled_commits) != 1:
            raise ValueError("one process reported different compiled source commits")
        record["compiled_source_commit"] = next(iter(compiled_commits))
        if case.repeat > 1 and len({r["input_sha256"] for r in record["executions"]}) != case.repeat:
            raise ValueError("repeat case did not upload different inputs")
        if any(r["status"] != "pass" for r in record["executions"]):
            record["status"] = "numerical-fail"
        else:
            record["status"] = "pass" if code == 0 else "crash"
    except (ValueError, TypeError, KeyError, struct.error) as error:
        record.update(status="protocol-error", error=str(error))
    except OSError as error:
        record.update(status="unsupported", error=str(error), exit_code=None,
                      timed_out=False, wall_seconds=None)
    finally:
        record["log_sha256"] = {p.name: sha256_file(p) for p in (stdout, stderr) if p.is_file()}
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--metal-home", type=Path, default=os.environ.get("TT_METAL_HOME"))
    parser.add_argument("--simulator", type=Path, default=os.environ.get("TT_METAL_SIMULATOR"))
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--timeout", type=float, default=300,
                        help="seconds per process; calibrate from the first real smoke (default: 300)")
    parser.add_argument("--case", choices=["all", "smoke", *[c.case_id for c in MATRIX], REPEAT_CASE.case_id],
                        default="all")
    parser.add_argument("--list", action="store_true", help="print the matrix without running anything")
    parser.add_argument("--profile", action="store_true", help="enable device profiler instrumentation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        print(json.dumps([asdict(c) for c in (*MATRIX, REPEAT_CASE)], indent=2))
        return 0
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise SystemExit("--timeout must be finite and positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("environment.json", "simulator.jsonl", "summary.json"):
        if (output / name).exists():
            raise SystemExit(f"refusing to overwrite existing evidence: {output / name}")
    (output / "raw").mkdir(exist_ok=True)
    if any((output / "raw").iterdir()):
        raise SystemExit("use an output directory with no existing raw logs")
    environment = {"schema_version": 1, "recorded_at_utc": utc_now(),
                   "os": platform.system(), "machine": platform.machine(),
                   "python": platform.python_version(), "hardware": "none", "backend": "ttsim",
                   "source": source_identity(args.source_root.resolve()),
                   "timeout_seconds": args.timeout, "simulator_version": SIMULATOR_VERSION,
                   "env": {"TT_METAL_SLOW_DISPATCH_MODE": "1", "TT_METAL_DISABLE_SFPLOADMACRO": "1"}}
    if args.profile:
        environment["env"]["TT_METAL_DEVICE_PROFILER"] = "1"
    try:
        if not args.binary or not args.metal_home or not args.simulator:
            raise ValueError("--binary, --metal-home and --simulator (or TT environment) are required")
        if not os.environ.get("TT_METAL_CACHE") or not Path(os.environ["TT_METAL_CACHE"]).is_absolute():
            raise ValueError("set TT_METAL_CACHE to an absolute isolated project cache before running")
        binary, metal_home, simulator = (p.resolve() for p in (args.binary, args.metal_home, args.simulator))
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("binary is missing or not executable")
        if not simulator.is_file():
            raise ValueError("simulator asset is missing")
        simulator_sha = sha256_file(simulator)
        if simulator.name not in SIMULATOR_DIGESTS or SIMULATOR_DIGESTS[simulator.name] != simulator_sha:
            raise ValueError("simulator filename/SHA-256 does not match the pinned official release")
        upstream_commit = git_value(metal_home, "rev-parse", "HEAD")
        if upstream_commit != METAL_COMMIT:
            raise ValueError("Metal checkout HEAD differs from upstream.lock")
        descriptor = simulator.parent / "soc_descriptor.yaml"
        upstream_descriptor = metal_home / "tt_metal/soc_descriptors/wormhole_b0_80_arch.yaml"
        if not descriptor.is_file() or sha256_file(descriptor) != sha256_file(upstream_descriptor):
            raise ValueError("simulator soc_descriptor.yaml does not match the pinned Metal descriptor")
        if not environment["source"]["commit"]:
            raise ValueError("commit the project source before recording simulator evidence")
        environment.update(upstream_commit=upstream_commit, binary_sha256=sha256_file(binary),
                           simulator_asset=simulator.name, simulator_sha256=simulator_sha,
                           soc_descriptor_sha256=sha256_file(descriptor))
        environment["kernel_cache"] = "caller-selected isolated TT_METAL_CACHE; inherited unchanged"
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        environment.update(preflight_status="unsupported", error=str(error))
        (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
        (output / "summary.json").write_text(json.dumps({"schema_version": 1, "status": "unsupported",
            "matrix_complete": False, "validation_stage": None, "executed_processes": 0,
            "hardware": "none", "error": str(error)}, indent=2) + "\n")
        print(f"unsupported: {error}", file=sys.stderr)
        return 2
    environment["preflight_status"] = "pass"
    (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    env = os.environ.copy()
    env.update(environment["env"], TT_METAL_HOME=str(metal_home), TT_METAL_SIMULATOR=str(simulator))
    identity = {key: environment[key] for key in ("upstream_commit", "binary_sha256", "simulator_asset",
                                                "simulator_version", "simulator_sha256", "soc_descriptor_sha256")}
    identity.update(source_commit=environment["source"]["commit"], source_dirty=environment["source"]["dirty"],
                    source_tree_sha256=environment["source"]["tree_sha256"])
    selected = (*MATRIX, REPEAT_CASE) if args.case == "all" else tuple(
        c for c in (*MATRIX, REPEAT_CASE)
        if c.case_id == ("m32_n32_small_integers" if args.case == "smoke" else args.case))
    records = []
    with (output / "simulator.jsonl").open("w") as handle:
        for case in selected:
            record = run_case(case, binary, metal_home, env, args.timeout, output, identity)
            handle.write(json.dumps(record, allow_nan=False) + "\n")
            handle.flush()
            records.append(record)
            print(f"{case.case_id}: {record['status']} ({record.get('wall_seconds')} process-wall seconds)", flush=True)
    counts = dict(Counter(r["status"] for r in records))
    final_identity = source_identity(args.source_root.resolve())
    sources_unchanged = (final_identity["tree_sha256"] == environment["source"]["tree_sha256"]
                         and sha256_file(binary) == environment["binary_sha256"])
    all_pass = all(r["status"] == "pass" for r in records) and sources_unchanged
    complete = args.case == "all" and all_pass
    summary = {"schema_version": 1, "recorded_at_utc": utc_now(), "identity": identity,
               "status": "pass" if all_pass else "fail", "matrix_complete": complete,
               "sources_and_binary_unchanged": sources_unchanged,
               "validation_stage": "C3" if complete else None,
               "executed_processes": len(records), "validated_executions": sum(
                   len(r["executions"]) for r in records if r["status"] == "pass"),
               "required_shapes": [[c.rows, c.cols] for c in MATRIX], "same_process_repeat": complete,
               "counts": counts, "backend": "ttsim", "hardware": "none",
               "measurement_kind": "simulator_process_wall_time",
               "total_process_wall_seconds": sum(r.get("wall_seconds") or 0 for r in records),
               "timing_scope": "process startup, JIT/cache, simulator execution and teardown; no silicon interpretation"}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
