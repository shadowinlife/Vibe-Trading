# Configurable report design

Use one component system and security boundary across reports. Select an appropriate
composition and validated theme within that system; the information architecture is
not fixed to a single executive layout.

## Composition modes

The top-level `composition` is one of `standard`, `executive-brief`,
`analytical-narrative`, or `operational-review`. The renderer applies bounded spacing and layout adjustments,
while the author still decides what evidence belongs in each section.

- `standard`: neutral, purpose-adaptive structure with no presumed opening or closing.
- `executive-brief`: compact, decision-led, and often supported by an opening.
- `analytical-narrative`: evidence-led chapters that can open with a conclusion,
  neutral context, or unresolved question.
- `operational-review`: current-state and exception-oriented, with filters and detail
  close to the evidence they affect.

Select the mode automatically. Ask the user only when two purposes are genuinely
ambiguous and the choice would materially alter emphasis.

## Theme contract

`theme.json` contains the packaged default. It is validated against
`theme.schema.json` plus runtime contrast checks in `scripts/theme.py`.

Use `--theme path/to/custom-theme.json` when the user supplies a theme or brand
guidance. The agent may translate brand guidance into a new validated theme without
asking for design approval. A theme may configure:

- `mode`: `light` or `dark`.
- Primary, secondary, neutral, and semantic colors as six-digit hex values.
- Ordered font-family fallbacks for text and numbers.
- Content width, reading width, gutter, section spacing, and corner radius within
  bounded ranges.

The renderer rejects missing or unknown fields, unsafe font-family names,
out-of-range dimensions, and essential foreground/background combinations that
do not meet the required contrast. `theme.schema.json` supports editor feedback;
the Python validator remains authoritative because JSON Schema does not calculate
color contrast.

The package does not redistribute font binaries and its CSP sets `font-src
'none'`. Use installed and appropriately licensed fonts with portable fallbacks.
Do not fetch or embed a font during report creation.

Theme files supply tokens, not executable styling. Do not add raw CSS, JavaScript,
HTML, logos, URLs, chart options, or content to the theme contract. Add a bounded
embedded PNG/JPEG logo through `meta.logo`. Changes to the
fixed component structure belong in reviewed package code with regression tests.

## Visual grammar

- Use the theme's light or dark surfaces, a narrow primary rule, compact context,
  and high-contrast body copy. Keep prose within the configured reading width and
  use the configured content width for the overall canvas.
- When an opening band is present, place its optional context, headline or neutral
  question, summary, and optional metrics in that order. At
  992px and wider, a substantial summary may use two balanced columns. Below 992px,
  keep every element in one column. Without an opening block, use the report title
  as the sole `h1` and move directly to navigation and evidence.
- Sections use a required title and an optional eyebrow. Number an eyebrow only when
  order or sequence helps the reader. A section can be prose-led, table-led, visual,
  chronological, or mixed. Use a visual focal point only when the evidence benefits
  from one; qualitative sections may use structured text without a chart.
- At 992px and wider, pair each section title with its introduction across the
  page. Group consecutive narrative and callout blocks into a balanced two-column
  prose row when both blocks are independently readable, while charts, controlled figures, tables, explorers, and detail
  disclosures always span the full canvas. A final unpaired prose block returns to
  the reading width. Linearize the complete section below 992px so source and
  visual reading order remain identical.
- Generate section navigation from the authored section order only when there is more
  than one section. Each navigated section
  has a unique one-to-six-word `navigation_label`, at most 72 characters, that describes its subject.
  Never use generic labels such as "Section 1," "Finding 2," or "Chapter 3."
  Keep navigation horizontally scrollable at narrow widths rather than wrapping
  into a tall menu. Use the complete section heading as the link's accessible name.
- Separate sections with whitespace and thin rules. Reserve the primary-soft token
  for a specific priority or annotation; do not put every section in a card.
- Maintain a strict descending hierarchy: 32px/27px opening `h1`, 26px/23px
  section `h2`, 20px/19px subsection `h3`, 17px nested `h4`, 15px body, and 12px
  supporting text at desktop/mobile sizes. Do not skip heading levels. Use the
  high-contrast ink token for headings and primary colors for labels, links, rules, and data emphasis.
  Letter spacing is zero.
- Ordinary data uses primary and neutral colors. Semantic positive, negative, or
  caution colors appear only when the model supplies explicit meaning. Pair them
  with labels, signs, shapes, or ECharts decals.
- Keep category charts near 30-38px per visible row. At 370px, the renderer may fold
  a dense chart to ten initial rows and provide a "show all" control; this is responsive
  disclosure, not permission to omit categories from the analytical population. Do not
  shrink labels into an unreadable fixed canvas.
- On small viewports, retain the table structure in a bounded two-axis scroll
  region so long record lists do not dominate the page. On larger viewports, use
  50px rows, numeric alignment, no vertical rules, and concise headers. Put long
  supporting tables in collapsed detail at every viewport.
- Keyboard focus is visible. Controls work by keyboard and tap. Motion is brief
  and respects reduced motion. Essential values do not depend on tooltips.

## Theme verification

Run both commands after changing theme tokens:

```bash
python -B scripts/render_html_report.py examples/streaming-audience-fixture.json report.html --theme theme.json --check
python -B -m unittest discover -s tests -v
```

For a routine report, inspect at least one desktop and one narrow mobile width. For
a public theme, package change, or unusually dense report, run the browser suite and
inspect 370, 576, 768, 992, and 1440 pixels. Automated contrast checks cover essential
token pairs, but they do not replace visual review of charts, focus states, long
labels, and screenshot output.
