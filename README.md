# scan-report-kit

The shared dashboard, printable report and PDF format for the `*-scan` skills
(security-scan, infra-scan, performance-scan, privacy-scan, architecture-scan).
Extracted from security-scan so every scan reads the same way: the
"journal of record" design in [docs/design.md](docs/design.md).

Standard library only (Python 3.9+). PDF printing needs headless Chrome/Chromium
or the WeasyPrint CLI. Install a Japanese font such as Noto Sans/Serif CJK JP
when printing Japanese reports on Linux.

## What is in it

| Path | What |
|---|---|
| `scan_report_kit/assets/document.css` | Printable document stylesheet: A4, numbered sections, booktabs tables, severity inks, running footer. |
| `scan_report_kit/assets/dashboard.css` | Dashboard stylesheet: light/dark themes, cards, priority list, coverage, filterable register. |
| `scan_report_kit/assets/dashboard.js` | Dashboard runtime for the common format (filters, search, lazy details, copy-for-LLM, theme toggle). |
| `scan_report_kit/style.py` | The stylesheets and runtime as strings to inline. |
| `scan_report_kit/pdf.py` | `to_pdf()`: Chrome then WeasyPrint, with timeouts and diagnosable failures. |
| `scan_report_kit/output.py` | Atomic single-file writes and staged multi-file updates with rollback. |
| `scan_report_kit/report.py` | Validate and render the common format ([docs/format.md](docs/format.md)). |
| `scripts/vendor.py` | Copy the package into a skill, or check a vendored copy. |

## Use

Render a common-format report directly:

```sh
python3 -m scan_report_kit examples/report.sample.ja.json --out out --lang ja
# out/dashboard.html, out/report.html, out/report.pdf
```

Use `--no-pdf` when only HTML is needed. Exit codes are `0` for success,
`2` for invalid input or output I/O failure, and `3` when no PDF could be
produced. If PDF generation fails, the current HTML remains available to print.

Both public render functions validate their input and raise `report.FormatError`
for invalid data or an unsupported language. See [the format contract](docs/format.md)
for dates, reference URLs, snippets, and label overrides. Report strings are
rendered as text, including the CSS running footer; unknown extension fields
are ignored and are not embedded in the dashboard.

The CLI rejects duplicate JSON object keys and nonstandard `NaN`/`Infinity`
constants before changing any outputs. Both renderers reject NUL characters and
surrogate code points so recorded text cannot silently change during HTML
parsing or UTF-8 encoding. Existing reports remain intact after invalid input.

The dashboard sorts IDs naturally (`F-2` before `F-10`) and searches source
excerpts and references as well as finding text. Excluded records retain their
original status while staying outside the summary counts.

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

Vendoring refuses a destination that overlaps the source package. It prepares
and verifies the new copy before replacing an existing copy, and restores the
old copy if installation fails. If restoration also fails, the diagnostic names
the preserved recovery directory. `--check` rejects edited/missing/extra files,
malformed manifests, symlinks and special files. It checks integrity against the
copy's own manifest; it does not establish that the copy is the latest upstream
version. Verification records each tracked file and directory's identity in
the initial scan, then compares it on subsequent opens and at the end of the
check. Held directory descriptors and component-by-component opens keep reads
attached to the inspected tree without following symlinks. A mismatched
identity is an integrity failure. These checks do not lock the tree against
concurrent writers.

The standalone vendoring script requires directory-descriptor support
(`dir_fd`, `scandir(fd)`, `O_DIRECTORY` and `O_NOFOLLOW`), available on the
supported Linux and macOS Python versions. On other platforms, including
Windows, it stops with a clear error before replacing an existing copy.
This requirement is specific to `scripts/vendor.py`; rendering with the
standard-library package does not depend on these APIs.

## Output and PDF behaviour

The CLI prepares both HTML files before changing existing outputs. If replacing
the HTML files or invalidating the previous PDF fails, it restores the previous
set; recovery files are retained if restoration itself fails. Individual file
replacements are atomic, but the set is not an atomic snapshot for concurrent
readers. Output names that are symlinks or special files, and output paths that
alias the input report, are refused.

New output files are private to their owner (`0600` on POSIX). Existing HTML
permissions are preserved; a PDF regenerated by the CLI is private. The
single-file `write_output()` and `to_pdf()` APIs preserve an existing regular
destination's permission bits without changing the process-wide umask.

PDF engines write to a private staging directory. Failed or timed-out output is
discarded; only a successful output with a PDF header and end marker is
published. This is a truncation check, not a full PDF parser. Engine processes
are reaped, with their process groups also stopped on POSIX. Set `CHROME` to
select a browser executable and `SCAN_REPORT_PDF_TIMEOUT` to a positive finite
number of seconds per engine. Diagnostics can be redacted through `to_pdf()`'s
`redact` callback; redaction happens before text is truncated.

## Make targets

`make help` lists them: `test`, `check`, `demo` (`LANG_OUT=en`, `NO_PDF=1`),
`vendor DEST=…`. Checkout and output paths may contain spaces or single quotes;
invocation through `make -f /path/to/Makefile` also locates the source checkout.

## Development checks

`make test` runs the offline Python tests. When Node.js 18+ is installed, it
also runs the dashboard behaviour suite without npm dependencies. The DOM
adapter tests interaction logic; it does not replace browser layout checks.
`make check` additionally renders the English and Japanese examples and fails
if its temporary directory cannot be created. Set `TMPDIR` to an existing
writable directory when the system temporary directory is unavailable.

GitHub Actions runs the checks on Python 3.9, 3.12 and 3.14 on Linux, and 3.12
on macOS, with Node.js available. A real Chrome check runs on Linux in CI and
can also run locally on macOS. It exercises both languages in the dashboard and
printable HTML at desktop and mobile widths, including filters, sorting, deep
links, focus, escaping, theme changes, source-line highlighting, accessible
table headers, and horizontal overflow. Rendered HTML, the live DOM,
full-page screenshots and a result summary are retained as CI artifacts:

```sh
python3 scripts/check_browser.py --out out/browser-check
```

Separate checks print both languages with real Chrome and WeasyPrint engines
and inspect titles, summaries, finding field text and running footers with
Poppler. CI retains the PDFs, extracted text and rendered pages for inspection:

```sh
python3 scripts/check_pdf.py --engine chrome
python3 scripts/check_pdf.py --engine weasyprint
# Add --out out/pdf-check to retain the generated files locally.
```

These integration checks require `pdftotext` and the selected PDF engine in
addition to the fonts above. PDF engines and inspection tools are development
dependencies; the vendored Python package has no third-party imports.
