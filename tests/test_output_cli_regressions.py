import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scan_report_kit import output
from scan_report_kit.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "report.sample.json"


class OutputRegressionTests(unittest.TestCase):
    def test_private_existing_file_stays_private_without_changing_umask(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            target.write_text("old", encoding="utf-8")
            target.chmod(0o600)
            with patch.object(os, "umask", side_effect=AssertionError("must not change global umask")):
                output.write_output(target, "new")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_new_output_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            output.write_output(target, "private")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_failed_replace_preserves_old_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            target.write_text("old", encoding="utf-8")
            with patch.object(os, "replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
                output.write_output(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(temp).iterdir()), [target])

    def test_encoding_error_does_not_replace_old_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "report.html"
            target.write_text("old", encoding="utf-8")
            with self.assertRaises(UnicodeError):
                output.write_output(target, "\ud800")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(temp).iterdir()), [target])


class OutputSetRegressionTests(unittest.TestCase):
    OLD = {"dashboard.html": b"old dashboard", "report.html": b"old report", "report.pdf": b"old PDF"}
    NEW = {"dashboard.html": "new dashboard", "report.html": "new report"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def old_outputs(self, files=None):
        for name, contents in (self.OLD if files is None else files).items():
            (self.root / name).write_bytes(contents)

    def assert_outputs(self, expected):
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, expected)

    def write(self):
        output.write_outputs(self.root, self.NEW, remove=("report.pdf",))

    def test_success_replaces_the_set_removes_stale_pdf_and_preserves_modes(self):
        self.old_outputs()
        (self.root / "dashboard.html").chmod(0o640)
        (self.root / "report.html").chmod(0o400)
        self.write()
        self.assert_outputs({name: text.encode("utf-8") for name, text in self.NEW.items()})
        self.assertEqual(stat.S_IMODE((self.root / "dashboard.html").stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE((self.root / "report.html").stat().st_mode), 0o400)

    def test_new_output_set_is_private(self):
        self.write()
        for name in self.NEW:
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)
        self.assert_outputs({name: text.encode("utf-8") for name, text in self.NEW.items()})

    def test_staging_encoding_failure_leaves_all_originals_unchanged(self):
        self.old_outputs()
        with self.assertRaises(UnicodeError):
            output.write_outputs(self.root, {"dashboard.html": "new", "report.html": "\ud800"},
                                 remove=("report.pdf",))
        self.assert_outputs(self.OLD)

    def test_second_publication_failure_restores_old_files_and_original_absences(self):
        real_replace = os.replace
        for originals in (self.OLD, {"report.pdf": self.OLD["report.pdf"]}):
            with self.subTest(originals=list(originals)):
                for path in self.root.iterdir():
                    path.unlink()
                self.old_outputs(originals)

                def fail_report(source, destination):
                    if Path(source).parent.name == "new" and Path(destination) == self.root / "report.html":
                        self.assertEqual((self.root / "dashboard.html").read_text(encoding="utf-8"), "new dashboard")
                        raise OSError("publication failed")
                    return real_replace(source, destination)

                with patch.object(output.os, "replace", side_effect=fail_report):
                    with self.assertRaisesRegex(OSError, "publication failed"):
                        self.write()
                self.assert_outputs(originals)

    def test_failure_to_invalidate_pdf_restores_both_html_files(self):
        self.old_outputs()
        real_replace = os.replace

        def fail_pdf_backup(source, destination):
            if Path(source) == self.root / "report.pdf":
                raise OSError("PDF cannot be moved")
            return real_replace(source, destination)

        with patch.object(output.os, "replace", side_effect=fail_pdf_backup):
            with self.assertRaisesRegex(OSError, "PDF cannot be moved"):
                self.write()
        self.assert_outputs(self.OLD)

    def test_failed_rollback_keeps_original_file_at_the_reported_recovery_path(self):
        self.old_outputs()
        real_replace = os.replace

        def fail_publish_and_restore(source, destination):
            source, destination = Path(source), Path(destination)
            if source.parent.name == "new" and destination == self.root / "report.html":
                raise OSError("publication failed")
            if source.parent.name == "previous" and destination == self.root / "dashboard.html":
                raise OSError("restore failed")
            return real_replace(source, destination)

        with patch.object(output.os, "replace", side_effect=fail_publish_and_restore):
            with self.assertRaisesRegex(OSError, "recovery files are preserved at") as caught:
                self.write()
        backups = list(self.root.glob(".scan-report-output-*/previous/dashboard.html"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.OLD["dashboard.html"])
        self.assertIn(str(backups[0].parent.parent), str(caught.exception))
        self.assertIn("restore failed", str(caught.exception))
        self.assertEqual((self.root / "report.html").read_bytes(), self.OLD["report.html"])
        self.assertEqual((self.root / "report.pdf").read_bytes(), self.OLD["report.pdf"])

    def test_interruption_after_a_completed_rename_still_restores_the_original_set(self):
        real_replace = os.replace
        for phase in ("backup", "publication"):
            with self.subTest(phase=phase):
                self.old_outputs()

                def interrupt_after_move(source, destination):
                    result = real_replace(source, destination)
                    if ((phase == "backup" and Path(source) == self.root / "dashboard.html")
                            or (phase == "publication" and Path(source).parent.name == "new"
                                and Path(destination) == self.root / "dashboard.html")):
                        raise KeyboardInterrupt()
                    return result

                with patch.object(output.os, "replace", side_effect=interrupt_after_move):
                    with self.assertRaises(KeyboardInterrupt):
                        self.write()
                self.assert_outputs(self.OLD)

    def test_unsafe_target_is_refused_without_changing_other_outputs(self):
        self.old_outputs()
        outside = self.root / "outside"
        outside.write_bytes(b"keep outside")
        (self.root / "report.html").unlink()
        (self.root / "report.html").symlink_to(outside)
        with self.assertRaisesRegex(OSError, "refusing"):
            self.write()
        self.assertTrue((self.root / "report.html").is_symlink())
        self.assertEqual(outside.read_bytes(), b"keep outside")
        self.assertEqual((self.root / "dashboard.html").read_bytes(), self.OLD["dashboard.html"])
        self.assertEqual((self.root / "report.pdf").read_bytes(), self.OLD["report.pdf"])
        self.assertFalse(list(self.root.glob(".scan-report-output-*")))

    def test_names_cannot_escape_the_directory_or_overlap_removal(self):
        self.old_outputs()
        for texts, remove in (({"../outside": "bad"}, ()), ({"sub/file": "bad"}, ()),
                              ({"sub\\file": "bad"}, ()), ({"report.html": "bad"}, ("report.html",))):
            with self.subTest(texts=texts, remove=remove):
                with self.assertRaises(ValueError):
                    output.write_outputs(self.root, texts, remove=remove)
                self.assert_outputs(self.OLD)


class CliRegressionTests(unittest.TestCase):
    def run_cli(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_invalid_json_preserves_outputs(self, source_text, diagnostic):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"
            source.write_text(source_text, encoding="utf-8")
            out = root / "out"
            out.mkdir()
            previous = {"dashboard.html": b"old dashboard", "report.html": b"old report",
                        "report.pdf": b"old PDF"}
            for name, contents in previous.items():
                (out / name).write_bytes(contents)
            code, stdout, error = self.run_cli([str(source), "--out", str(out), "--no-pdf"])
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn(diagnostic, error)
            self.assertNotIn("Traceback", error)
            self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, previous)

    def test_duplicate_json_keys_cannot_silently_replace_findings_or_severity(self):
        finding = ('{"id":"F-1","title":"High risk","severity":"High",'
                   '"status":"Open"}')
        prefix = ('{"format":"scan-report/1",'
                  '"meta":{"project":"Demo","date":"2026-10-12"},')
        cases = [
            prefix + '"findings":[' + finding + '],"findings":[]}',
            prefix + '"findings":[' + finding + '],"\\u0066indings":[]}',
            prefix + '"findings":[' + finding[:-1] + ',"severity":"Info"}]}',
            prefix + '"findings":[],"extension":{"key":1,"key":2}}',
        ]
        for source in cases:
            with self.subTest(source=source):
                self.assert_invalid_json_preserves_outputs(source, "duplicate JSON object key")

    def test_nonstandard_json_constants_are_rejected_even_in_extensions(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                source = SAMPLE.read_text(encoding="utf-8").rstrip()[:-1] + ',"extension":' + value + '}'
                self.assert_invalid_json_preserves_outputs(source, "invalid JSON constant")

    def test_json_nesting_limit_is_an_input_error(self):
        # Exercise the parser's failure path without relying on a particular
        # interpreter's JSON nesting limit or risking a native stack overflow.
        with patch("scan_report_kit.__main__.json.loads", side_effect=RecursionError("JSON nesting limit")):
            self.assert_invalid_json_preserves_outputs(SAMPLE.read_text(encoding="utf-8"), "JSON nesting limit")

    def test_standard_json_extensions_and_quoted_constants_remain_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.json"
            data = json.loads(SAMPLE.read_text(encoding="utf-8"))
            data["summary"] = "NaN, Infinity and -Infinity are ordinary text when quoted."
            source.write_text(json.dumps(data)[:-1] + ',"extension":'
                              '{"large":1e400,"small":1e-400,"items":[{"key":true},{"key":null}]}}',
                              encoding="utf-8")
            out = root / "out"
            code, _, error = self.run_cli([str(source), "--out", str(out), "--no-pdf"])
            self.assertEqual(code, 0, error)
            page = (out / "dashboard.html").read_text(encoding="utf-8")
            payload = json.loads(page.split('<script id="data" type="application/json">', 1)[1]
                                 .split("</script>", 1)[0])
            self.assertEqual(payload["summary"], data["summary"])
            self.assertNotIn("extension", payload)

    def test_output_cannot_overwrite_input_even_with_no_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "report.pdf"
            source.write_bytes(SAMPLE.read_bytes())
            code, _, error = self.run_cli([str(source), "--out", temp, "--no-pdf"])
            self.assertEqual(code, 2)
            self.assertIn("overwrite input", error)
            self.assertEqual(source.read_bytes(), SAMPLE.read_bytes())

    def test_output_hard_link_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.json"
            source.write_bytes(SAMPLE.read_bytes())
            out = Path(temp) / "out"
            out.mkdir()
            os.link(source, out / "report.html")
            code, _, error = self.run_cli([str(source), "--out", str(out), "--no-pdf"])
            self.assertEqual(code, 2)
            self.assertIn("overwrite input", error)
            self.assertEqual(source.read_bytes(), SAMPLE.read_bytes())

    def test_failed_second_html_staging_preserves_the_entire_old_report(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = {"dashboard.html": b"old dashboard", "report.html": b"old report", "report.pdf": b"old PDF"}
            for name, contents in previous.items():
                (Path(temp) / name).write_bytes(contents)
            real_write = output.write_output
            def fail_document(path, text):
                # Preparing the first page must not have changed either link
                # destination or invalidated the PDF from the old report.
                self.assertEqual({p.name: p.read_bytes() for p in Path(temp).iterdir() if p.is_file()}, previous)
                if Path(path).name == "report.html":
                    raise OSError("full disk")
                real_write(path, text)
            with patch.object(output, "write_output", side_effect=fail_document):
                code, _, error = self.run_cli([str(SAMPLE), "--out", temp])
            self.assertEqual(code, 2)
            self.assertIn("full disk", error)
            self.assertEqual({p.name: p.read_bytes() for p in Path(temp).iterdir()}, previous)

    def test_failed_commit_restores_the_entire_old_report_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            previous = {"dashboard.html": b"old dashboard", "report.html": b"old report", "report.pdf": b"old PDF"}
            for name, contents in previous.items():
                (root / name).write_bytes(contents)
            real_replace = os.replace

            def fail_second_publish(source, destination):
                if Path(source).parent.name == "new" and Path(destination) == root / "report.html":
                    raise OSError("publication failed")
                return real_replace(source, destination)

            with patch.object(output.os, "replace", side_effect=fail_second_publish):
                code, stdout, error = self.run_cli([str(SAMPLE), "--out", temp])
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("publication failed", error)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, previous)

    def test_pdf_io_failure_is_diagnosed_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("scan_report_kit.__main__.pdf.to_pdf", side_effect=PermissionError("permission denied")):
                code, _, error = self.run_cli([str(SAMPLE), "--out", temp])
            self.assertEqual(code, 2)
            self.assertIn("cannot write PDF", error)
            self.assertNotIn("Traceback", error)
            self.assertTrue((Path(temp) / "report.html").is_file())

    def test_missing_engine_reports_exit_three_and_retains_html(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("scan_report_kit.__main__.pdf.to_pdf", return_value=None):
                code, _, error = self.run_cli([str(SAMPLE), "--out", temp])
            self.assertEqual(code, 3)
            self.assertIn("no PDF engine found", error)
            self.assertTrue((Path(temp) / "dashboard.html").is_file())
            self.assertTrue((Path(temp) / "report.html").is_file())


@unittest.skipUnless(shutil.which("make"), "make is not installed")
class MakeCheckRegressionTests(unittest.TestCase):
    def test_failed_temporary_directory_creation_fails_check(self):
        # Use make's -o to skip the test prerequisite and avoid recursive tests.
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, TMPDIR=str(Path(temp) / "missing"))
            result = subprocess.run(["make", "-o", "test", "check"], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("check ok", result.stdout)

    def test_render_failure_fails_check_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, TMPDIR=temp)
            result = subprocess.run(["make", "-o", "test", "check", "PYTHON=false"], cwd=ROOT, env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("check ok", result.stdout)
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
