# Interactive chart library

Charts map validated datasets to a constrained Apache ECharts runtime. The model
supplies data and supported semantics, never JavaScript callbacks or arbitrary
ECharts options.

## Assemble from data

Declare datasets once, then map chart fields to dataset column keys. Reference every
chart at least once from a section `chart` or `views` block. A definition may be reused
in more than one section or comparison; rendered instances receive unique DOM ids.

```json
{
  "datasets": {
    "formats": {
      "columns": [
        {"key": "format", "label": "Format", "type": "text"},
        {"key": "hours", "label": "Viewing hours", "type": "number", "decimals": 1}
      ],
      "rows": [
        {"format": "Series", "hours": 176.7},
        {"format": "Film", "hours": 142.5},
        {"format": "Documentary", "hours": 53.1}
      ]
    }
  },
  "charts": {
    "viewing-by-format": {
      "type": "bar",
      "title": "Viewing time by format",
      "unit": "Viewing hours (millions)",
      "period": "January-June 2026",
      "caption": "Synthetic example only. All three formats shown.",
      "dataset": "formats",
      "category": "format",
      "value": "hours",
      "sort": "descending",
      "limit": 3
    }
  }
}
```

This example illustrates chart mapping only. Add it to a report only when the visual
answers a material question; reports without datasets or charts are valid. Never copy
the example values into a real report.

## Families and options

Every chart requires `type`, `title`, `unit`, `period`, `caption`, `dataset`, and
the text `category` column. Optional common fields are `decimals` (0–6), `domain`,
`notes`, and `limit` (1–500). The accessible values table and visual payload are
generated from the same selected rows.

| Type | Required column mappings | Additional options |
| --- | --- | --- |
| `bar` | Numeric `value` | `sort`; optional text `status` containing explicit semantic values |
| `line` | Numeric `x`, `value` | `x_unit`; optional `low`, `high`, and `x_domain`; actual x spacing is preserved |
| `waterfall` | Numeric `value`, text `kind` | Starts with `total`; later totals reconcile with preceding `change` rows |
| `stacked` | `series`: 1–6 numeric column keys | Optional `normalize: true`; complete nonnegative components required |
| `dumbbell` | Numeric `before`, `after` | Required `before_label` and `after_label`; one missing endpoint is allowed |
| `scatter` | Numeric `x`, `value` | Required `x_unit`; optional `x_domain` |
| `bullet` | Numeric `value`, `target` | Optional text `status` for explicit semantics |
| `variance` | Numeric `value` | `sort`; optional text `status`; diverges around zero |
| `histogram` | Numeric count `value`, `bin_start`, `bin_end` | Bins must be contiguous, ordered, positive-width, and equal-width; use a custom figure for unequal-width density histograms |
| `range` | Numeric `low`, `high` | Optional numeric point `value` inside the range |
| `heatmap` | Text/date/datetime `x_category`, numeric `value` | Repeated category rows form the matrix |
| `timeline` | Numeric/date/datetime `x` | Optional text `detail` |
| `scenario` | `series`: 2–6 numeric column keys | `style`: `line` or `bar` |

For time axes, line and timeline charts accept ISO `date` or `datetime` columns as
well as numeric years, elapsed time, or timestamps with an explicit business unit.
Do not label irregular time observations as equally spaced periods. Numeric x
coordinates are not inferred from row order. Intervals are supplied evidence, never
calculated or invented by the renderer. Explain their meaning and confidence level
in the caption.

The library validates finite numbers, intervals, domains, and waterfall arithmetic.
It cannot verify source analysis, definitions, denominators, or causal claims.
Explicit domains include every plotted value; bar-based plots retain zero. Sign
never determines favorable/unfavorable color. A bar uses semantic color only when
its optional `status` column explicitly supplies `positive`, `negative`, `caution`,
`info`, or `neutral`.

## Useful local interaction

Use a section `views` block:

```json
{"type": "views", "label": "Measure", "default": 0, "views": [
  {"label": "Viewing hours", "chart": "format-hours"},
  {"label": "Completion rate", "chart": "format-completion"}
]}
```

Each referenced chart is complete and independently validated. Use two to five
parallel views with concise labels. Controls apply only to that section. Do not
create a rate/share view without the correct denominator.

Use section-level `filters` when charts and tables share a categorical or temporal
field and the section's narrative remains valid across choices. One selection can
update multiple declared datasets when the same filter column and type exist in each.
Use table `drilldown_from` to open exact records when a reader selects a chart
category. Both interactions include status, reset, and empty-result behavior.

Keep these interactions local by default. A global filter is acceptable only when
every affected headline, denominator, annotation, and conclusion can update from the
same validated state.

## Flexibility beyond the library

- Use complementary plots or separate sections when the evidence needs them.
  Renderer limits are resource limits, not section quotas: at most 500 rows per
  chart, six stack components, and five local views.
- Dense category charts show ten rows first below 576px and provide “show all.”
- When none of the supported families can communicate the evidence, create a
  specialized static visual with an appropriate trusted plotting or image tool,
  inspect the output, and embed it through the structured `figure` block. The
  figure contract accepts only a bounded PNG/JPEG with a title, alt text, and
  caption. Pair quantitative figures with a structured table containing the
  exact validated values.
- Use a reviewed package extension only when the report genuinely requires new
  reusable or interactive behavior. Such an extension needs schema, controller,
  security, and browser tests. Report content cannot add code, ECharts options,
  HTML, CSS, JavaScript, or raw SVG.
- The packaged runtime uses ECharts' SVG renderer, ARIA descriptions, direct
  values, and a same-data accessible table. Add patterns only when labels, signs,
  and shapes cannot distinguish explicitly meaningful series. It does not choose
  the business story or annotate causality.

For routine reports, inspect each generated plot at one representative desktop width
and one narrow mobile width. Use 370, 576, 768, 992, and 1440 pixels for package
changes, themes, or unusually dense reports. Check small values, precision, truncated
labels, intervals, legends, semantic colors, tooltips, focus, filters, drill-down, and
“show all” behavior. Split the chart or choose a better visual when overloaded.
