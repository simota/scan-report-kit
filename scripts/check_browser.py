#!/usr/bin/env python3
"""Exercise dashboards and printable HTML in real Chrome and retain CI artifacts.

Requires Chrome/Chromium on Linux or macOS (CHROME selects a binary). No npm
packages or browser driver are needed. Missing Chrome, missing result markers
and failed assertions all fail the check. Full-page screenshots, rendered DOM,
stderr and a summary go to --out.
"""

import argparse
import base64
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scan_report_kit import output, pdf, report  # noqa: E402

RESULT_ID = "scan-report-browser-result"
MARKUP = '<srk-smoke-injected data-value="<&">literal & text</srk-smoke-injected>'
SEARCH = "scan-report-browser-search-token"
VIEWPORTS = {"desktop": (1280, 960), "mobile": (390, 844)}

ERROR_CAPTURE = """<script>
window.__scanBrowserErrors = [];
window.addEventListener('error', function (e) {
  window.__scanBrowserErrors.push(e.message || 'resource error');
}, true);
window.addEventListener('unhandledrejection', function (e) {
  window.__scanBrowserErrors.push(String(e.reason));
});
</script>
"""

SMOKE_JS = r"""
window.__scanBrowserSmoke = (async function () {
  'use strict';
  const C = __CONFIG__, checks = [], errors = [];
  const marker = document.getElementById('scan-report-browser-result');
  const byId = id => document.getElementById(id);
  const rows = () => Array.from(document.querySelectorAll('#rows > tr.row'));
  const ids = () => rows().map(row => row.cells[0].textContent);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  function check(condition, name) {
    if (!condition) throw new Error(name);
    checks.push(name);
  }
  function select(id, value) {
    byId(id).value = value;
    byId(id).dispatchEvent(new Event('change', { bubbles: true }));
  }
  function search(value) {
    byId('f-q').value = value;
    byId('f-q').dispatchEvent(new Event('input', { bubbles: true }));
  }
  function background(node) {
    for (; node; node = node.parentElement) {
      const color = getComputedStyle(node).backgroundColor;
      if (color !== 'transparent' && color !== 'rgba(0, 0, 0, 0)') return color;
    }
    return '';
  }
  async function link(anchor) {
    window.location.hash = anchor;
    for (let i = 0; i < 30; i++) {
      await pause(20);
      const detail = byId(anchor + '-detail');
      if (detail && !detail.hidden) return;
    }
    throw new Error('deep link did not open ' + anchor);
  }
  let metrics = {};
  try {
    await document.fonts.ready;
    await pause(20);
    check(document.documentElement.lang === C.lang, 'document language');
    check(byId('h').textContent === C.project, 'project rendered by JavaScript');
    check(rows().length === C.included, 'initial included row count');
    check(document.querySelectorAll('#cards .card').length === 4, 'summary cards rendered');
    check(document.querySelectorAll('#lists > section').length === 3, 'runtime completed');

    select('f-sort', 'id');
    check(JSON.stringify(ids()) === JSON.stringify(C.sortedIds), 'natural ID order');
    search(C.search.toUpperCase());
    check(rows().length === 1 && ids()[0] === C.firstId, 'case-insensitive detail text search');
    search('no-record-matches-this-browser-smoke-query');
    check(rows().length === 0 && !!document.querySelector('#rows .empty'), 'empty search result');

    select('f-sev', 'High');
    await link(C.excludedAnchor);
    check(byId('f-scope').value === 'excluded', 'deep link reveals excluded record');
    check(rows().length === C.excluded, 'excluded row count');
    check(byId(C.excludedAnchor).querySelector('button').getAttribute('aria-expanded') === 'true',
          'deep link updates expansion state');
    await link(C.firstAnchor);
    const toggle = byId(C.firstAnchor).querySelector('.finding-toggle');
    check(document.activeElement === toggle, 'deep link focuses finding toggle');
    toggle.click();
    check(byId(C.firstAnchor + '-detail').hidden, 'finding toggle collapses details');
    document.querySelector('#priority a[href="#' + C.firstAnchor + '"]').click();
    check(!byId(C.firstAnchor + '-detail').hidden, 'repeated deep link reopens details');
    check(byId(C.firstAnchor + '-detail').textContent.includes(C.markup), 'special characters remain text');
    check(!document.querySelector('srk-smoke-injected'), 'report text never creates HTML elements');

    byId('reset').click();
    check(rows().length === C.included && byId('f-sort').value === 'priority' && byId('f-q').value === '',
          'reset restores the included register');
    byId('theme').click();
    const theme = document.documentElement.dataset.theme;
    byId('theme').click();
    check(document.documentElement.dataset.theme !== theme, 'theme toggle');
    const snippet = byId(C.firstAnchor + '-detail').querySelector('.snippet');
    const hit = snippet.querySelector('.hit'), normal = snippet.querySelector('span:not(.hit)');
    check(snippet.querySelectorAll('.hit').length === 3 && !!normal, 'source excerpt marks the recorded lines');
    for (const mode of ['light', 'dark']) {
      document.documentElement.dataset.theme = mode;
      check(background(hit) !== background(normal), mode + ' source highlight is visible against the detail background');
      check(Number(getComputedStyle(hit.querySelector('b')).fontWeight) >
            Number(getComputedStyle(normal.querySelector('b')).fontWeight), mode + ' source highlight has a non-color cue');
    }
    document.documentElement.dataset.theme = 'light';
    window.scrollTo(0, 0);
    await pause(50);
    const root = document.documentElement;
    metrics = { innerWidth: window.innerWidth, clientWidth: root.clientWidth,
      scrollWidth: Math.max(root.scrollWidth, document.body.scrollWidth),
      rowDisplay: getComputedStyle(rows()[0]).display };
    check(Math.abs(window.innerWidth - C.width) <= 1, 'requested viewport width');
    check(metrics.rowDisplay === (C.mobile ? 'grid' : 'table-row'), 'responsive register layout');
    check(metrics.scrollWidth <= metrics.clientWidth + 1, 'no page horizontal overflow');
    check(window.__scanBrowserErrors.length === 0, 'no browser runtime errors');
  } catch (error) {
    errors.push(String(error.message || error));
  }
  errors.push(...window.__scanBrowserErrors);
  marker.textContent = JSON.stringify({ case: C.case, ok: errors.length === 0,
    checks, errors, metrics }).replace(/</g, '\\u003c');
})();
"""


DOCUMENT_SMOKE_JS = r"""
window.__scanBrowserSmoke = (async function () {
  'use strict';
  const C = __CONFIG__, checks = [], errors = [];
  const marker = document.getElementById('scan-report-browser-result');
  let metrics = {};
  function check(condition, name) {
    if (!condition) throw new Error(name);
    checks.push(name);
  }
  try {
    await document.fonts.ready;
    check(document.documentElement.lang === C.lang, 'document language');
    check(document.querySelector('.project').textContent === C.project, 'project rendered in the document');
    const table = document.querySelector('.index'), head = table.tHead;
    const rows = Array.from(table.tBodies[0].rows);
    check(rows.length === C.included, 'included finding index');
    check(table.getAttribute('role') === 'table' && head.getAttribute('role') === 'rowgroup' &&
          table.tBodies[0].getAttribute('role') === 'rowgroup', 'index preserves explicit table structure');
    check(Array.from(head.rows[0].cells).every(cell =>
          cell.getAttribute('role') === 'columnheader' && cell.getAttribute('scope') === 'col'),
          'index identifies column headers');
    check(rows.every(row => row.getAttribute('role') === 'row' &&
          Array.from(row.cells).every(cell => cell.getAttribute('role') === 'cell')), 'index identifies rows and cells');
    check(getComputedStyle(head).display !== 'none' && getComputedStyle(head).visibility !== 'hidden',
          'index headers remain available to assistive technology');
    if (C.mobile) {
      const bounds = head.getBoundingClientRect();
      check(bounds.width <= 1 && bounds.height <= 1 && getComputedStyle(head).clipPath !== 'none',
            'mobile index headers are visually clipped');
      check(Math.abs(table.caption.getBoundingClientRect().width - table.getBoundingClientRect().width) <= 1,
            'mobile index caption spans the card width');
    }
    check(document.getElementById(C.firstAnchor).textContent.includes(C.markup), 'special characters remain text');
    check(!document.querySelector('srk-smoke-injected'), 'report text never creates HTML elements');
    const root = document.documentElement;
    metrics = { innerWidth: window.innerWidth, clientWidth: root.clientWidth,
      scrollWidth: Math.max(root.scrollWidth, document.body.scrollWidth), rowDisplay: getComputedStyle(rows[0]).display };
    check(Math.abs(window.innerWidth - C.width) <= 1, 'requested viewport width');
    check(metrics.rowDisplay === (C.mobile ? 'block' : 'table-row'), 'responsive index layout');
    check(metrics.scrollWidth <= metrics.clientWidth + 1, 'no page horizontal overflow');
    check(window.__scanBrowserErrors.length === 0, 'no browser runtime errors');
  } catch (error) {
    errors.push(String(error.message || error));
  }
  errors.push(...window.__scanBrowserErrors);
  marker.textContent = JSON.stringify({ case: C.case, ok: errors.length === 0,
    checks, errors, metrics }).replace(/</g, '\\u003c');
})();
"""


class ResultParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inside = False
        self.parts = []
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == RESULT_ID:
            self.inside = True
            self.count += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


# Chrome's --remote-debugging-pipe reads FD 3 and writes FD 4. Set these in a
# fresh Python child before exec, avoiding preexec_fn and parent FD mutation.
# Chromium's devtools_pipe_handler.cc specifies NUL-delimited JSON messages.
PIPE_BOOTSTRAP = "import os, sys; os.dup2(0, 3); os.dup2(1, 4); os.execv(sys.argv[1], sys.argv[1:])"


class DevTools:
    """A sequential CDP pipe client with one deadline for the whole case."""

    def __init__(self, proc, timeout):
        self.proc = proc
        self.deadline = time.monotonic() + timeout
        self.timeout = timeout
        self.sequence = 0
        self.buffer = b""
        self.loaded = set()

    def read(self):
        while b"\0" not in self.buffer:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                raise RuntimeError(f"Chrome timed out after {self.timeout:g} seconds")
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Chrome closed its DevTools pipe; see the case's stderr artifact")
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\0", 1)
        message = json.loads(raw)
        if message.get("method") == "Page.lifecycleEvent" and message["params"].get("name") == "load":
            self.loaded.add((message.get("sessionId"), message["params"]["loaderId"]))
        return message

    def call(self, method, params=None, session=None):
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            request["sessionId"] = session
        self.proc.stdin.write(json.dumps(request).encode("utf-8") + b"\0")
        self.proc.stdin.flush()
        while True:
            response = self.read()
            if response.get("id") != request["id"]:
                continue
            if "error" in response:
                raise RuntimeError(f"{method}: {response['error'].get('message', response['error'])}")
            return response.get("result", {})

    def wait_loaded(self, session, loader):
        while (session, loader) not in self.loaded:
            self.read()


def case_name(lang, view, document_view=False):
    return ("document-" if document_view else "") + f"{lang}-{view}"


def make_page(lang, view, document_view=False):
    sample = ROOT / "examples" / ("report.sample.ja.json" if lang == "ja" else "report.sample.json")
    data = json.loads(sample.read_text(encoding="utf-8"))
    # A small, explicit fixture makes natural sorting and field-only search
    # distinguishable from the original sample's already-sorted identifiers.
    ids = ["F-10", "F-2", "F-1", "F-20", "F-99"]
    if len(data["findings"]) != len(ids):
        raise ValueError("browser smoke expects the five-record sample fixture")
    for finding, identifier in zip(data["findings"], ids):
        finding["id"] = identifier
    first = data["findings"][0]
    first["title"] += " — " + MARKUP
    first["location"] = "src/" + "long_unbroken_path_" * 12 + ":42"
    first.setdefault("fields", []).append({"label": "Browser smoke", "text": SEARCH + " </script> " + MARKUP})
    width, _ = VIEWPORTS[view]
    config = {"case": case_name(lang, view, document_view), "lang": lang, "project": data["meta"]["project"],
              "width": width, "mobile": view == "mobile", "markup": MARKUP, "search": SEARCH,
              "included": 4, "excluded": 1, "sortedIds": ["F-1", "F-2", "F-10", "F-20"],
              "firstId": ids[0], "firstAnchor": report.anchor(0), "excludedAnchor": report.anchor(4)}
    source = DOCUMENT_SMOKE_JS if document_view else SMOKE_JS
    script = source.replace("__CONFIG__", json.dumps(config, ensure_ascii=True).replace("<", "\\u003c"), 1)
    harness = ('<script type="application/json" id="' + RESULT_ID + '">{"ok":false,"state":"pending"}</script>\n'
               + "<script>\n" + script + "\n</script>\n")
    page = (report.render_document if document_view else report.render_dashboard)(data, lang)
    return page.replace("</head>", ERROR_CAPTURE + "</head>", 1).replace("</body>", harness + "</body>", 1)


def run_case(chrome, directory, lang, view, timeout, document_view=False):
    case = case_name(lang, view, document_view)
    stem = directory / (case if document_view else "dashboard-" + case)
    artifacts = [stem.with_suffix(suffix) for suffix in (".html", ".dom.html", ".stderr.txt", ".png")]
    bad = output.unsafe_target(directory, [path.name for path in artifacts])
    if bad:
        raise OSError(f"refusing unsafe artifact destination: {bad}")
    # A failed rerun must not leave a previous run's successful screenshots.
    for artifact in artifacts:
        artifact.unlink(missing_ok=True)
    html = stem.with_suffix(".html")
    output.write_output(html, make_page(lang, view, document_view))
    with tempfile.TemporaryDirectory(prefix="scan-report-browser-") as temp, tempfile.TemporaryFile(
            "w+", encoding="utf-8", errors="replace") as err:
        stage = Path(temp)
        width, height = VIEWPORTS[view]
        command = [chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                   "--disable-background-networking", "--force-device-scale-factor=1",
                   f"--window-size={width},{height}", f"--user-data-dir={stage / 'profile'}",
                   "--remote-debugging-pipe", "about:blank"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            command.insert(1, "--no-sandbox")
        proc = subprocess.Popen([sys.executable, "-c", PIPE_BOOTSTRAP, *command],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                start_new_session=True)
        dom = ""
        image_bytes = b""
        destination = stem.with_suffix(".png")
        try:
            cdp = DevTools(proc, timeout)
            target = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
            session = cdp.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            cdp.call("Page.enable", session=session)
            cdp.call("Page.setLifecycleEventsEnabled", {"enabled": True}, session)
            # --window-size alone is clamped to a desktop minimum by Chrome.
            # CDP sets the real layout viewport, including CSS media queries.
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                     "deviceScaleFactor": 1, "mobile": view == "mobile"}, session)
            cdp.call("Page.bringToFront", session=session)
            navigation = cdp.call("Page.navigate", {"url": html.resolve().as_uri()}, session)
            if navigation.get("errorText") or not navigation.get("loaderId"):
                raise RuntimeError(f"Chrome could not load the report page: {navigation}")
            cdp.wait_loaded(session, navigation["loaderId"])
            evaluated = cdp.call("Runtime.evaluate", {"expression":
                "Promise.resolve(window.__scanBrowserSmoke).then("
                "() => document.documentElement.outerHTML, () => document.documentElement.outerHTML)",
                "awaitPromise": True, "returnByValue": True}, session)
            rendered = evaluated.get("result", {}).get("value")
            if not isinstance(rendered, str):
                raise RuntimeError(f"Chrome did not return the live DOM: {evaluated}")
            dom = "<!DOCTYPE html>\n" + rendered
            layout = cdp.call("Page.getLayoutMetrics", session=session)
            size = layout.get("cssContentSize") or layout["contentSize"]
            captured = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True,
                "captureBeyondViewport": True, "clip": {"x": 0, "y": 0, "width": width,
                "height": max(height, math.ceil(size["height"])), "scale": 1}}, session)
            image_bytes = base64.b64decode(captured["data"], validate=True)
            # Capture the full document, including the register and the first
            # finding left expanded by the smoke, before evaluating assertions.
            if output.unsafe_target(directory, [destination.name]):
                raise OSError(f"refusing unsafe screenshot destination: {destination}")
            destination.write_bytes(image_bytes)
        finally:
            status = pdf._stop(proc)
            proc.stdin.close()
            proc.stdout.close()
            err.seek(0)
            output.write_output(stem.with_suffix(".dom.html"), dom)
            output.write_output(stem.with_suffix(".stderr.txt"), err.read())
        if status not in (None, 0):
            raise RuntimeError(f"Chrome exited with status {status}; see {stem.name}.stderr.txt")
        parser = ResultParser()
        parser.feed(dom)
        if parser.count != 1:
            raise RuntimeError("Chrome did not produce exactly one runtime result marker")
        result = json.loads("".join(parser.parts))
        if not isinstance(result, dict) or result.get("case") != case or not isinstance(result.get("checks"), list):
            raise RuntimeError("browser assertions did not complete (result marker is missing or pending)")
        header = image_bytes[:24]
        if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise RuntimeError("Chrome screenshot is not a PNG")
        image_width, image_height = struct.unpack(">II", header[16:24])
        if image_width != width or image_height < 1:
            raise RuntimeError(f"unexpected screenshot dimensions: {image_width} x {image_height}")
        result["screenshot"] = destination.name
        result["screenshotSize"] = {"width": image_width, "height": image_height}
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="directory for DOM, screenshots and summary.json")
    parser.add_argument("--timeout", type=float, default=45, help="real seconds per browser run (default: 45)")
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("the Chrome DevTools pipe check requires Linux or macOS")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive, finite number")
    candidates = [os.environ["CHROME"]] if os.environ.get("CHROME") else pdf.CHROME_CANDIDATES
    chrome = next((found for candidate in candidates if (found := shutil.which(candidate))), None)
    if not chrome:
        parser.error("Chrome/Chromium is required; set CHROME to its executable")
    try:
        args.out.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        parser.exit(2, f"browser check: {error}\n")
    results = []
    cases = ((lang, view, document_view) for document_view in (False, True)
             for lang in ("en", "ja") for view in VIEWPORTS)
    for lang, view, document_view in cases:
        try:
            result = run_case(chrome, args.out, lang, view, args.timeout, document_view)
        except (OSError, ValueError, RuntimeError) as error:
            result = {"case": case_name(lang, view, document_view), "ok": False, "errors": [str(error)]}
        results.append(result)
        print(f"{'PASS' if result.get('ok') is True else 'FAIL'} {result['case']}: "
              + (f"{len(result['checks'])} browser checks" if result.get("ok") is True
                 else "; ".join(result.get("errors", ["unknown failure"]))))
    passed = all(result.get("ok") is True for result in results)
    try:
        output.write_output(args.out / "summary.json", json.dumps({"ok": passed, "cases": results}, indent=2) + "\n")
    except OSError as error:
        parser.exit(2, f"browser check: cannot write summary: {error}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
