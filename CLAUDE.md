# CLAUDE.md

Shared report format (dashboard, printable document, PDF) for the `*-scan`
skill repos. Standard library only — consumers vendor `scan_report_kit/`
into their skill, so no third-party imports, ever.

- `make test` / `make check` before reporting done; `make demo` renders the sample with PDF.
- Design rules live in `docs/design.md`; the JSON contract in `docs/format.md`. Change them with the code.
- After changing `scan_report_kit/`, re-vendor each consumer (`make vendor DEST=<repo>/skills/<name>/scripts`)
  and run that repo's checks; never edit a vendored copy in place (`scripts/vendor.py --check` fails).
- Report data is untrusted: escape in Python (`esc`), use `textContent` in JS.
