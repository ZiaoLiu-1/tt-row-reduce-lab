"""Evidence-tool regression tests. Fixtures never count as simulator results."""

import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def import_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = import_tool("run_matrix")
profiler = import_tool("check_profiler")


def fixture(case, actual=None):
    assert case.pattern == "ones"
    actual = actual if actual is not None else [float(case.cols)] * case.rows
    output = bytearray(2 * 32 * (32 * ((case.rows + 31) // 32)))
    for row, value in enumerate(actual):
        position = (row // 32) * 1024 + ((row % 32) // 16) * 512 + (row % 16) * 16
        output[2 * position:2 * position + 2] = struct.pack("<f", value)[2:]
    return {"rows": case.rows, "cols": case.cols, "repeat_index": 0, "repeat_count": case.repeat,
            "pattern": case.pattern, "seed": case.seed, "nodes": 1,
            "backend": "ttsim", "validation_stage": "C3", "upstream_commit": runner.METAL_COMMIT,
            "compiled_source_commit": "a" * 40,
            "input_layout_verified_against_upstream": True,
            "input_quantization_verified_against_upstream": True,
            "quantized_bits_encoding": "logical_row_major_bf16_little_endian",
            "output_bits_encoding": "tile_face_bf16_little_endian",
            "output_tiled_bits_hex": output.hex(),
            "quantized_bits_hex": "803f" * (case.rows * case.cols),
            "actual": actual,
            "reference": [float(case.cols)] * case.rows, "exact": True, "status": "pass",
            "padding_zero": True}


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.case = runner.Case("fixture", 2, 33, "ones")
        self.result = fixture(self.case)

    def test_exact_sum_and_little_endian_hash(self):
        checked = runner.validate_result(self.result, self.case, 0)
        self.assertEqual(checked["status"], "pass")
        self.assertEqual(checked["reference"], [33.0, 33.0])
        self.assertEqual(checked["tolerances"], [0.0, 0.0])
        self.assertEqual(len(checked["input_sha256"]), 64)

    def test_false_pass_and_padding_failure(self):
        self.result = fixture(self.case, [33.0, 32.0])
        checked = runner.validate_result(self.result, self.case, 0)
        self.assertEqual(checked["status"], "numerical-fail")
        self.assertEqual(checked["failed_rows"], [1])
        self.result = fixture(self.case)
        self.result["padding_zero"] = False
        self.assertEqual(runner.validate_result(self.result, self.case, 0)["status"], "numerical-fail")

    def test_physical_padding_and_mislabeled_input(self):
        output = bytearray.fromhex(self.result["output_tiled_bits_hex"])
        output[2:4] = bytes.fromhex("803f")
        self.result["output_tiled_bits_hex"] = output.hex()
        self.assertEqual(runner.validate_result(self.result, self.case, 0)["status"], "numerical-fail")
        self.result = fixture(self.case)
        self.result["quantized_bits_hex"] = "0000" * 66
        with self.assertRaisesRegex(ValueError, "declared pattern"):
            runner.validate_result(self.result, self.case, 0)

    def test_invalid_or_misbound_evidence_is_rejected(self):
        modifications = ({"actual": [float("nan"), 33]}, {"reference": [32, 33]},
                         {"seed": 4}, {"backend": "cpu"}, {"exact": False},
                         {"quantized_bits_hex": "807f" * 66}, {"actual": [33]},
                         {"upstream_commit": "wrong"}, {"repeat_index": 1},
                         {"compiled_source_commit": "unknown"},
                         {"input_layout_verified_against_upstream": False})
        for modification in modifications:
            with self.subTest(modification=modification), self.assertRaises(ValueError):
                runner.validate_result({**self.result, **modification}, self.case, 0)

    def test_general_budget_is_recomputed(self):
        case = runner.Case("fixture", 1, 1, "decimals")
        q = struct.unpack("<f", bytes.fromhex("0000cd3d"))[0]
        result = fixture(runner.Case("fixture", 1, 1, "ones"), [q])
        result.update(pattern="decimals", quantized_bits_hex="cd3d", actual=[q], reference=[q], exact=False)
        checked = runner.validate_result(result, case, 0)
        self.assertEqual(checked["status"], "pass")
        result["actual"][0] = q * 2
        output = bytearray.fromhex(result["output_tiled_bits_hex"])
        output[:2] = struct.pack("<f", q * 2)[2:]
        result["output_tiled_bits_hex"] = output.hex()
        self.assertEqual(runner.validate_result(result, case, 0)["status"], "numerical-fail")

    def test_mt19937_published_default_seed_sequence(self):
        generator = runner.mt19937(5489)
        self.assertEqual([next(generator) for _ in range(5)],
                         [3499211612, 581869302, 3890346734, 3586334585, 545404204])

    def test_matrix_shapes_are_complete(self):
        self.assertEqual([(c.rows, c.cols) for c in runner.MATRIX],
                         [(1, 1), (1, 31), (1, 32), (1, 33), (31, 31), (32, 32),
                          (32, 33), (33, 32), (33, 33), (65, 97), (97, 65), (128, 128)])
        self.assertEqual(runner.REPEAT_CASE.repeat, 2)


class ProcessTests(unittest.TestCase):
    def run_fixture(self, code, case=None, timeout=2):
        case = case or runner.Case("fixture", 1, 1, "ones")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "fixture.py"
            binary.write_text(f"#!{sys.executable}\n" + code)
            binary.chmod(0o700)
            (root / "raw").mkdir()
            return runner.run_case(case, binary, root, {}, timeout, root, {"test_fixture": True})

    def test_process_pass_and_raw_logs(self):
        result = fixture(runner.Case("fixture", 1, 1, "ones"))
        record = self.run_fixture("print(" + repr(runner.RESULT_PREFIX + json.dumps(result)) + ")\n")
        self.assertEqual(record["status"], "pass")
        self.assertGreater(record["wall_seconds"], 0)
        self.assertEqual(len(record["log_sha256"]), 2)

    def test_timeout_retains_elapsed_time(self):
        record = self.run_fixture("import time\nprint('started', flush=True)\ntime.sleep(30)\n", timeout=0.05)
        self.assertEqual(record["status"], "timeout")
        self.assertTrue(record["timed_out"])
        self.assertGreaterEqual(record["wall_seconds"], 0.05)

    def test_unsupported_crash_and_malformed(self):
        for code, expected in (("raise SystemExit(77)\n", "unsupported"),
                               ("raise SystemExit(3)\n", "crash"),
                               ("print('no result')\n", "protocol-error"),
                               ("print('TT_ROW_REDUCE_RESULT={bad}')\n", "protocol-error")):
            with self.subTest(expected=expected):
                self.assertEqual(self.run_fixture(code)["status"], expected)

    def test_repeated_same_input_is_rejected(self):
        case = runner.Case("fixture", 1, 1, "ones", repeat=2)
        first = fixture(case)
        second = copy.deepcopy(first)
        second["repeat_index"] = 1
        code = "\n".join("print(" + repr(runner.RESULT_PREFIX + json.dumps(r)) + ")"
                         for r in (first, second))
        self.assertEqual(self.run_fixture(code, case)["status"], "protocol-error")


class ProfilerTests(unittest.TestCase):
    # Exact header observed in the real project CSV and verified against the
    # pinned writeCSVHeader function. Event rows below are synthetic fixtures.
    HEADER = "PCIe slot, core_x, core_y, RISC processor type, timer_id, time[cycles since reset], data, run host ID, trace id, trace id counter, zone name, type, source line, source file, meta data"

    @staticmethod
    def csv_text():
        rows = ["ARCH: wormhole_b0, CHIP_FREQ[MHz]: 0, Max Compute Cores: 80", ProfilerTests.HEADER]
        for index, zone in enumerate(profiler.DEFAULT_ZONES):
            rows.extend([f"0,1,1,BRISC,12,{100 + index},0,0,,,{zone},ZONE_START,10,kernel.cpp,",
                         f"0,1,1,BRISC,13,{200 + index},0,0,,,{zone},ZONE_END,10,kernel.cpp,"])
        return "\n".join(rows) + "\n"

    def check(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.csv"
            path.write_text(text)
            return profiler.validate_csv(path)

    def test_real_schema_structure(self):
        result = self.check(self.csv_text())
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["measurement_kind"], "simulator_instrumentation")
        self.assertEqual(len(result["intervals"]), 3)
        self.assertEqual(result["intervals"][0]["run_host_id"], 0)
        self.assertIsNone(result["intervals"][0]["trace_id"])
        self.assertIsNone(result["intervals"][0]["trace_id_counter"])

    def test_exact_actual_header_without_events_fails(self):
        text = "ARCH: wormhole_b0, CHIP_FREQ[MHz]: 0, Max Compute Cores: 80\n" + self.HEADER + "\n"
        with self.assertRaisesRegex(ValueError, "no event rows"):
            self.check(text)

    def test_trace_identity_preserved_and_not_cross_paired(self):
        text = self.csv_text().replace(",0,0,,,", ",0,7,9,2,")
        result = self.check(text)
        interval = result["intervals"][0]
        self.assertEqual((interval["run_host_id"], interval["trace_id"], interval["trace_id_counter"]), (7, 9, 2))
        for unmatched in (text.replace(",0,7,9,2,row_reduce_reader,ZONE_END", ",0,8,9,2,row_reduce_reader,ZONE_END"),
                          text.replace(",0,7,9,2,row_reduce_reader,ZONE_END", ",0,7,10,2,row_reduce_reader,ZONE_END"),
                          text.replace(",0,7,9,2,row_reduce_reader,ZONE_END", ",0,7,9,3,row_reduce_reader,ZONE_END")):
            with self.subTest(unmatched=unmatched), self.assertRaisesRegex(ValueError, "end without a matching begin"):
                self.check(unmatched)

    def test_old_documentation_fields_and_phase_values_rejected(self):
        for old_schema in (self.csv_text().replace("run host ID", "Run ID"),
                           self.csv_text().replace(", type,", ", zone phase,"),
                           self.csv_text().replace(", data,", ", stat value,"),
                           self.csv_text().replace("ZONE_START", "begin"),
                           self.csv_text().replace("ZONE_END", "end")):
            with self.subTest(old_schema=old_schema), self.assertRaises(ValueError):
                self.check(old_schema)

    def test_missing_empty_and_incomplete_fail(self):
        with self.assertRaises(ValueError):
            profiler.validate_csv(Path("/nonexistent-fixture.csv"))
        for text in ("", self.csv_text().replace("row_reduce_compute", "unrelated"),
                     self.csv_text().replace(",ZONE_END,", ",ZONE_START,"),
                     self.csv_text().replace(",200,", ",50,"),
                     self.csv_text().replace(",100,", ",NaN,"),
                     self.csv_text().replace("time[cycles since reset]", "seconds")):
            with self.subTest(text=text[:50]), self.assertRaises(ValueError):
                self.check(text)


if __name__ == "__main__":
    unittest.main()
