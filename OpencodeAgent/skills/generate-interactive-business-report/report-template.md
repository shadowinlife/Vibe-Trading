# Structured report composition

Read with `report-specification.md` when authoring the report specification. The
renderer, not the specification author, creates HTML components and identifiers.

## Build sequence

1. Curate the story and validated result values before writing the specification.
2. Define reusable datasets with typed columns and exact rows.
3. Map charts and tables to those datasets rather than copying values into each
  component.
4. Compose ordered sections from typed blocks. Reference every chart at least once;
  reuse chart definitions when the same validated view serves more than one section.
5. Run `python -B "<skill-dir>/scripts/render_html_report.py" report-specification.json report.html`.
  The renderer validates the specification, generates markup, embeds CSS and scripts, and
  creates the CSP.
6. Render the output, inspect it, exercise controls, and fix the artifact. A syntactically valid file can still communicate poorly.

## Scaling the shell

Use one section per material question or tightly related evidence group; merge sections
that do not answer distinct reader needs. Select `executive-brief`,
`analytical-narrative`, or `operational-review` only when its emphasis helps; otherwise
use `standard`. `opening` is optional. When used, its headline may describe mixed
results, a neutral monitoring state, or an unresolved question; it does not require a
definitive answer. Use up to six short summary paragraphs for qualifications and
tradeoffs. Omitted metrics, closing content, and supporting detail are normal. Never
invent content to populate them.

## Ruled metric strip

Use `opening.metrics` only when a small set of validated measures materially improves
orientation. Label the period or baseline in `detail` when the value could be
ambiguous. Do not add a metric strip merely because the schema supports one, and avoid
cards that repeat values already communicated clearly.

```json
{"label": "90-day retention", "value": "72.4%", "detail": "+2.6 points vs January", "tone": "positive"}
```

## Section

Each section supplies a `title`, optional `eyebrow`, optional `intro`,
optional descriptive `navigation_label`, optional section `filters`, and typed blocks.
Use an eyebrow only when it adds useful context; number it only when sequence matters.
For reports with multiple sections, use a unique one-to-six-word navigation label of at
most 72 characters, never a generic numbered label. Put observations,
interpretation, and caveats in separate blocks when that improves scanning.
The renderer groups consecutive paragraph, insight, and callout blocks into an
adaptive editorial grid. Evidence components interrupt that group and retain the
full report width; no author-supplied layout flags are needed.

```json
{
  "id": "release-completion",
  "eyebrow": "Release evidence",
  "navigation_label": "Release completion",
  "title": "Documentaries complete more often despite lower total reach",
  "blocks": [
    {"type": "chart", "chart": "completion-by-format"},
    {"type": "insight", "label": "Meaning.", "text": "Reach and depth answer different programming questions."}
  ]
}
```

## Rich paragraphs and lists

Keep ordinary prose as a string. Use typed inline nodes only when a paragraph
needs meaningful emphasis or an auditable source link. Do not fragment every
sentence into nodes.

```json
{
  "type": "paragraph",
  "text": [
    {"type": "text", "text": "The gain is concentrated in "},
    {"type": "strong", "text": "three flagship releases"},
    {"type": "text", "text": "; it is not yet broad-based. "},
    {"type": "citation", "label": "1", "href": "https://example.com/source", "title": "Published audience methodology"}
  ]
}
```

Use a list when sequence, criteria, or distinct exceptions matter. Choose
`ordered` only when order or priority carries meaning.

```json
{
  "type": "list",
  "style": "ordered",
  "items": [
    "Reconcile the three largest release totals.",
    [
      {"type": "text", "text": "Validate the "},
      {"type": "emphasis", "text": "current-period"},
      {"type": "text", "text": " denominator before publishing the rate."}
    ]
  ]
}
```

## Subsection inside a section

Use a subsection to separate distinct parts of one material question without
creating another top-level navigation item. Only one subsection level is allowed.

```json
{
  "type": "subsection",
  "title": "Concentration risk",
  "intro": "The total depends on a narrow set of releases.",
  "blocks": [
    {"type": "chart", "chart": "release-concentration"},
    {"type": "insight", "label": "Implication.", "text": "The total is sensitive to three flagship releases."}
  ]
}
```

## Controlled custom figure

Use `figure` for a specialized static visual—such as a map, annotated process,
network, or statistical diagnostic—that the chart library cannot express. Embed
only a validated PNG or JPEG and include a meaningful title, alt text, and
caption. Pair quantitative figures with a structured exact-values table.

```json
{
  "type": "figure",
  "title": "Premiere milestones cluster before the summer window",
  "image": {"mime_type": "image/png", "base64": "<base64 PNG bytes>"},
  "alt": "Timeline with three premiere milestones clustered before the summer window.",
  "caption": "The custom timeline uses the validated release calendar; dates are illustrative."
}
```

Use an `image` block for ordinary editorial imagery; its `title` and `caption` are
optional and `width` is `reading` or `wide`. Use `meta.logo` for a bounded brand
mark. Generate the validated embedded object with
`python -B "<skill-dir>/scripts/encode_image.py" path/to/image.png --pretty`.

## Working comparison switcher

Use a `views` block for two to five complete chart comparisons at the same
information-hierarchy level. The default is a zero-based index. Each choice has
its own units, caption, denominator, and chart id.

```json
{"type": "views", "label": "Measure", "default": 0, "views": [
  {"label": "Viewing hours", "chart": "release-hours"},
  {"label": "Completion rate", "chart": "release-completion"}
]}
```

The builder creates the controls, selected-state feedback, panels, and reset.
Never add a choice whose data or denominator is unavailable.

## Searchable and sortable supporting table

Use a `table` block. Keep inline tables short and set `display: "detail"` for full
record lists that support a chart. Numeric sort values and mobile labels are
generated from the dataset schema.

```json
{
  "type": "table",
  "dataset": "releases",
  "title": "Release detail",
  "columns": ["release", "viewing_hours", "completion_pct"],
  "sort": {"key": "viewing_hours", "direction": "descending"},
  "limit": 100,
  "search": true,
  "display": "detail",
  "summary": "Explore release detail"
}
```

Add `drilldown_from` when a chart selection should open and narrow the table:

```json
"drilldown_from": {"chart": "release-hours", "column": "release"}
```

## Linked section filters

Declare filters on a section when multiple charts or tables should respond to one
categorical or temporal selection:

```json
"filters": [
  {"id": "format", "label": "Release format", "column": "format", "datasets": ["releases"]}
]
```

Keep filters local unless all affected headlines and interpretations can update consistently. The
builder supplies status, reset, and empty-result behavior.

## Evidence fallback

Keep the main narrative, chart summary, and exact values in the document rather than creating them only in JavaScript. Controls start hidden and are exposed by the controller, so a browser without JavaScript receives evidence rather than unusable controls. Every alternate view needs its own scope label and validated data.

Use typed `link` and `citation` nodes for ordinary HTTPS sources. The reader must
not need to open one to understand a section. `security-notes.md` states what the
builder rejects and how to extend the grammar.
