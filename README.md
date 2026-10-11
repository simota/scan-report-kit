# scan-report-kit

The shared dashboard, printable report and PDF format for the `*-scan` skills
(security-scan, infra-scan, performance-scan, privacy-scan, architecture-scan).
Extracted from security-scan so every scan reads the same way: the
"journal of record" design in [docs/design.md](docs/design.md).

Standard library only (Python 3.9+). PDF printing needs headless Chrome/Chromium
or WeasyPrint.

## What is in it

| Path | What |
|---|---|
| `scan_report_kit/assets/document.css` | Printable document stylesheet: A4, numbered sections, booktabs tables, severity inks, running footer. |
| `scan_report_kit/assets/dashboard.css` | Dashboard stylesheet: light/dark themes, cards, priority list, coverage, filterable register. |
| `scan_report_kit/assets/dashboard.js` | Dashboard runtime for the common format (filters, search, lazy details, copy-for-LLM, theme toggle). |
| `scan_report_kit/style.py` | The stylesheets and runtime as strings to inline. |
| `scan_report_kit/pdf.py` | `to_pdf()`: Chrome then WeasyPrint, with timeouts and diagnosable failures. |
| `scan_report_kit/output.py` | Safe writes (`write_output`, symlink refusal). |
| `scan_report_kit/report.py` | Validate and render the common format ([docs/format.md](docs/format.md)). |
| `scripts/vendor.py` | Copy the package into a skill, or check a vendored copy. |

## Use

Render a common-format report directly:

```sh
python3 -m scan_report_kit examples/report.sample.ja.json --out out --lang ja
# out/dashboard.html, out/report.html, out/report.pdf
```

Two ways for a scan to adopt it:

1. **Full format** — map findings into [docs/format.md](docs/format.md) and call
   `report.render_dashboard()` / `report.render_document()` / `pdf.to_pdf()`.
2. **Styles + PDF only** — keep your own page structure and inline
   `style.document_css(footer)` / `style.dashboard_css()`, appending only the
   rules for your own record types (security-scan does this).

Skills are installed by symlinking one directory, so they cannot import from
here at run time. Vendor a copy:

```sh
make vendor DEST=../security-scan/skills/security-scan/scripts
python3 scripts/vendor.py DEST --check   # in the consumer's CI: copy not edited
```

Change the format here, then re-vendor into each consumer.

## Make targets

`make help` lists them: `test`, `check`, `demo` (`LANG_OUT=en`, `NO_PDF=1`),
`vendor DEST=…`.
