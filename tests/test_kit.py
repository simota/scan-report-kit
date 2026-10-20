import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scan_report_kit import output, pdf, report, style  # noqa: E402
import vendor  # noqa: E402

SAMPLE = json.loads((ROOT / "examples" / "report.sample.json").read_text(encoding="utf-8"))
SAMPLE_JA = json.loads((ROOT / "examples" / "report.sample.ja.json").read_text(encoding="utf-8"))


class ValidateTests(unittest.TestCase):
    def test_samples_are_valid(self):
        report.validate(copy.deepcopy(SAMPLE))
        report.validate(copy.deepcopy(SAMPLE_JA))

    def bad(self, mutate, message):
        data = copy.deepcopy(SAMPLE)
        mutate(data)
        with self.assertRaisesRegex(report.FormatError, message):
            report.validate(data)

    def test_rejects_wrong_format_and_enums(self):
        self.bad(lambda d: d.update(format="x"), "format")
        self.bad(lambda d: d["findings"][0].update(severity="Critical"), "severity")
        self.bad(lambda d: d["findings"][0].update(status="Done"), "status")
        self.bad(lambda d: d["findings"][1].update(id="F-001"), "duplicate")
        self.bad(lambda d: d["meta"].pop("project"), "project")

    def test_rejects_non_http_reference_and_bad_snippet(self):
        self.bad(lambda d: d["findings"][0]["references"][0].update(url="javascript:alert(1)"), "http")
        self.bad(lambda d: d["findings"][0]["snippet"].update(hit=[1]), "snippet")
        self.bad(lambda d: d["findings"][0]["snippet"].update(start=True), "snippet")


class RenderTests(unittest.TestCase):
    def test_document_escapes_and_numbers(self):
        page = report.render_document(report.validate(copy.deepcopy(SAMPLE)), "en")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn('content:"EXAMPLE REVIEW"', page)
        self.assertIn("class='finding sev-High' id='finding-1'", page)
        self.assertIn("class='finding excluded'", page)
        # Excluded findings stay out of the counts: 2 open (High 1, Medium 1).
        self.assertIn("2 open High or Medium findings need attention.", page)

    def test_japanese_labels_and_footer(self):
        page = report.render_document(report.validate(copy.deepcopy(SAMPLE_JA)), "ja")
        self.assertIn('<html lang="ja">', page)
        self.assertIn('content:"サンプル診断"', page)
        self.assertIn("重大度別の未対応指摘", page)

    def test_dashboard_payload_cannot_close_the_script(self):
        data = copy.deepcopy(SAMPLE)
        data["findings"][0]["title"] = "</script><img src=x onerror=alert(1)> __DATA__"
        page = report.render_dashboard(report.validate(data), "en")
        body = page.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("<", body)
        self.assertIn("__DATA__", json.loads(body)["findings"][0]["title"])
        self.assertIn("function fillDetails(dt, f)", page)
        self.assertEqual(page.count("__CSS__"), 0)

    def test_register_layout_does_not_leak_to_other_tables(self):
        css = style.dashboard_css()
        self.assertIn('<table class="register" role="table">', report.render_dashboard(report.validate(copy.deepcopy(SAMPLE)), "en"))
        self.assertNotRegex(css, r"(?:^|[}\n,])(?:\.register\s)?th:nth-child")
        self.assertNotIn("table-layout:fixed}", css.replace("table.register{table-layout:fixed}", ""))
        self.assertNotRegex(css, r"(?:^|[{}\n,])(?:table|\.register),")
        self.assertNotIn("table.register thead{display:none}", css)
        self.assertIn("table.register thead{position:absolute;width:1px;height:1px;", css)
        # The narrow register rows stay a grid: as specific as the block rule above it.
        self.assertIn("table.register tr.row{padding:14px;border-bottom:1px solid var(--line);display:grid;", css)
        # Cells wrap between words; only the register breaks anywhere.
        self.assertIn("vertical-align:top;overflow-wrap:break-word}", css)
        self.assertIn("table.register td{overflow-wrap:anywhere}", css)
        self.assertIn("th.nowrap,td.nowrap,.nowrap{white-space:nowrap}", css)
        # Any number of cards shares one row, two columns on narrow screens.
        self.assertIn(".cards{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr)", css)
        self.assertIn(".cards{grid-auto-flow:row;grid-template-columns:repeat(2,minmax(0,1fr))}", css)
        # Print stays light: the reset must outrank the OS dark-theme rule,
        # :root:not([data-theme="light"]), whether or not a theme is saved.
        self.assertIn('@media print{html:root:not([data-theme="light"]),html:root[data-theme]{color-scheme:light;', css)

    def test_running_footer_is_a_css_string(self):
        css = style.document_css('A "quoted" \\ footer')
        self.assertIn('content:"A \\"quoted\\" \\\\ footer"', css)

    def test_cli_writes_outputs_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            cmd = [sys.executable, "-m", "scan_report_kit", str(ROOT / "examples" / "report.sample.json"),
                   "--out", str(out), "--no-pdf"]
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((out / "dashboard.html").is_file() and (out / "report.html").is_file())
            (out / "report.html").unlink()
            (out / "report.html").symlink_to(Path(tmp) / "elsewhere")
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn("refusing", r.stderr)
            self.assertFalse((Path(tmp) / "elsewhere").exists())


@unittest.skipIf(os.name == "nt", "POSIX shell fixture")
class ShellScopeTests(unittest.TestCase):
    def test_application_shell_rules_apply_only_to_the_kit_dashboard(self):
        # Consumers inline dashboard_css() into their own page structure; the
        # shell's sticky regions and column widths must not reach those pages.
        import re
        css = style.dashboard_css()
        shell = re.sub(r"/\*.*?\*/", "", css[css.index("/* Application shell:"):], flags=re.S)
        selectors = [sel.strip() for group in re.findall(r"(?:(?<=[{}])|^)([^{}@]+)\{[^{}]*\}", shell, re.M)
                     for sel in group.split(",")]
        self.assertTrue(selectors)
        unscoped = [sel for sel in selectors if not sel.startswith((".app-shell ", "html.app-shell", ":root"))]
        self.assertEqual(unscoped, [])
        self.assertIn('<html lang="en" class="app-shell">', report.render_dashboard(copy.deepcopy(SAMPLE), "en"))


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.html = self.root / "a.html"
        self.html.write_text("<p>x</p>", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, name, body):
        tool = self.root / "bin" / name
        tool.parent.mkdir(exist_ok=True)
        tool.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        tool.chmod(0o755)
        return tool

    def test_failure_is_reported_with_redacted_stderr(self):
        chrome = self.fake("chrome", 'echo "token=abc123" >&2; exit 1')
        failures = []
        with patch.dict(os.environ, {"CHROME": str(chrome), "PATH": str(chrome.parent)}):
            self.assertIsNone(pdf.to_pdf(self.html, self.root / "a.pdf", failures,
                                         redact=lambda s: s.replace("abc123", "***")))
        self.assertEqual(failures[0][0], "chrome")
        self.assertIn("exited with status 1", failures[0][1])
        self.assertIn("token=***", failures[0][2])

    def test_success_and_stale_pdf_removed(self):
        chrome = self.fake("chrome", 'for a; do case "$a" in --print-to-pdf=*) printf "%%PDF-1.7\\nfixture\\n%%%%EOF\\n" > "${a#*=}";; esac; done')
        out = self.root / "a.pdf"
        out.write_bytes(b"stale")
        with patch.dict(os.environ, {"CHROME": str(chrome), "PATH": str(chrome.parent)}):
            self.assertEqual(pdf.to_pdf(self.html, out), "chrome")
        self.assertEqual(out.read_bytes(), b"%PDF-1.7\nfixture\n%%EOF\n")

    def test_failed_chrome_pdf_is_discarded_before_weasyprint(self):
        chrome = self.fake("chrome", 'for a; do case "$a" in --print-to-pdf=*) printf partial > "${a#*=}";; esac; done; exit 1')
        self.fake("weasyprint", 'printf "%%PDF-1.7\\nfixture\\n%%%%EOF\\n" > "$2"')
        out, failures = self.root / "a.pdf", []
        with patch.dict(os.environ, {"CHROME": str(chrome), "PATH": str(chrome.parent)}):
            self.assertEqual(pdf.to_pdf(self.html, out, failures), "weasyprint")
        self.assertEqual(out.read_bytes(), b"%PDF-1.7\nfixture\n%%EOF\n")
        self.assertIn("exited with status 1; its PDF was discarded", failures[0][1])

    def test_failed_weasyprint_leaves_no_partial_output(self):
        weasy = self.fake("weasyprint", 'printf partial > "$2"; exit 1')
        out = self.root / "a.pdf"
        with patch.dict(os.environ, {"CHROME": str(self.root / "missing"), "PATH": str(weasy.parent)}):
            self.assertIsNone(pdf.to_pdf(self.html, out))
        self.assertFalse(out.exists())

    def test_timeout_env_and_scaling(self):
        chrome = self.fake("chrome", "exec " + (shutil.which("sleep") or "/bin/sleep") + " 30")
        failures, started = [], time.monotonic()
        with patch.dict(os.environ, {"CHROME": str(chrome), "PATH": str(chrome.parent)}):
            self.assertIsNone(pdf.to_pdf(self.html, self.root / "a.pdf", failures, timeout=1))
        self.assertLess(time.monotonic() - started, 20)
        self.assertIn("timed out after 1 s", failures[0][1])
        with patch.dict(os.environ, {"X_TIMEOUT": ""}):
            self.assertEqual(pdf.pdf_timeout(0, env="X_TIMEOUT"), 120)
            self.assertEqual(pdf.pdf_timeout(10 ** 6, env="X_TIMEOUT"), 900)
        with patch.dict(os.environ, {"X_TIMEOUT": "45"}):
            self.assertEqual(pdf.pdf_timeout(10 ** 6, env="X_TIMEOUT"), 45)


class OutputAndVendorTests(unittest.TestCase):
    def test_write_output_replaces_a_link_instead_of_following_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, link = Path(tmp) / "target", Path(tmp) / "page.html"
            target.write_text("keep", encoding="utf-8")
            link.symlink_to(target)
            output.write_output(link, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")
            self.assertFalse(link.is_symlink())

    def test_vendor_copy_and_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy_dir = vendor.vendor(tmp)
            self.assertTrue((copy_dir / "assets" / "dashboard.css").is_file())
            self.assertEqual(vendor.check(copy_dir), [])
            (copy_dir / "assets" / "dashboard.css").write_text("edited", encoding="utf-8")
            (copy_dir / "extra.py").write_text("", encoding="utf-8")
            problems = vendor.check(copy_dir)
            self.assertTrue(any("dashboard.css: edited" in p for p in problems))
            self.assertTrue(any("extra.py: not part" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
