#!/usr/bin/env python3
"""Integration check: print sample reports and check text and newline layout.

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
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scan_report_kit import output, pdf, report  # noqa: E402


def render_pdf(data, lang, name, directory, expected_engine):
    html, result = directory / (name + ".html"), directory / (name + ".pdf")
    output.write_output(html, report.render_document(data, lang))
    failures = []
    engine = pdf.to_pdf(html, result, failures, timeout=60)
    if engine != expected_engine:
        raise RuntimeError(f"{name}: expected {expected_engine}, got {engine}; failures={failures}")
    return result


def check_newline_positions(bbox, groups):
    """Compare short marker coordinates; whitespace-stripped text misses this bug."""
    markers = {marker: [] for group in groups for marker in group}
    markers.update({"SpaceBefore": [], "SpaceAfter": []})
    for number, page in enumerate(ET.fromstring(bbox).findall(".//{*}page"), 1):
        for word in page.findall(".//{*}word"):
            if word.text in markers:
                markers[word.text].append((number, float(word.attrib["xMin"]),
                                           float(word.attrib["yMin"]), float(word.attrib["xMax"])))
    for group in groups:
        positions = [markers[marker] for marker in group]
        if any(len(found) != 1 for found in positions):
            raise RuntimeError(f"newlines: expected each marker once: {dict(zip(group, positions))}")
        positions = [found[0] for found in positions]
        first, second = positions[:2]
        step = second[2] - first[2]  # The first pair is separated by an ordinary LF.
        if step <= 1 or any(
                position[0] != first[0]
                or abs(position[1] - first[1]) > 0.75
                or abs(position[2] - first[2] - index * step) > 0.75
                for index, position in enumerate(positions)):
            raise RuntimeError(f"newlines: LF, CRLF and CR need equal line spacing: "
                               f"{dict(zip(group, positions))}")

    # These two metadata values use identical fonts and words. Under normal
    # whitespace, a recorded CR must separate words just as a space does.
    before, after = markers["SpaceBefore"], markers["SpaceAfter"]
    if len(before) != 2 or len(after) != 2:
        raise RuntimeError(f"newlines: normal-whitespace markers are missing: {before}, {after}")
    gaps = []
    order = lambda position: (position[0], position[2], position[1])
    for left, right in zip(sorted(before, key=order), sorted(after, key=order)):
        if left[0] != right[0] or abs(left[2] - right[2]) > 0.75:
            raise RuntimeError(f"newlines: normal-whitespace CR split the line: {left}, {right}")
        gaps.append(right[1] - left[3])
    if min(gaps) <= 0.5 or abs(gaps[0] - gaps[1]) > 0.5:
        raise RuntimeError(f"newlines: normal-whitespace CR changed word spacing: {gaps}")


def check_newlines(directory, engine):
    groups = [
        ["SummaryStart", "SummaryLf", "SummaryCrLf", "SummaryCr"],
        ["EvidenceStart", "EvidenceLf", "EvidenceCrLf", "EvidenceCr"],
    ]
    texts = [f"{group[0]}\n{group[1]}\r\n{group[2]}\r{group[3]}" for group in groups]
    data = {
        "format": "scan-report/1",
        "meta": {"project": "Newline rendering check", "date": "2026-01-01",
                 "scope": "SpaceBefore SpaceAfter", "method": "SpaceBefore\rSpaceAfter"},
        "summary": texts[0],
        "findings": [{"id": "N-1", "title": "Recorded newline evidence",
                      "severity": "Info", "status": "Fixed",
                      "fields": [{"label": "Evidence", "text": texts[1]}]}],
    }
    result = render_pdf(data, "en", "newlines", directory, engine)
    # Keep both readable text and the coordinates even when an assertion fails.
    for option, suffix in (("-layout", ".txt"), ("-bbox-layout", ".bbox.html")):
        extracted = subprocess.run(
            ["pdftotext", option, str(result), "-"], capture_output=True,
            text=True, check=True, timeout=30,
        ).stdout
        output.write_output(directory / ("newlines" + suffix), extracted)
    check_newline_positions(extracted, groups)
    print(f"{engine}: summary and evidence render LF, CRLF and CR on equally spaced lines; "
          "normal-whitespace CR retains a word space")


def check_field_tables(directory, engine):
    # Automatic table layout must wrap an unbreakable cell inside the page:
    # glyphs pushed past the page edge are clipped and missing from the text.
    cell = "LongTableCell" * 24
    data = {
        "format": "scan-report/1",
        "meta": {"project": "Field table check", "date": "2026-01-01"},
        "findings": [{"id": "T-1", "title": "Recorded table evidence", "severity": "Info", "status": "Fixed",
                      "fields": [{"label": "Request", "group": "Evidence", "columns": ["Name", "Value"],
                                  "rows": [["Header", cell], ["Short", "TableShortValue"]]}]}],
    }
    result = render_pdf(data, "en", "table", directory, engine)
    extracted = subprocess.run(["pdftotext", str(result), "-"], capture_output=True,
                               text=True, check=True, timeout=30).stdout
    output.write_output(directory / "table.txt", extracted)
    compact = re.sub(r"\s+", "", extracted)
    for text in ("Evidence", "Request", "Name", "Value", "Header", cell, "Short", "TableShortValue"):
        if text not in compact:
            raise RuntimeError(f"table: PDF text is missing {text[:40]!r} ({len(text)} characters)")
    print(f"{engine}: field groups and tables print every cell, wrapping a long cell inside the page")


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
            result = render_pdf(data, lang, lang, directory, args.engine)
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
            print(f"{args.engine}: {lang} PDF contains the project, title, footer, summary "
                  f"and all {len(data['findings'])} findings with their field text")
        check_newlines(directory, args.engine)
        check_field_tables(directory, args.engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
