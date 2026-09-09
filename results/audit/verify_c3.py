#!/usr/bin/env python3
"""Read-only C3 artifact audit, independent of project C++ and runner imports.

Uses exact rational BF16 arithmetic and enumerates physical faces to decode
output. Does not run Metal, execute binaries, or write evidence. --self-test
uses explicitly synthetic in-memory fixtures, never project result artifacts.
"""
import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

METAL = "89e1256c982a5b4739d173bcc446c8c748a44b40"
ASSETS = {
    "libttsim_wh_aarch64.so": "d38142c9d94526a5d6b48e5c0711efd718ffaed9270150c5556da0de788214ae",
    "libttsim_wh.so": "2686ebc212ea9753c0aecde74e640aff43efbe54db0322676627c3a0c1b14d81",
}
CASES = [
    (1, 1, "zero", 101), (1, 31, "one_hot", 102), (1, 32, "ones", 103),
    (1, 33, "decimals", 104), (31, 31, "cancellation", 105),
    (32, 32, "small_integers", 106), (32, 33, "ones", 107),
    (33, 32, "one_hot", 108), (33, 33, "small_integers", 109),
    (65, 97, "random", 110), (97, 65, "decimals", 111),
    (128, 128, "random", 112),
]
EXACT = {"zero", "one_hot", "ones", "cancellation", "small_integers"}
PREFIX = "TT_ROW_REDUCE_RESULT="


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, "duplicate JSON key: " + key)
        obj[key] = value
    return obj


def parse(text):
    def nonfinite(token):
        raise ValueError("non-standard JSON number: " + token)
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=nonfinite)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def check_hash(value, length, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value),
            label + " has invalid format")


def local_file(root, name):
    require(isinstance(name, str), "artifact path is not text")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts, "artifact escapes result directory")
    result = (root / name).resolve()
    require(root.resolve() in result.parents, "artifact symlink escapes result directory")
    require(result.is_file(), "missing artifact: " + name)
    return result


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value), label + " is not finite numeric data")
    return float(value)


def vector(value, rows, label):
    require(isinstance(value, list) and len(value) == rows, label + " length mismatch")
    return [finite(item, label) for item in value]


def near(a, b, label):
    require(math.isclose(finite(a, label), float(b), rel_tol=2e-14, abs_tol=1e-18),
            label + " differs from independently calculated value")


def words(encoded, count, label):
    require(isinstance(encoded, str) and re.fullmatch(r"[0-9a-fA-F]*", encoded), label + " is not hex")
    require(len(encoded) == count * 4, label + " size mismatch")
    raw = bytes.fromhex(encoded)
    return [int.from_bytes(raw[n:n + 2], "little") for n in range(0, len(raw), 2)], raw


def bf16(word):
    exponent, fraction = (word >> 7) & 255, word & 127
    require(exponent != 255, "BF16 NaN or infinity found")
    significand = fraction if exponent == 0 else 128 + fraction
    power = -133 if exponent == 0 else exponent - 134
    value = Fraction(significand) * (Fraction(2) ** power)
    return -value if word & 32768 else value


def output_matrix(bits, padded_rows):
    # Enumerate serialized tile/face order, instead of reproducing the host's
    # logical-coordinate -> physical-index formula.
    result = [[None] * 32 for _ in range(padded_rows)]
    cursor = iter(bits)
    for top in range(0, padded_rows, 32):
        for face_top, face_left in ((0, 0), (0, 16), (16, 0), (16, 16)):
            for row in range(top + face_top, top + face_top + 16):
                for col in range(face_left, face_left + 16):
                    result[row][col] = bf16(next(cursor))
    require(next(cursor, None) is None, "extra output words")
    return result


def check_pattern(bits, rows, cols, pattern):
    require(pattern in EXACT | {"decimals", "random"}, "unknown pattern")
    if pattern == "random":
        return  # Deliberately independent of the project's MT19937 generator.
    for row in range(rows):
        for col in range(cols):
            if pattern == "decimals":
                expected = (0x3dcd, 0xbe4d, 0x3e9a)[(row + col) % 3]
            elif pattern == "ones":
                expected = 0x3f80
            elif pattern == "one_hot":
                expected = 0x3f80 if (row, col) == (rows - 1, cols - 1) else 0
            elif pattern == "small_integers":
                expected = 0x3f80 if col < min(row % 17 + 1, cols) else 0
            elif pattern == "cancellation":
                expected = 0 if cols % 2 and col == cols - 1 else (0x3f80 if col % 2 == 0 else 0xbf80)
            else:
                expected = 0
            require(bits[row * cols + col] == expected, "input pattern word mismatch")


def audit_host(host, rows, cols, pattern, seed, repeat, index):
    checks = {
        "rows": rows, "cols": cols, "pattern": pattern, "seed": seed,
        "repeat_count": repeat, "repeat_index": index, "nodes": 1,
        "padded_rows": ((rows - 1) // 32 + 1) * 32,
        "padded_cols": ((cols - 1) // 32 + 1) * 32,
        "status": "pass", "validation_stage": "C3", "backend": "ttsim",
        "upstream_commit": METAL, "padding_zero": True,
        "exact": pattern in EXACT,
        "input_layout_verified_against_upstream": True,
        "input_quantization_verified_against_upstream": True,
        "quantized_bits_encoding": "logical_row_major_bf16_little_endian",
        "output_bits_encoding": "tile_face_bf16_little_endian",
    }
    for key, expected in checks.items():
        require(type(host.get(key)) is type(expected) and host[key] == expected, "host field mismatch: " + key)
    check_hash(host.get("compiled_source_commit"), 40, "compiled source commit")
    bits, input_raw = words(host.get("quantized_bits_hex"), rows * cols, "input")
    inputs = [bf16(word) for word in bits]
    require(all(q == 0 or Fraction(1, 256) <= abs(q) <= 1 for q in inputs), "BF16 input outside numeric contract")
    check_pattern(bits, rows, cols, pattern)
    outbits, output_raw = words(host.get("output_tiled_bits_hex"), checks["padded_rows"] * 32, "output")
    matrix = output_matrix(outbits, checks["padded_rows"])
    for row in range(len(matrix)):
        for col in range(32):
            if row >= rows or col != 0:
                require(matrix[row][col] == 0, "output padding nonzero at %d,%d" % (row, col))
    actual = vector(host.get("actual"), rows, "host actual")
    refs = vector(host.get("reference"), rows, "host reference")
    reported_errors = vector(host.get("abs_errors"), rows, "host errors")
    reported_limits = vector(host.get("tolerances"), rows, "host tolerance")
    gamma = Fraction(cols + 64, 2**24 - cols - 64)
    sums, magnitudes, errors, limits = [], [], [], []
    for row in range(rows):
        logical = inputs[row * cols:(row + 1) * cols]
        exact_sum = sum(logical, Fraction(0))
        magnitude = sum(map(abs, logical), Fraction(0))
        observed = matrix[row][0]
        require(Fraction(actual[row]) == observed, "reported actual does not match BF16 face readback")
        require(Fraction(refs[row]) == exact_sum, "host oracle differs from exact BF16 rational sum")
        error = abs(observed - exact_sum)
        budget = Fraction(0) if pattern in EXACT else (
            abs(exact_sum) / 256 + Fraction(257, 256) * gamma * magnitude + Fraction(1, 2**20))
        require(error <= budget, "numeric failure at row %d: error=%s budget=%s" % (row, error, budget))
        near(reported_errors[row], error, "row error")
        near(reported_limits[row], budget, "row budget")
        sums.append(exact_sum); magnitudes.append(magnitude); errors.append(error); limits.append(budget)
    max_error = max(errors)
    max_scaled = max(e / max(a, Fraction(1, 2**20)) for e, a in zip(errors, magnitudes))
    near(host.get("max_abs_error"), max_error, "max error")
    near(host.get("max_scaled_error"), max_scaled, "max scaled error")
    require(host.get("failed_rows") == [], "host reports failed rows")
    require(finite(host.get("execute_wall_seconds"), "host wall time") >= 0, "negative host time")
    return {
        "input_sha256": digest(input_raw), "output_sha256": digest(output_raw),
        "actual": actual, "reference": sums, "abs_sums": magnitudes,
        "abs_errors": errors, "tolerances": limits, "max_abs_error": max_error,
        "max_scaled_error": max_scaled, "input_words": bits,
    }


def source_bytes(source, project, compiled_commits):
    manifest = source.get("files")
    require(isinstance(manifest, dict) and len(manifest) >= 6, "missing source manifest")
    for required in ("CMakeLists.txt", "src/metal_main.cpp", "include/row_reduce/row_reduce.hpp",
                     "kernels/reader.cpp", "kernels/compute.cpp", "kernels/writer.cpp"):
        require(required in manifest, "source manifest missing " + required)
    check_hash(source.get("commit"), 40, "recorded source commit")
    calculated = digest(json.dumps(manifest, sort_keys=True).encode())
    require(calculated == source.get("tree_sha256"), "source manifest aggregate hash mismatch")
    verified = 0
    commits = [source["commit"], *sorted(compiled_commits)]
    for name, wanted in manifest.items():
        check_hash(wanted, 64, "source file hash")
        path = PurePosixPath(name)
        require(not path.is_absolute() and ".." not in path.parts, "source path escapes repository")
        candidate = (project / name).resolve()
        available = []
        if project.resolve() in candidate.parents and candidate.is_file():
            available.append(candidate.read_bytes())
        for commit in commits:
            result = subprocess.run(["git", "-C", str(project), "show", commit + ":" + name], capture_output=True)
            if result.returncode == 0:
                available.append(result.stdout)
        require(any(digest(raw) == wanted for raw in available), "source bytes unavailable or changed: " + name)
        verified += 1
    return verified


def audit(directory, project, binary=None):
    environment = parse(local_file(directory, "environment.json").read_text())
    summary = parse(local_file(directory, "summary.json").read_text())
    records = [parse(line) for line in local_file(directory, "simulator.jsonl").read_text().splitlines() if line.strip()]
    require(environment.get("preflight_status") == "pass", "environment preflight not passed")
    require(environment.get("os") == "Linux" and environment.get("backend") == "ttsim", "wrong execution environment")
    require(environment.get("upstream_commit") == METAL, "wrong upstream commit")
    asset = environment.get("simulator_asset")
    require(asset in ASSETS and environment.get("simulator_sha256") == ASSETS[asset], "wrong simulator asset/hash")
    require(environment.get("machine") == ("aarch64" if "aarch64" in asset else "x86_64"), "simulator architecture mismatch")
    require(environment.get("simulator_version") == "v1.10.6", "wrong simulator version")
    require(environment.get("env", {}).get("TT_METAL_SLOW_DISPATCH_MODE") == "1", "missing slow dispatch")
    require(environment.get("env", {}).get("TT_METAL_DISABLE_SFPLOADMACRO") == "1", "missing simulator workaround")
    check_hash(environment.get("binary_sha256"), 64, "binary hash")
    check_hash(environment.get("soc_descriptor_sha256"), 64, "descriptor hash")
    if binary:
        require(digest(binary.read_bytes()) == environment["binary_sha256"], "provided executable hash mismatch")
    require(len(records) == 13, "full evidence requires 13 subprocess records")
    expected = {"m%d_n%d_%s" % (m, n, p): (m, n, p, s, 1) for m, n, p, s in CASES}
    expected["m33_n33_repeat"] = (33, 33, "random", 20260909, 2)
    identity = {key: environment[key] for key in ("upstream_commit", "binary_sha256", "simulator_asset",
        "simulator_version", "simulator_sha256", "soc_descriptor_sha256")}
    identity.update(source_commit=environment["source"]["commit"], source_dirty=environment["source"]["dirty"],
                    source_tree_sha256=environment["source"]["tree_sha256"])
    compiled = set(); seen = set(); log_paths = set(); total_rows = 0; executions = 0
    elapsed = []
    for record in records:
        case = record["case"]; name = case.get("case_id")
        require(name in expected and name not in seen, "missing, unexpected, or duplicate case")
        seen.add(name)
        m, n, pattern, seed, count = expected[name]
        require(case == dict(case_id=name, rows=m, cols=n, pattern=pattern, seed=seed, repeat=count), "case definition mismatch")
        command = ["<binary>", "--rows", str(m), "--cols", str(n), "--pattern", pattern,
                   "--seed", str(seed), "--nodes", "1", "--repeat", str(count)]
        if environment.get("env", {}).get("TT_METAL_DEVICE_PROFILER") == "1":
            command.append("--profile")
        require(record.get("command") == command, "recorded invocation differs from requested case")
        require(record.get("identity") == identity, "per-case source/binary/simulator identity mismatch")
        require(record.get("status") == "pass" and record.get("exit_code") == 0 and record.get("timed_out") is False,
                "case did not complete successfully: " + name)
        elapsed.append(finite(record.get("wall_seconds"), "subprocess wall time"))
        require(elapsed[-1] > 0, "nonpositive subprocess wall time")
        require(record.get("backend") == "ttsim" and record.get("nodes") == 1, "wrong backend/node count")
        logs = {}
        for channel in ("stdout", "stderr"):
            path = local_file(directory, record[channel])
            require(path not in log_paths, "same log is reused by different cases/channels")
            log_paths.add(path)
            raw = path.read_bytes()
            require(record.get("log_sha256", {}).get(path.name) == digest(raw), "raw log hash mismatch: " + path.name)
            logs[channel] = raw.decode("utf-8", errors="strict")
        require("TT_ROW_REDUCE_ERROR=" not in logs["stdout"] + logs["stderr"], "host error marker in successful case")
        hosts = [parse(line[len(PREFIX):]) for line in logs["stdout"].splitlines() if line.startswith(PREFIX)]
        require(len(hosts) == count == len(record.get("executions", [])), "execution count differs from raw stdout")
        previous = None
        process_commits = set()
        for index, (host, reported) in enumerate(zip(hosts, record["executions"])):
            if previous is not None:
                pattern = "zero" if all(word == 0x3f80 for word in previous["input_words"]) else "ones"
            independent = audit_host(host, m, n, pattern, seed, count, index)
            commit = host["compiled_source_commit"]
            process_commits.add(commit); compiled.add(commit)
            require(reported.get("compiled_source_commit") == commit, "normalized compiled commit mismatch")
            for key in ("input_sha256", "output_sha256"):
                require(reported.get(key) == independent[key], "normalized hash mismatch: " + key)
            for key in ("actual", "reference", "abs_sums", "abs_errors", "tolerances"):
                values = vector(reported.get(key), m, "normalized " + key)
                for a, b in zip(values, independent[key]): near(a, b, "normalized " + key)
            for key in ("max_abs_error", "max_scaled_error"):
                near(reported.get(key), independent[key], "normalized " + key)
            require(reported.get("status") == "pass" and reported.get("failed_rows") == [] and reported.get("padding_zero") is True,
                    "normalized execution not passing")
            require(reported.get("repeat_index") == index and reported.get("pattern") == pattern and reported.get("seed") == seed,
                    "normalized execution metadata mismatch")
            require(reported.get("exact") is (pattern in EXACT), "normalized exact flag mismatch")
            if previous is not None:
                require(previous["input_words"] != independent["input_words"], "repeat did not change raw input")
            previous = independent; total_rows += m; executions += 1
        require(process_commits == {record.get("compiled_source_commit")}, "process compiled identity differs")
    require(seen == set(expected), "required cases missing")
    require(len(compiled) == 1, "matrix combines different compiled executables")
    for key, value in {"status": "pass", "matrix_complete": True, "sources_and_binary_unchanged": True,
                       "validation_stage": "C3", "executed_processes": 13, "validated_executions": 14,
                       "same_process_repeat": True, "backend": "ttsim"}.items():
        require(type(summary.get(key)) is type(value) and summary[key] == value, "summary mismatch: " + key)
    require(summary.get("identity") == identity and summary.get("counts") == {"pass": 13}, "summary identity/count mismatch")
    require(summary.get("required_shapes") == [[m, n] for m, n, _, _ in CASES], "summary shape list mismatch")
    near(summary.get("total_process_wall_seconds"), math.fsum(elapsed), "summary total wall time")
    files_verified = source_bytes(environment["source"], project, compiled)
    return {"status": "pass", "artifact_audit": "independent_exact_rational_bf16",
            "processes": 13, "executions": executions, "rows_checked": total_rows,
            "raw_logs_hashed": len(log_paths), "source_files_hash_verified": files_verified,
            "recorded_source_commit": environment["source"]["commit"],
            "compiled_source_commit": next(iter(compiled)), "binary_sha256": environment["binary_sha256"],
            "actual_binary_bytes_checked": binary is not None,
            "limitations": [
                "Audit validates retained artifacts; it does not independently observe simulator execution.",
                "Random-case mathematics is checked from raw BF16 inputs; MT19937 seed-to-byte generation is not regenerated.",
                "Raw input DRAM/tiled upload bytes are not retained, so input padding/upload layout is only host-attested.",
                "Simulator and descriptor digests are checked for consistency; their original binaries are not reopened.",
                "Compiled commit is an embedded label; binary-to-source provenance is not independently rebuilt.",
            ]}


def self_test():
    require(bf16(0x3f80) == 1 and bf16(0xbf80) == -1 and bf16(0x3dcd) == Fraction(205, 2048), "BF16 known answer")
    require(bf16(1) == Fraction(1, 2**133), "BF16 subnormal known answer")
    def rejected(callback):
        try: callback()
        except (ValueError, StopIteration): return
        raise AssertionError("synthetic defect was accepted")
    rejected(lambda: bf16(0x7fc1))
    rejected(lambda: parse('{"x":1,"x":2}'))
    rejected(lambda: parse('{"x":NaN}'))
    # Independent sparse serialized fixture: first column of each face pair.
    tile = [0] * 1024
    for row in range(16):
        tile[row * 16] = 0x3f80
        tile[512 + row * 16] = 0x4000
    decoded = output_matrix(tile, 32)
    require(all(decoded[r][0] == (1 if r < 16 else 2) for r in range(32)), "face halves swapped")
    require(all(v == 0 for row in decoded for v in row[1:]), "face padding decode")
    require(decoded[1][0] != bf16(tile[1]), "front-M extraction defect not detected")
    rejected(lambda: check_pattern([0x3f80, 0], 1, 2, "ones"))
    check_pattern([0x3dcd, 0xbe4d, 0x3e9a], 1, 3, "decimals")
    host = {
        "rows": 1, "cols": 1, "pattern": "ones", "seed": 1, "repeat_count": 1,
        "repeat_index": 0, "nodes": 1, "padded_rows": 32, "padded_cols": 32,
        "status": "pass", "validation_stage": "C3", "backend": "ttsim", "upstream_commit": METAL,
        "padding_zero": True, "exact": True, "input_layout_verified_against_upstream": True,
        "input_quantization_verified_against_upstream": True,
        "quantized_bits_encoding": "logical_row_major_bf16_little_endian",
        "output_bits_encoding": "tile_face_bf16_little_endian", "compiled_source_commit": "a" * 40,
        "quantized_bits_hex": "803f", "output_tiled_bits_hex": "803f" + "0000" * 1023,
        "actual": [1], "reference": [1], "abs_errors": [0], "tolerances": [0],
        "max_abs_error": 0, "max_scaled_error": 0, "failed_rows": [], "execute_wall_seconds": 1,
    }
    audit_host(host, 1, 1, "ones", 1, 1, 0)
    for key, value in (
        ("output_tiled_bits_hex", "0040" + "0000" * 1023),
        ("output_tiled_bits_hex", "803f803f" + "0000" * 1022),
        ("output_tiled_bits_hex", "c17f" + "0000" * 1023),
        ("reference", [0]), ("tolerances", [1]), ("actual", [float("nan")]),
    ):
        bad = {**host, key: value}
        rejected(lambda: audit_host(bad, 1, 1, "ones", 1, 1, 0))
    exact_sum, magnitude, error = Fraction(411, 2048), Fraction(1231, 2048), Fraction(1, 2048)
    budget = exact_sum / 256 + Fraction(257, 256) * Fraction(67, 2**24 - 67) * magnitude + Fraction(1, 2**20)
    host.update(cols=3, pattern="decimals", exact=False, quantized_bits_hex="cd3d4dbe9a3e",
                output_tiled_bits_hex="4d3e" + "0000" * 1023, actual=[float(Fraction(410, 2048))],
                reference=[float(exact_sum)], abs_errors=[float(error)], tolerances=[float(budget)],
                max_abs_error=float(error), max_scaled_error=float(error / magnitude))
    audit_host(host, 1, 3, "decimals", 1, 1, 0)
    host.update(output_tiled_bits_hex="503e" + "0000" * 1023, actual=[0.203125])
    rejected(lambda: audit_host(host, 1, 3, "decimals", 1, 1, 0))
    print("PASS independent checker self-tests (synthetic in-memory fixtures; no C3 evidence created).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resultsdir", type=Path, nargs="?")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--binary", type=Path, help="optional retained executable to hash; never executed")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if args.resultsdir is None:
        parser.error("resultsdir is required unless --self-test is selected")
    try:
        result = audit(args.resultsdir.resolve(), args.source_root.resolve(), args.binary)
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as error:
        print(json.dumps({"status": "fail", "error": str(error)})); return 1
    print(json.dumps(result, indent=2, allow_nan=False)); return 0


if __name__ == "__main__":
    sys.exit(main())
