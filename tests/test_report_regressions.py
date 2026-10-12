"""Security and format-contract regressions for both report renderers."""

import copy
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scan_report_kit import report, style  # noqa: E402


def finding(fid="F-1", severity="High", status="Open", **extra):
    return {"id": fid, "title": "A recorded finding", "severity": severity, "status": status, **extra}


def document(*findings):
    return {"format": report.FORMAT, "meta": {"project": "Example", "date": "2026-10-12"},
            "findings": list(findings)}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class ReportSecurityTests(unittest.TestCase):
    def payload(self, page):
        match = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match)
        self.assertNotIn("<", match.group(1))
        return json.loads(match.group(1), parse_constant=lambda value: self.fail("Invalid JSON: " + value))

    def test_footer_cannot_close_style_or_inject_html(self):
        payloads = (
            '</style><script>injected()</script><style>',
            '</StYlE><img src=x onerror=alert(1)><style>',
            '<!-- <style> </style><svg onload=alert(1)>',
        )
        for footer in payloads:
            for container in ("report", "labels"):
                with self.subTest(footer=footer, container=container):
                    data = document()
                    data[container] = {"running_footer": footer}
                    page = report.render_document(data, "en")
                    parsed = Tags()
                    parsed.feed(page)
                    self.assertEqual([tag for tag, _ in parsed.tags if tag in ("style", "script", "img", "svg")],
                                     ["style"])

    def test_footer_is_never_substituted_into_a_css_comment(self):
        footer = '*/ @import url(https://example.invalid/track); /*'
        css = style.document_css(footer)
        self.assertNotIn(footer, css[:css.index("@page{")])
        self.assertEqual(css.count(footer), 1)
        self.assertIn('content:"' + footer + '"', css)

    def test_footer_quotes_controls_and_html_are_escaped_in_one_pass(self):
        footer = '日本語 "quoted" \\ \r\n\f</style> f __RUNNING_FOOTER__'
        css = style.document_css(footer)
        self.assertIn('日本語 \\"quoted\\" \\\\ \\d \\a \\c \\3c /style> f __RUNNING_FOOTER__', css)
        self.assertNotIn("\r", css)
        self.assertNotIn("\f", css)
        self.assertNotIn("</style>", css)

    def test_renderers_validate_direct_input_before_rendering(self):
        data = document(finding(excluded=True, severity="High'><script>injected()</script><span class='"))
        for render in (report.render_document, report.render_dashboard):
            with self.subTest(renderer=render.__name__):
                with self.assertRaisesRegex(report.FormatError, r"findings\[0\]\.severity"):
                    render(data, "en")

    def test_renderers_reject_unsupported_languages_consistently(self):
        for lang in ("de", "", None, []):
            for render in (report.render_document, report.render_dashboard):
                with self.subTest(lang=lang, renderer=render.__name__):
                    with self.assertRaisesRegex(report.FormatError, "lang"):
                        render(document(), lang)

    def test_unknown_report_fields_cannot_override_labels(self):
        data = document(finding(), finding("F-2"))
        data["report"] = {"kind": "Custom kind", "risk_summary": ["not", "text"], "summary": "Wrong heading"}
        data["labels"] = {"kind": "Final kind", "custom_extension": "Ignored"}
        payload = self.payload(report.render_dashboard(data, "en"))
        self.assertEqual(payload["labels"]["kind"], "Final kind")
        self.assertEqual(payload["labels"]["summary"], report.LABELS["en"]["summary"])
        self.assertNotIn("custom_extension", payload["labels"])
        self.assertEqual(payload["headline"], "2 open High or Medium findings need attention.")

    def test_count_labels_treat_unknown_braces_as_literal_text(self):
        data = document(finding(), finding("F-2"))
        data["labels"] = {"risk_summary": "{n} / {n}; {other}; {n:1000000000}; lone {"}
        expected = "2 / 2; {other}; {n:1000000000}; lone {"
        self.assertEqual(self.payload(report.render_dashboard(data, "en"))["headline"], expected)
        self.assertIn(expected, report.render_document(data, "en"))

    def test_singular_count_label_uses_the_same_literal_substitution(self):
        data = document(finding(severity="Low"))
        data["labels"] = {"open_summary_one": "One: {n}; {unknown}"}
        for lang in ("en", "ja"):
            with self.subTest(lang=lang):
                self.assertEqual(self.payload(report.render_dashboard(data, lang))["headline"], "One: 1; {unknown}")

    def test_dashboard_omits_extensions_at_every_object_level(self):
        extension = {"not_json": float("nan"), "private": "SHOULD_NOT_APPEAR"}
        row = finding(action={"label": "Fix", **extension},
                      fields=[{"label": "Evidence", "text": "Observed", **extension}],
                      snippet={"start": 1, "hit": [1, 1], "lines": ["code"], **extension},
                      references=[{"url": "https://example.org", **extension}], **extension)
        data = document(row)
        data.update(extension)
        data["meta"].update(extension)
        data["report"] = dict(extension)
        data["perspectives"] = [{"name": "Coverage", **extension}]
        page = report.render_dashboard(data, "en")
        payload = self.payload(page)
        self.assertNotIn("SHOULD_NOT_APPEAR", page)
        self.assertEqual(payload["findings"][0]["action"], {"label": "Fix"})
        self.assertEqual(payload["findings"][0]["snippet"], {"start": 1, "hit": [1, 1], "lines": ["code"]})

    def test_script_terminators_and_template_tokens_round_trip_as_text(self):
        text = '</script><!-- <script> __CSS__ __DATA__ 日本語 \U0001f50d \u2028\u2029'
        data = document(finding(fields=[{"label": "Evidence", "text": text}]))
        data["summary"] = text
        page = report.render_dashboard(data, "ja")
        payload = self.payload(page)
        self.assertEqual(payload["summary"], text)
        self.assertEqual(payload["findings"][0]["fields"][0]["text"], text)
        parsed = Tags()
        parsed.feed(page)
        self.assertEqual(sum(tag == "script" for tag, _ in parsed.tags), 2)

    def test_user_text_is_escaped_in_document_text_and_attributes(self):
        text = '\"><img src=x onerror=alert(1)> & <script>injected()</script>'
        data = document(finding(fid=text, title=text, location=text, fields=[{"label": text, "text": text}],
                                references=[{"url": "https://example.org/?q=\"<tag>", "type": text, "title": text}]))
        data["summary"] = text
        data["meta"]["project"] = text
        data["labels"] = {"title_col": text}
        parsed = Tags()
        parsed.feed(report.render_document(data, "en"))
        self.assertFalse(any(tag in ("img", "script", "tag") for tag, _ in parsed.tags))
        self.assertIn(("a", {"href": 'https://example.org/?q="<tag>'}), parsed.tags)


class FormatContractTests(unittest.TestCase):
    def assert_bad(self, mutate, location):
        data = document(finding())
        mutate(data)
        with self.assertRaisesRegex(report.FormatError, location):
            report.validate(data)

    def test_date_requires_exact_form_and_a_real_calendar_day(self):
        for value in ("not-a-date", "2026-2-01", "20261012", "2026-W42-1", "2026-02-29", "1900-02-29",
                      "2026-13-01", "2026-00-01", "2026-01-32", "0000-01-01", "2026-10-12 "):
            with self.subTest(date=value):
                self.assert_bad(lambda data: data["meta"].update(date=value), r"meta\.date")
        for value in ("0001-01-01", "2000-02-29", "2024-02-29", "9999-12-31"):
            with self.subTest(date=value):
                data = document()
                data["meta"]["date"] = value
                self.assertIs(report.validate(data), data)

    def test_title_must_be_one_line(self):
        for separator in ("\n", "\r", "\r\n", "\v", "\f", "\x85", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)):
                self.assert_bad(lambda data: data["findings"][0].update(title="Title" + separator), r"\.title")

    def test_invalid_urls_raise_format_errors_with_the_reference_location(self):
        urls = (None, 123, "", "javascript:alert(1)", "data:text/html,test", "//example.org", "https:/example.org",
                "https://[::1", "https://[invalid]", "https://user@", "https://:443", "https://example.org:abc",
                "https://example.org:65536", "https://example.org:-1", "https://example.org/\nnext",
                "https://example.org/\rnext", "https://example.org/\tnext", " https://example.org",
                "https://example.org/path with space", "https://example.org/\x00", "https://example.org/\x7f",
                "https://example.org/\x80", "https://example.org/\x9f",
                "https://example.org\\@other.invalid", "https://example.org\uff0f@other.invalid",
                "https://<invalid>/", "https://exa^mple.org/", "https://example.org|invalid/",
                "https://%/", "https://%zz/", "https://%0/", "https://%00/", "https://%09example.org/",
                "https://%20/", "https://%2f/", "https://%5c/", "https://%23/", "https://%3a/",
                "https://%40/", "https://%5b/", "https://%5d/", "https://%7c/", "https://%25/",
                "https://%ff/", "https://%c0%af/", "https://%EF%BC%8Fexample.org/", "https://\ufffd.org/",
                "https://%EF%BF%BD.org/")
        for url in urls:
            with self.subTest(url=url):
                self.assert_bad(lambda data: data["findings"][0].update(references=[{"url": url}]),
                                r"findings\[0\]\.references\[0\]\.url")

    def test_http_urls_keep_paths_queries_unicode_and_valid_ports(self):
        urls = ("http://example.org", "HTTPS://example.org/path?q=a%20b&x=1#part",
                "https://[::1]:8443/", "http://127.0.0.1:0", "https://example.org:65535",
                "https://例え.jp/資料", "https://user:password@example.org/path", "http://localhost:8080/",
                "https://[2001:db8::1]/?x=%7C#%00", "http://[::ffff:192.0.2.1]:80/",
                "https://[fe80::1%25eth0]/", "https://%65xample.org/",
                "https://%E4%BE%8B%E3%81%88.jp/%E8%B3%87%E6%96%99", "https://例え.テスト/資料?q=値%20次",
                "https://example.org/%2F%5C%00?q=%3C%3E%25", "https://faß.de/", "https://example.org./")
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(report.http_url(url, "url"), url)
                report.validate(document(finding(references=[{"url": url}])))

    def test_modern_rtl_idns_and_percent_encoding_render_unchanged(self):
        # RFC 5893 section 2 permits RTL labels ending in EN or AN digits;
        # Python's older IDNA2003 codec rejects these otherwise valid hosts.
        for host in ("مثال1.إختبار", "مثال١.إختبار", "نامه1.example"):
            for spelling in (host, quote(host)):
                url = "https://" + spelling + "/reference?q=a%20b"
                with self.subTest(url=url):
                    data = document(finding(references=[{"url": url}]))
                    original = copy.deepcopy(data)
                    self.assertIs(report.validate(data), data)
                    parsed = Tags()
                    parsed.feed(report.render_document(data, "en"))
                    self.assertIn(("a", {"href": url}), parsed.tags)
                    page = report.render_dashboard(data, "ja")
                    payload = json.loads(re.search(
                        r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1))
                    self.assertEqual(payload["findings"][0]["references"][0]["url"], url)
                    self.assertEqual(data, original)

    def test_snippet_rejects_invalid_and_inexact_line_numbers(self):
        for change in ({"start": True}, {"start": 0}, {"start": -1}, {"start": 1.0},
                       {"start": report.MAX_LINE_NUMBER + 1}, {"hit": [False, 1]}, {"hit": [1, 1.0]},
                       {"hit": [0, 1]}, {"hit": [-3, -1]}, {"hit": [3, 2]}, {"hit": [1]},
                       {"hit": [1, report.MAX_LINE_NUMBER + 1]}, {"start": report.MAX_LINE_NUMBER}):
            with self.subTest(change=change):
                snippet = {"start": 1, "hit": [1, 2], "lines": ["first", "last"], **change}
                self.assert_bad(lambda data: data["findings"][0].update(snippet=snippet), r"\.snippet")

    def test_snippet_rejects_non_strings_and_embedded_line_breaks(self):
        for line in (None, 123, "first\nsecond", "first\r", "first\u2028second"):
            with self.subTest(line=line):
                snippet = {"start": 1, "hit": [1, 1], "lines": [line]}
                self.assert_bad(lambda data: data["findings"][0].update(snippet=snippet), r"\.snippet\.lines\[0\]")

    def test_snippet_highlights_absolute_overlap_and_exact_boundary(self):
        snippet = {"start": 41, "hit": [39, 42], "lines": ["one", "two", "three"]}
        data = document(finding(snippet=snippet))
        report.validate(data)
        page = report.render_document(data, "en")
        self.assertIn("<span class='hit'><b>   41</b> one</span>", page)
        self.assertIn("<span class='hit'><b>   42</b> two</span>", page)
        self.assertIn("<span><b>   43</b> three</span>", page)
        edge = {"start": report.MAX_LINE_NUMBER - 1, "hit": [report.MAX_LINE_NUMBER, report.MAX_LINE_NUMBER],
                "lines": ["one", "two"]}
        report.validate(document(finding(snippet=edge)))

    def test_surrogates_are_rejected_at_every_declared_text_location(self):
        mutations = (
            (lambda data: data["meta"].update(project="\ud800"), r"meta\.project"),
            (lambda data: data.update(summary="\udfff"), "summary"),
            (lambda data: data.update(report={"running_footer": "\ud800"}), "running_footer"),
            (lambda data: data.update(labels={"summary": "\udfff"}), r"labels\.summary"),
            (lambda data: data.update(limitations=["\ud800"]), r"limitations\[0\]"),
            (lambda data: data.update(perspectives=[{"name": "\ud800"}]), r"perspectives\[0\]\.name"),
            (lambda data: data["findings"][0].update(title="\ud800"), r"\.title"),
            (lambda data: data["findings"][0].update(fields=[{"label": "Evidence", "text": "\ud800"}]), r"fields\[0\]\.text"),
            (lambda data: data["findings"][0].update(references=[{"url": "https://example.org/\ud800"}]), r"references\[0\]\.url"),
        )
        for mutate, location in mutations:
            with self.subTest(location=location):
                self.assert_bad(mutate, location)

    def test_valid_japanese_and_supplementary_unicode_render_in_both_outputs(self):
        data = document(finding(title="認可の確認 \U0001f50d", fields=[{"label": "証跡", "text": "日本語\n詳細 \U0001f680"}]))
        data["report"] = {"running_footer": "日本語の診断"}
        for render in (report.render_document, report.render_dashboard):
            with self.subTest(renderer=render.__name__):
                page = render(data, "ja")
                self.assertIn("認可の確認 \U0001f50d", page)
                self.assertEqual(page.encode("utf-8").decode("utf-8"), page)


class ReportAccountingTests(unittest.TestCase):
    def test_counts_exclude_every_excluded_severity_and_status(self):
        rows = []
        for excluded in (False, True):
            for severity in report.SEVERITIES:
                for status in report.STATUSES:
                    rows.append(finding(str(len(rows)), severity, status, excluded=excluded))
        data = document(*rows)
        counts = report.counts(report.validate(data))
        self.assertTrue(all(count == 1 for count in counts["by"].values()))
        self.assertEqual({key: counts[key] for key in ("open", "urgent", "included", "excluded", "fixed", "accepted")},
                         {"open": 4, "urgent": 2, "included": 12, "excluded": 12, "fixed": 4, "accepted": 4})

    def test_priority_is_stable_and_document_anchors_keep_original_indices(self):
        data = document(finding("closed-high", "High", "Fixed"), finding("open-info", "Info"),
                        finding("open-medium", "Medium"), finding("open-high-a"), finding("open-high-b"),
                        finding("accepted-high", "High", "Accepted"), finding("excluded", excluded=True))
        original = copy.deepcopy(data)
        self.assertEqual([row["id"] for row in report.priority(report.included(data))],
                         ["open-high-a", "open-high-b", "open-medium", "open-info", "closed-high", "accepted-high"])
        parsed = Tags()
        parsed.feed(report.render_document(data, "en"))
        self.assertEqual([attrs["id"] for tag, attrs in parsed.tags if tag == "article"],
                         ["finding-4", "finding-5", "finding-3", "finding-2", "finding-1", "finding-6", "finding-7"])
        self.assertEqual(data, original)
        report.render_dashboard(data, "ja")
        self.assertEqual(data, original)

    def test_headlines_handle_empty_excluded_closed_singular_and_plural(self):
        cases = (
            (document(), "No findings were recorded."),
            (document(finding(excluded=True)), "No findings were recorded."),
            (document(finding(status="Fixed")), "No open findings."),
            (document(finding()), "1 open High or Medium finding needs attention."),
            (document(finding(severity="Low")), "1 open finding, not High or Medium."),
            (document(finding(severity="Info"), finding("F-2", "Low")), "2 open findings, none High or Medium."),
        )
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(report.headline(data, report.labels_for(data, "en")), expected)


if __name__ == "__main__":
    unittest.main()
