import math
import os
import re
import signal
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scan_report_kit import pdf

COMPLETE_PDF = b"%PDF-1.7\nfixture\n%%EOF\n"


@unittest.skipIf(os.name == "nt", "POSIX executable fixtures")
class PdfRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.html = self.root / "report.html"
        self.html.write_text("<p>test</p>", encoding="utf-8")
        self.out = self.root / "report.pdf"
        env = patch.dict(os.environ, {"CHROME": str(self.bin / "chrome"), "PATH": str(self.bin)})
        env.start()
        self.addCleanup(env.stop)

    def engine(self, name, body):
        exe = self.bin / name
        exe.write_text(
            "#!" + sys.executable + "\n"
            "import os, sys, time\nfrom pathlib import Path\n"
            "out = Path(next((a.split('=', 1)[1] for a in sys.argv[1:] "
            "if a.startswith('--print-to-pdf=')), sys.argv[-1]))\n" + body + "\n",
            encoding="utf-8",
        )
        exe.chmod(0o755)
        return exe

    def assert_clean(self):
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.root.glob(".scan-report-pdf-*")), [])

    def test_timeout_discards_a_nonempty_partial_pdf(self):
        self.engine("chrome", "out.write_bytes(b'%PDF-1.7\\npartial'); time.sleep(30)")
        failures = []
        self.assertIsNone(pdf.to_pdf(self.html, self.out, failures, timeout=0.2))
        self.assertIn("timed out", failures[0][1])
        self.assert_clean()

    def test_fast_success_with_short_timeout_is_not_misclassified(self):
        self.engine("chrome", "out.write_bytes(" + repr(COMPLETE_PDF) + ")")
        failures = []
        self.assertEqual(pdf.to_pdf(self.html, self.out, failures, timeout=0.4), "chrome")
        self.assertEqual(failures, [])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_stop_preserves_a_failure_that_races_with_termination(self):
        proc = Mock(pid=12345, returncode=1)
        proc.poll.return_value = None
        with patch.object(os, "killpg"):
            self.assertEqual(pdf._stop(proc), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_stop_tolerates_a_group_that_can_no_longer_be_signalled(self):
        # macOS killpg fails with EPERM while the group's last members exit.
        for error in (ProcessLookupError, PermissionError):
            with self.subTest(error=error.__name__):
                proc = Mock(pid=12345, returncode=0)
                proc.poll.return_value = 0
                with patch.object(os, "killpg", side_effect=error(1, "gone")):
                    self.assertEqual(pdf._stop(proc), 0)
                proc.send_signal.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_stop_signals_a_running_leader_when_its_group_refuses(self):
        proc = Mock(pid=12345, returncode=-signal.SIGTERM)
        proc.poll.return_value = None
        with patch.object(os, "killpg", side_effect=PermissionError(1, "refused")):
            self.assertIsNone(pdf._stop(proc))
        self.assertEqual([c.args[0] for c in proc.send_signal.call_args_list], [signal.SIGTERM, signal.SIGKILL])
        proc.wait.assert_called_once_with(timeout=10)

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/status").is_file(),
                         "Linux /proc process inspection")
    def test_timeout_stops_child_renderers_for_both_engines(self):
        for name in ("chrome", "weasyprint"):
            with self.subTest(engine=name):
                for old in self.bin.iterdir():
                    old.unlink()
                pidfile = self.root / (name + ".child")
                self.engine(name, "import subprocess\n"
                            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                            "Path(" + repr(str(pidfile)) + ").write_text(str(child.pid))\n"
                            "time.sleep(30)")
                self.assertIsNone(pdf.to_pdf(self.html, self.out, timeout=0.4))
                pid = int(pidfile.read_text())
                status_file = Path('/proc') / str(pid) / 'status'
                # A killed child can remain briefly as a zombie until its
                # system parent reaps it; it must no longer execute.
                deadline = time.monotonic() + 1
                while status_file.exists():
                    try:
                        state = status_file.read_text()
                    except FileNotFoundError:
                        break
                    if re.search(r"State:\s+Z", state):
                        break
                    self.assertLess(time.monotonic(), deadline, "child renderer is still executing")
                    time.sleep(0.01)
                self.assert_clean()

    def test_pause_in_a_write_does_not_publish_truncated_output(self):
        self.engine("chrome", "out.write_bytes(b'%PDF-1.7\\npartial'); time.sleep(1.2)\n"
                    "out.write_bytes(" + repr(COMPLETE_PDF) + "); time.sleep(30)")
        self.assertEqual(pdf.to_pdf(self.html, self.out, timeout=5), "chrome")
        self.assertEqual(self.out.read_bytes(), COMPLETE_PDF)

    def test_zero_exit_with_incomplete_pdf_falls_back(self):
        self.engine("chrome", "out.write_bytes(b'%PDF-1.7\\ntruncated')")
        self.engine("weasyprint", "out.write_bytes(" + repr(COMPLETE_PDF) + ")")
        failures = []
        self.assertEqual(pdf.to_pdf(self.html, self.out, failures), "weasyprint")
        self.assertIn("incomplete", failures[0][1])
        self.assertEqual(self.out.read_bytes(), COMPLETE_PDF)

    def test_weasyprint_rejects_empty_and_non_pdf_outputs(self):
        for content in (b"", b"this is not a PDF", b"%PDF-1.7\nunfinished"):
            with self.subTest(content=content):
                self.engine("weasyprint", "out.write_bytes(" + repr(content) + ")")
                failures = []
                self.assertIsNone(pdf.to_pdf(self.html, self.out, failures))
                self.assertIn("complete PDF", failures[0][1])
                self.assert_clean()

    def test_weasyprint_timeout_discards_partial_output(self):
        self.engine("weasyprint", "out.write_bytes(b'%PDF-1.7\\npartial'); time.sleep(30)")
        failures = []
        self.assertIsNone(pdf.to_pdf(self.html, self.out, failures, timeout=0.2))
        self.assertIn("timed out", failures[0][1])
        self.assert_clean()

    def test_no_engine_removes_stale_pdf(self):
        self.out.write_bytes(COMPLETE_PDF)
        self.assertIsNone(pdf.to_pdf(self.html, self.out))
        self.assert_clean()

    def test_same_path_and_hard_link_do_not_delete_the_input(self):
        before = self.html.read_bytes()
        with self.assertRaisesRegex(ValueError, "different"):
            pdf.to_pdf(self.html, self.html)
        os.link(self.html, self.out)
        with self.assertRaisesRegex(ValueError, "different"):
            pdf.to_pdf(self.html, self.out)
        self.assertEqual(self.html.read_bytes(), before)
        self.assertEqual(self.out.read_bytes(), before)

    def test_missing_html_does_not_delete_existing_output(self):
        self.out.write_bytes(COMPLETE_PDF)
        with self.assertRaises(FileNotFoundError):
            pdf.to_pdf(self.root / "missing.html", self.out)
        self.assertEqual(self.out.read_bytes(), COMPLETE_PDF)

    def test_symlink_and_fifo_output_are_refused(self):
        victim = self.root / "victim"
        victim.write_text("keep", encoding="utf-8")
        self.out.symlink_to(victim)
        with self.assertRaisesRegex(OSError, "refusing"):
            pdf.to_pdf(self.html, self.out)
        self.assertTrue(self.out.is_symlink())
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep")
        self.out.unlink()
        os.mkfifo(self.out)
        with self.assertRaisesRegex(OSError, "refusing"):
            pdf.to_pdf(self.html, self.out)
        self.assertTrue(stat.S_ISFIFO(self.out.lstat().st_mode))

    def test_only_published_pdf_is_visible_and_permissions_are_preserved(self):
        self.out.write_bytes(b"stale")
        self.out.chmod(0o600)
        self.engine("chrome", "assert out != Path(" + repr(str(self.out)) + ")\n"
                    "assert not Path(" + repr(str(self.out)) + ").exists()\n"
                    "out.write_bytes(" + repr(COMPLETE_PDF) + ")")
        self.assertEqual(pdf.to_pdf(self.html, self.out), "chrome")
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".scan-report-pdf-*")), [])

    def test_invalid_timeout_is_rejected_before_removing_old_output(self):
        self.out.write_bytes(COMPLETE_PDF)
        for value in (0, -1, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pdf.to_pdf(self.html, self.out, timeout=value)
            self.assertEqual(self.out.read_bytes(), COMPLETE_PDF)

    def test_redaction_happens_before_diagnostic_truncation(self):
        secret = "A" * 350
        self.engine("chrome", "sys.stderr.write(" + repr("token=" + secret) + "); sys.exit(1)")
        failures = []
        pdf.to_pdf(self.html, self.out, failures, redact=lambda text: re.sub(r"token=\S+", "token=***", text))
        self.assertEqual(failures[0][2], "token=***")
        self.assertNotIn("A" * 20, repr(failures))


if __name__ == "__main__":
    unittest.main()
