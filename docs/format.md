# Common report format (`scan-report/1`)

The JSON that `python3 -m scan_report_kit` and `scan_report_kit.report`
render. A scan that maps its own findings into this shape gets the shared
dashboard, printable report and PDF without writing any rendering code.
`report.validate()` enforces the contract below, returns the original data
without modifying it, and raises `FormatError` for invalid input. Both
`render_document()` and `render_dashboard()` also validate their input; their
`lang` argument must be `en` or `ja`, otherwise they raise `FormatError`.
Text must be valid Unicode without surrogate code points. Required strings
must contain a non-whitespace character. Unknown keys are ignored and are not
copied into the dashboard payload, so extensions cannot change built-in labels
or rendering behavior.

```json
{
  "format": "scan-report/1",
  "meta": {"project": "…", "date": "YYYY-MM-DD", "assessor": "…", "scope": "…", "method": "…", "commit": "…"},
  "report": {"kind": "…", "title": "…", "dashboard_title": "…", "running_footer": "…"},
  "labels": {"…": "…"},
  "summary": "…",
  "findings": [ … ],
  "perspectives": [{"name": "…", "result": "…", "note": "…"}],
  "limitations": ["…"], "decisions": ["…"], "next_steps": ["…"], "checked_ok": ["…"]
}
```

| Key | Required | Meaning |
|---|---|---|
| `format` | yes | Always `scan-report/1`. |
| `meta.project`, `meta.date` | yes | Title-page project and date. The date must be a real calendar date in exact `YYYY-MM-DD` form. The other listed `meta` keys are optional strings. |
| `report` | no | The report's own names: `kind` (masthead / kicker), `title` (document title), `dashboard_title`, `running_footer` (fixed PDF page footer, e.g. `SECURITY ASSESSMENT`). Defaults are generic ("Assessment"). |
| `labels` | no | Overrides for any built-in label in `report.LABELS` (string values). Report names are applied first, then these overrides. See count placeholders below. |
| `summary` | no | Executive summary, set as the abstract and the dashboard banner. |
| `findings` | yes | May be empty. See below. |
| `perspectives` | no | Coverage: what was examined (`name` required) and the result. |
| list keys | no | Lists of strings. |

## Finding

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique, stable reference (e.g. `F-001`). |
| `title` | yes | One line. |
| `severity` | yes | `High`, `Medium`, `Low` or `Info` — the four severity inks of docs/design.md. |
| `status` | yes | `Open`, `Fixed` or `Accepted`. |
| `confidence`, `verdict`, `category`, `location` | no | Free strings, shown in the register (`—` when absent). |
| `excluded` | no | `true` for records kept but not counted (false positives, not applicable). |
| `action` | no | `{"label", "text"}`: the next action for an open finding (e.g. "Fix now" / "Verify first"). |
| `fields` | no | Ordered body sections: `[{"label", "text"}]` — impact, fix, evidence, whatever the scan records. |
| `snippet` | no | `{"start": first line number, "hit": [first, last], "lines": ["…"]}`. Each string in `lines` holds one source line without line breaks. See line numbers below; redact secrets before writing it. |
| `references` | no | `[{"type", "title", "url"}]`; `url` must use http(s), have a nonempty host and, when supplied, a valid numeric port. Whitespace, control characters and backslashes must not appear literally in a URL. Host names also reject forbidden delimiters and malformed percent encodings; internationalized domains, IPv6 and localhost remain supported. `type` and `title` are optional strings. |

Ordering: open before closed, then High → Info; recorded order breaks ties.
Counts cover non-excluded findings only.

The dashboard's ID sort uses the selected report language's collation with
numeric comparison (`F-2` before `F-10`). Search covers IDs, titles, locations,
categories, statuses, severities, confidence, verdicts, action labels and text,
field labels and text, snippet lines, and reference types, titles and URLs.
The excluded-record view keeps the recorded status visible alongside the
exclusion label.

### Line numbers

`start` and both `hit` values are positive integers, not booleans, no larger
than `9007199254740991` (the largest exact integer in JavaScript). Every line
number displayed by the excerpt must also fit within that range. `hit` uses
absolute source line numbers with `first <= last`; the range may extend beyond
the excerpt, and only displayed lines that overlap it are highlighted.

### Count placeholders

`risk_summary`, `open_summary` and their optional `_one` variants substitute
the literal token `{n}`. A `_one` variant takes precedence for a count of one;
English supplies built-in singular variants, so override those too when
customizing both forms. `showing` and `queue_more` substitute `{shown}` and
`{total}`. Repeated known tokens are all replaced. Other braces or tokens are
kept as literal text; Python format expressions are not evaluated.

## What the kit does not do

It does not redact, verify evidence, or decide severity: the producing scan
owns those. Every string is rendered as text (HTML-escaped, or set via
`textContent` in the dashboard). The running footer is escaped as a CSS string
and for its enclosing HTML style element, so report data cannot inject markup
or stylesheet rules. Unknown extension fields are not rendered.
