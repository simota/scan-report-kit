#!/usr/bin/env python3
"""Integration check: print both sample reports with a real engine and inspect their text.

Requires Chrome/Chromium or the WeasyPrint CLI, plus pdftotext and Japanese fonts.
These are development tools; the scan_report_kit package remains standard-library only.
"""

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scan_report_kit import output, pdf, report  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", choices=("chrome", "weasyprint"), required=True)
    parser.add_argument("--out", type=Path, help="retain generated HTML, PDFs and extracted text here")
    args = parser.parse_args(argv)
    if not shutil.which("pdftotext"):
        parser.error("pdftotext is required (install Poppler)")
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    workspace = (nullcontext(args.out.resolve()) if args.out is not None else
                 tempfile.TemporaryDirectory(prefix="scan-report-pdf-check-"))
    with workspace as temp:
        directory = Path(temp)
        if args.engine == "weasyprint":
            if not shutil.which("weasyprint"):
                parser.error("the weasyprint CLI is required")
            os.environ["CHROME"] = str(directory / "disabled-chrome")
        for lang in ("en", "ja"):
            sample = ROOT / "examples" / ("report.sample.ja.json" if lang == "ja" else "report.sample.json")
            data = json.loads(sample.read_text(encoding="utf-8"))
            html, result = directory / (lang + ".html"), directory / (lang + ".pdf")
            output.write_output(html, report.render_document(data, lang))
            failures = []
            engine = pdf.to_pdf(html, result, failures, timeout=60)
            if engine != args.engine:
                raise RuntimeError(f"{lang}: expected {args.engine}, got {engine}; failures={failures}")
            extracted = subprocess.run(
                ["pdftotext", "-layout", str(result), "-"], capture_output=True,
                text=True, check=True, timeout=30,
            ).stdout
            if args.out is not None:
                output.write_output(directory / (lang + ".txt"), extracted)
            compact = re.sub(r"\s+", "", extracted)
            expected = [data["meta"]["project"], data["report"]["title"],
                        data["report"]["running_footer"], data.get("summary", "")]
            for finding in data["findings"]:
                expected.append(finding["title"])
                expected.extend(field.get("text", "") for field in finding.get("fields", []))
            for text in expected:
                if re.sub(r"\s+", "", text) not in compact:
                    raise RuntimeError(f"{lang}: PDF text is missing {text!r}")
            print(f"{engine}: {lang} PDF contains the project, title, footer, summary "
                  f"and all {len(data['findings'])} findings with their field text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
