"""Run the public Make targets from checkouts whose paths contain spaces."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("make"), "requires make")
class MakePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="scan-report-make-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "checkout's work space"
        self.repo.mkdir()
        shutil.copy2(ROOT / "Makefile", self.repo / "Makefile")
        shutil.copytree(ROOT / "scan_report_kit", self.repo / "scan_report_kit",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "examples", self.repo / "examples")
        (self.repo / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "vendor.py", self.repo / "scripts" / "vendor.py")
        # Keep this fixture independent of the suite that invokes make check.
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_smoke.py").write_text(
            "import unittest\nfrom scan_report_kit import report\n"
            "class Smoke(unittest.TestCase):\n"
            "    def test_import(self):\n"
            "        self.assertTrue(callable(report.render_document))\n",
            encoding="utf-8",
        )
        self.outside = self.root / "other directory"
        self.outside.mkdir()
        self.prelude = self.outside / "prelude.mk"
        self.prelude.write_text("CHECK_PRELUDE := loaded\n", encoding="utf-8")

    def make(self, *args, cwd=None):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(self.root))
        # Do not inherit jobserver descriptors or overrides from an outer make.
        env.pop("MAKEFLAGS", None)
        env.pop("MFLAGS", None)
        env.pop("MAKEOVERRIDES", None)
        return subprocess.run(
            [shutil.which("make"), *args, "PYTHON=" + sys.executable],
            cwd=cwd or self.repo, env=env, capture_output=True, text=True,
            timeout=30,
        )

    def assert_succeeded(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_from_checkout_or_an_explicit_makefile(self):
        invocations = (
            (self.repo, ()),
            (self.outside, ("-f", str(self.repo / "Makefile"))),
            (self.outside, ("-f", os.path.relpath(self.repo / "Makefile", self.outside))),
            (self.outside, ("-f", str(self.prelude), "-f", str(self.repo / "Makefile"))),
        )
        for cwd, flags in invocations:
            with self.subTest(cwd=cwd, flags=flags):
                result = self.make(*flags, "check", cwd=cwd)
                self.assert_succeeded(result)
                self.assertIn("Ran 1 test", result.stderr)
                self.assertIn("check ok - both samples render", result.stdout)

    def test_help_demo_and_vendor_accept_paths_with_spaces(self):
        result = self.make("-f", str(self.repo / "Makefile"), "help", cwd=self.outside)
        self.assert_succeeded(result)
        self.assertIn("run offline unit tests", result.stdout)
        demo = self.root / "demo's output space"
        result = self.make("demo", "NO_PDF=1", "DEMO_OUT=" + str(demo))
        self.assert_succeeded(result)
        self.assertTrue((demo / "dashboard.html").is_file())
        self.assertTrue((demo / "report.html").is_file())
        dest = self.root / "consumer's scripts"
        result = self.make("vendor", "DEST=" + str(dest))
        self.assert_succeeded(result)
        checked = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / "vendor.py"), str(dest), "--check"],
            capture_output=True, text=True, timeout=30,
        )
        self.assert_succeeded(checked)


if __name__ == "__main__":
    unittest.main()
