# Common report format (`scan-report/1`)

The JSON that `python3 -m scan_report_kit` and `scan_report_kit.report`
render. A scan that maps its own findings into this shape gets the shared
dashboard, printable report and PDF without writing any rendering code.
`report.validate()` enforces everything below and raises `FormatError`.

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
| `meta.project`, `meta.date` | yes | Title-page project and date. Other `meta` keys are optional strings. |
| `report` | no | The report's own names: `kind` (masthead / kicker), `title` (document title), `dashboard_title`, `running_footer` (fixed PDF page footer, e.g. `SECURITY ASSESSMENT`). Defaults are generic ("Assessment"). |
| `labels` | no | Overrides for any built-in label in `report.LABELS` (string values). |
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
| `snippet` | no | `{"start": first line number, "hit": [first, last], "lines": ["…"]}` — redact secrets before writing it. |
| `references` | no | `[{"type", "title", "url"}]`; `url` must be http(s). |

Ordering: open before closed, then High → Info; recorded order breaks ties.
Counts cover non-excluded findings only.

## What the kit does not do

It does not redact, verify evidence, or decide severity: the producing scan
owns those. Every string is rendered as text (HTML-escaped, or set via
`textContent` in the dashboard), so report data cannot inject markup.
