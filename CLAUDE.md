# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Shared report format (dashboard, printable document, PDF) for the `*-scan`
skill repos. Standard library only (Python 3.9+) — consumers vendor
`scan_report_kit/` into their skill, so no third-party imports, ever.

## Commands

- `make test` — offline unit tests (`unittest discover -s tests`); also runs
  `tests/dashboard_dom_test.js` when Node 18+ is on PATH (skipped otherwise).
- `make check` — `make test` plus rendering both samples (en/ja) with `--no-pdf`. Run before reporting done.
- `make demo [LANG_OUT=en] [NO_PDF=1] [DEMO_OUT=dir]` — render the sample, with PDF by default.
- Single test: `python3 -m unittest tests.test_report_regressions.ClassName.test_name -v`
- CLI: `python3 -m scan_report_kit report.json --out DIR [--lang ja|en] [--no-pdf]`
  (exit 0 ok, 2 invalid input / output I/O failure, 3 no PDF produced).
- Integration checks (need real Chrome / WeasyPrint and `pdftotext`; CI runs them on Linux):
  `python3 scripts/check_browser.py --out out/browser-check`,
  `python3 scripts/check_pdf.py --engine chrome|weasyprint [--out out/pdf-check]`.
- CI also runs `git diff --check` — no trailing whitespace.

## Architecture

- `report.py` is the core: `validate()` enforces the `scan-report/1` contract
  (`docs/format.md`), raising `FormatError`; `render_document()` builds the
  printable HTML in Python; `render_dashboard()` embeds a JSON payload in
  `<script id="data" type="application/json">` and the browser-side
  `assets/dashboard.js` builds the interactive UI from it. UI strings live in
  `LABELS` (en/ja), overridable per report.
- `style.py` inlines `assets/*.css|js` as strings; `document.css` takes the
  running footer via the `__RUNNING_FOOTER__` placeholder (escaped by `css_string`).
  Outputs are fully self-contained — no remote assets or web fonts.
- `output.py` does atomic writes and staged multi-file updates with rollback;
  `pdf.py` tries headless Chrome then WeasyPrint in a private staging dir
  (`CHROME`, `SCAN_REPORT_PDF_TIMEOUT` env vars). `__main__.py` wires them:
  validate + render both pages before touching any existing output.
- `scripts/vendor.py` copies the package into a consumer with a SHA-256
  `MANIFEST`; `--check` verifies the copy against its own manifest. Requires
  `dir_fd`/`O_NOFOLLOW` support (Linux/macOS only); the package itself does not.
- Tests are mostly regression suites named by area (`test_*_regressions.py`);
  add new edge cases to the matching one.

## Rules

- Design rules live in `docs/design.md`; the JSON contract in `docs/format.md`.
  Change them with the code. README documents user-visible behaviour — keep it in sync too.
- After changing `scan_report_kit/`, re-vendor each consumer (`make vendor DEST=<repo>/skills/<name>/scripts`)
  and run that repo's checks; never edit a vendored copy in place (`scripts/vendor.py --check` fails).
- Report data is untrusted: escape in Python (`esc`), use `textContent` in JS.
  Inside inline `<style>`/`<script>`, `<` must also be escaped (see `css_string` and the dashboard payload).
