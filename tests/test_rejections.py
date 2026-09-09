"""CPU fixtures for the host-rejection recorder; never simulator evidence."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_rejections as tool


class RejectionEvidenceTests(unittest.TestCase):
    @staticmethod
    def rejection(before_device=True):
        return tool.ERROR_PREFIX + json.dumps({"status": "invalid-input", "before_device": before_device,
                                               "message": "invalid host input"})

    def test_pre_device_structured_rejection_passes(self):
        self.assertEqual(tool.classify_rejection(2, False, "", self.rejection())["status"], "pass")

    def test_accidental_initialized_device_is_not_accepted(self):
        for before_device in (False, None, "true", 1):
            with self.subTest(before_device=before_device):
                result = tool.classify_rejection(2, False, "", self.rejection(before_device))
                self.assertEqual(result["status"], "validation-fail")

    def test_exit_code_alone_and_result_lines_cannot_pass(self):
        self.assertEqual(tool.classify_rejection(2, False, "", "invalid shape")["status"], "protocol-error")
        self.assertEqual(tool.classify_rejection(0, False, "", self.rejection())["status"], "validation-fail")
        self.assertEqual(tool.classify_rejection(2, False, tool.RESULT_PREFIX + "{}", self.rejection())["status"],
                         "validation-fail")
        self.assertEqual(tool.classify_rejection(-9, False, "", "")["status"], "crash")
        self.assertEqual(tool.classify_rejection(2, True, "", self.rejection())["status"], "timeout")

    def test_malformed_duplicate_and_runtime_error_rejected(self):
        for stderr in (tool.ERROR_PREFIX + "[]", tool.ERROR_PREFIX + "{bad}",
                       self.rejection() + "\n" + self.rejection(),
                       tool.ERROR_PREFIX + json.dumps({"status": "runtime-error", "before_device": True})):
            with self.subTest(stderr=stderr):
                self.assertNotEqual(tool.classify_rejection(2, False, "", stderr)["status"], "pass")

    def test_serial_subprocess_captures_raw_logs_and_input_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw").mkdir()
            (root / "inputs").mkdir()
            binary = root / "fixture.py"
            binary.write_text(f"#!{sys.executable}\nimport sys\nprint({self.rejection()!r}, file=sys.stderr)\nsys.exit(2)\n")
            binary.chmod(0o700)
            case = tool.RejectionCase("fixture", ("--rows", "1", "--cols", "1"), "nan\n")
            result = tool.run_case(case, binary, root, root, 2, {}, {"unit_test_fixture": True})
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["exit_code"], 2)
            self.assertFalse(result["device_execution"])
            self.assertEqual(len(result["log_sha256"]), 2)
            self.assertEqual((root / result["input_file"]).read_text(), "nan\n")

    def test_required_rejections_cover_contract(self):
        cases = tool.required_cases()
        self.assertEqual(len(cases), 14)
        self.assertEqual(len({case.case_id for case in cases}), 14)
        self.assertEqual({case.input_text for case in cases if case.input_text is not None},
                         {"nan\n", "inf\n", "2\n", "0.001\n", "1\n", "1 0\n", ""})


if __name__ == "__main__":
    unittest.main()
