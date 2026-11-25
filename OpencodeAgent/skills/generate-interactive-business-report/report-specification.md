# Report specification

The renderer accepts a structured JSON object, not authored HTML. Unknown keys,
missing required keys, invalid references, and pre-escaped HTML entities are hard
errors. See [examples/minimal-reference-fixture.json](examples/minimal-reference-fixture.json)
for a minimal report and
[examples/streaming-audience-fixture.json](examples/streaming-audience-fixture.json)
for a capability-rich synthetic report. Neither example defines a required shape.

## Top level

```json
{
  "meta": {
    "title": "Report title",
    "scope": "Population or boundary",
    "source": "Evidence source"
  },
  "sections": [
    {"id": "overview", "title": "Overview", "blocks": [{"type": "paragraph", "text": "Report content."}]}
  ]
}
```

`composition`, `opening`, `datasets`, `charts`, `closing`, and `supporting` are
optional. Omit them when the report does not need them. `composition` defaults to
`standard` and also accepts `executive-brief`, `analytical-narrative`, or
`operational-review`. Display strings are raw text or the explicit rich-text structure
documented below. Write `A & B`, never
`A &amp; B`; the renderer escapes all content.

## Metadata and optional opening

`meta` requires `title`, `scope`, and `source`; `period`, `freshness`, `language`,
`logo`, and `ui_labels` are optional. Include `period` when evidence is time-bound.
`language` is a BCP 47-style tag such as `en-US`. `logo` contains an embedded `image`
object and meaningful `alt` text.

`ui_labels` replaces the renderer's English interface copy. Use it whenever the
report language is not English so skip links, controls, empty states, accessibility
labels, and dynamic status messages use the same language as the authored content.
Partial overrides are accepted, but a fully localized report should translate every
key in the following groups:

- Shell: `skip_to_report`, `report_sections`, `jump_to`, `supporting_detail`.
- Filters and views: `all_filter`, `reset_filters`, `filters_applied`,
  `showing_all_section_data`, `view`, `reset_view`, `showing_view`.
- Tables and drill-down: `find_record`, `reset_table`, `clear_drilldown`,
  `rows_shown`, `no_matching_rows`, `showing_details`, `not_available`,
  `observation`, `target`, `lower_bound`, `upper_bound`, `step`,
  `date_or_position`, `detail`, `column`.
- Charts: `chart_values`, `chart_values_region`, `chart_unavailable`,
  `no_filter_observations`, `show_all_rows`, `show_fewer_rows`, `stack_order`,
  `stack_order_normalized`, `missing_observations`, `before_after_order`,
  `observed`, `actual`.

Preserve every named placeholder in a translated value. For example:

```json
"ui_labels": {
  "all_filter": "Toutes les valeurs de {label}",
  "filters_applied": "Filtres appliqués : {count}.",
  "rows_shown": "{visible} lignes affichées sur {total}",
  "showing_details": "Détails affichés pour {value}."
}
```

`opening` is optional. Use it only when readers need synthesis or orientation beyond
the title and first section. It requires `headline`; `context`, `summary`, and
`metrics` are optional. The headline may be a conclusion, neutral orientation, current
status, or unresolved question. `summary` may be one plain string or a list of 1–6
paragraphs. Each paragraph may be plain text or a rich-text node list; when using rich
text, wrap each paragraph's node list inside the outer summary list. Optional `metrics` contain `label`, `value`,
`detail`, and an optional `tone`: `neutral`, `positive`, `negative`, `caution`, or
`info`. A tone is semantic and must be chosen from the evidence; the builder never
assumes that a positive number is favorable.

## Rich text

`paragraph.text`, `insight.text`, `callout.text`, subsection and section intros,
list items, figure captions, closing-item text, definition text, and opening summary
paragraphs accept either a plain string or a list of typed inline nodes. The
allowed nodes are:

- `text`: `text`
- `strong`: `text`; use for a short decision-relevant phrase, not a whole paragraph
- `emphasis`: `text`; use sparingly for qualification or contrast
- `link`: `text`, `href`; `href` must be an explicit HTTPS URL
- `citation`: `label`, `href`, optional `title`; rendered as a compact source marker

```json
[
  {"type": "text", "text": "Viewing increased, but "},
  {"type": "strong", "text": "completion did not"},
  {"type": "text", "text": ". The source method is available "},
  {"type": "link", "text": "from the publisher", "href": "https://example.com/method"},
  {"type": "citation", "label": "1", "href": "https://example.com/source", "title": "Publisher dataset"}
]
```

Rich text is intentionally inline only. It cannot contain HTML, images, nested
formatting, arbitrary attributes, local files, or executable URLs. A citation is
a source link, not evidence by itself; keep enough source identity, period, and
scope in the surrounding sentence or `title` to make the reference auditable.

## Datasets

Omit `datasets` when the report has no structured data. Otherwise each key is a short
identifier and declares `columns` and `rows`:

```json
"releases": {
  "columns": [
    {"key": "title", "label": "Title", "type": "text"},
    {"key": "premiere", "label": "Premiere", "type": "date"},
    {"key": "budget", "label": "Budget", "type": "currency", "currency": "USD", "decimals": 0},
    {"key": "runtime", "label": "Runtime", "type": "duration", "duration_unit": "minutes", "decimals": 0},
    {"key": "episodes", "label": "Episodes", "type": "integer"},
    {"key": "renewed", "label": "Renewed", "type": "boolean", "true_label": "Renewed", "false_label": "Pending"}
  ],
  "rows": [
    {"title": "Midnight Atlas", "premiere": "2026-01-15", "budget": 1200000, "runtime": 48, "episodes": 8, "renewed": true}
  ]
}
```

Column types are `text`, `number`, `integer`, `percent`, `currency`, `date`,
`datetime`, `duration`, and `boolean`. Numeric columns may set `decimals`, `prefix`,
and `suffix`; currency requires a three-letter uppercase `currency` code; duration
requires `duration_unit`; boolean columns may set `true_label` and `false_label`.
Date and datetime values use ISO 8601 text. Every row supplies exactly the declared keys;
`null` represents a missing value. A dataset can feed several charts and tables.

## Sections and blocks

Each section requires a unique `id`, `title`, and nonempty `blocks`.
`eyebrow`, `intro`, `navigation_label`, and `filters` are optional. Use an eyebrow only
when it adds context, and number it only when order or sequence matters. Navigation is
rendered only for reports with more than one section. When supplied,
`navigation_label` must be a unique one-to-six-word description no longer than 72
characters rather than a generic numbered label. The
builder uses `navigation_label` as the visible navigation text and keeps the complete section title
as the link's accessible name. Without it, the complete title is used. The builder
creates navigation and heading relationships.

Supported blocks:

- `paragraph`: rich `text`
- `insight`: `label`, rich `text`
- `callout`: `title`, rich `text`, optional semantic `tone`
- `list`: 1–50 rich-text `items`, optional `style` (`unordered` or `ordered`)
- `subsection`: `title`, nonempty `blocks`, and optional rich `intro`
- `figure`: `title`, embedded `image`, `alt`, and rich `caption`
- `image`: embedded `image`, `alt`, optional `title`, rich `caption`, and `width`
  (`reading` or `wide`)
- `chart`: one `chart` id
- `views`: 2–5 `{label, chart}` choices, optional `label` and zero-based `default`
- `table`: `dataset`, `title`, optional `columns`, `limit`, `search`, `sort`,
  `display` (`inline` or `detail`), `summary`, and `drilldown_from`
- `definitions`: `title`, `{term, definition}` items, and optional `display`

A subsection creates an `h3` beneath the section's `h2`; headings inside it use
`h4`. Subsections may contain any block, including charts and tables, but cannot
contain another subsection. Every chart definition must appear at least once,
directly, inside a subsection, or inside a `views` block. A chart definition may be
reused; the renderer gives each rendered instance a unique DOM id. Tables may reuse a dataset. Put
decision-changing evidence in a section; use `supporting` for definitions and
secondary lookup detail.

## Optional closing list

`closing` is optional and contains an authored `title` plus 1–20 `items`. Each item has
`label` and rich `text`. Use it for supported actions, monitoring questions, decisions,
or unresolved work only when a closing list helps the report. Its title should name the
actual purpose; there is no default "Next steps" heading.

```json
"closing": {
  "title": "Questions to resolve",
  "items": [
    {"label": "Coverage", "text": "Confirm whether the missing region changes the comparison."}
  ]
}
```

## Embedded images and controlled custom figures

Use `image` for an editorial or contextual image that is not a quantitative custom
visual. Its title and caption are optional, and its width is `reading` by default.
Use `meta.logo` for a compact brand mark. Both use the same validated `image` object.

Convert a local PNG/JPEG into that object with:

```bash
python -B "<skill-dir>/scripts/encode_image.py" path/to/image.png --pretty
```

Use `figure` when a specialized visual communicates validated evidence better
than the packaged chart families. The image must be a PNG or JPEG encoded directly
in the specification, no larger than 5 MB after decoding, 10,000 pixels per side,
or 40 megapixels overall:

```json
{
  "type": "figure",
  "title": "Release cadence accelerates before the summer premiere window",
  "image": {"mime_type": "image/png", "base64": "<base64 PNG bytes>"},
  "alt": "Timeline showing release milestones clustered before the summer premiere window.",
  "caption": [
    {"type": "text", "text": "Milestones reflect the validated release calendar. "},
    {"type": "citation", "label": "1", "href": "https://example.com/source", "title": "Release-calendar methodology"}
  ]
}
```

The builder verifies the encoding and file signature, embeds the image, and blocks
SVG, external images, paths, and scripts. Create the image with a trusted plotting
or image tool, inspect it before embedding, and keep labels readable at narrow
widths. When a custom figure represents quantitative values, follow it with a
structured `table` block from the validated dataset so exact values remain
accessible. Do not use `figure` to bypass an available structured chart merely to
gain cosmetic control.

## Tables

`sort` is `{ "key": "column-key", "direction": "ascending" }` or
`descending`. Set `search` only when the table has enough rows to benefit. Use
`display: "detail"` for a collapsed full-detail table and give it a concise
`summary`. At small viewports, tables remain tabular inside a bounded scroll region.

To link a table to a chart click, add:

```json
"drilldown_from": {"chart": "release-hours", "column": "release"}
```

The chart and table must use the same dataset, and the drill-down column must match
the chart category. The table opens and narrows to the selected category; a reset
restores the prior section-filtered state.

## Section filters

A section can declare one to eight filters. Each filter targets a column shared by
one to ten linked datasets. Supported filter columns are text, date, datetime, and
boolean. Charts and tables in the section that use a listed dataset update together.

```json
"filters": [
  {"id": "format", "label": "Release format", "column": "format", "datasets": ["releases"]}
]
```

Use section filters when the narrative interpretation remains valid across the available
choices. Do not simulate a global report filter unless all affected headlines,
counts, denominators, and annotations can update consistently.

## Charts

Omit `charts` when no visual encoding materially improves the report. Every chart that
is included requires `type`, `title`, `unit`, `period`, `caption`, `dataset`, and
`category`. Common optional keys are `decimals`, `notes`, `domain`, and `limit`.
The chart families and their field mappings are documented in
[chart-library.md](chart-library.md).

Chart data is encoded into a bounded data payload and rendered by the packaged
ECharts runtime. The model cannot supply callbacks, HTML formatters, URLs,
toolbox features, transforms, CSS, JavaScript, or arbitrary ECharts options.
