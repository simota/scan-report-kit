"""Exercise the generated dashboard in Node without third-party dependencies.

The DOM fixture comes from the real rendered HTML. The JavaScript suite uses a
small DOM adapter to test behaviour; layout still needs browser inspection.
"""

import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import unittest

from scan_report_kit import report


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


class DOMTree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "#document", "attrs": {}, "children": []}
        self.stack = [self.root]
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1]["children"].append({"text": data})


def example():
    return {
        "format": "scan-report/1",
        "meta": {"project": "Project <&> 日本語", "date": "2026-10-11"},
        "findings": [
            {"id": "F-10", "title": "First <img src=x onerror=attack()>", "severity": "High", "status": "Open",
             "confidence": "Certain", "verdict": "Confirmed", "category": "constructor", "location": "src/<entry>.py:5",
             "action": {"label": "Action label marker", "text": "Action text marker ````"},
             "fields": [{"label": "Evidence label marker", "text": "Body marker\nSecond line <&> 日本語"}],
             "snippet": {"start": 4, "hit": [5, 5], "lines": ["alpha", "snippet_unique_token"]},
             "references": [{"type": "Reference type marker", "title": "Reference title marker",
                              "url": "https://example.test/reference_unique_token?q=1&x=2"}]},
            {"id": "F-2", "title": "Sparse record", "severity": "Low", "status": "Open"},
            {"id": "F-02", "title": "Equivalent numeric ID", "severity": "Medium", "status": "Open"},
            {"id": "F-1", "title": "Fixed finding", "severity": "High", "status": "Fixed"},
            {"id": '__proto__ < & " / 日本語', "title": "Excluded record", "severity": "Medium", "status": "Accepted",
             "excluded": True, "category": "__proto__", "action": {"label": "Hidden action", "text": "Not actionable"}},
            {"id": "F-3", "title": "Second high finding", "severity": "High", "status": "Open"},
        ],
    }


@unittest.skipUnless(NODE, "Node.js is needed for the dependency-free dashboard behaviour suite")
class DashboardTests(unittest.TestCase):
    def test_dashboard_runtime(self):
        templates = {}
        for lang in ("en", "ja"):
            data = example()
            templates[lang] = DOMTree(report.render_dashboard(report.validate(data), lang)).root
            data["findings"] = []
            templates["empty_" + lang] = DOMTree(report.render_dashboard(report.validate(data), lang)).root
        result = subprocess.run(
            [NODE, str(ROOT / "tests" / "dashboard_dom_test.js")],
            input=json.dumps(templates, ensure_ascii=True), capture_output=True, text=True,
            timeout=45, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
