---
name: generate-interactive-business-report
description: Generate or revise a secure, interactive standalone HTML business report from completed analysis or validated data. Use when the user explicitly asks to create, build, generate, or revise an interactive or standalone HTML business report, even if they do not name this skill. Do not trigger for routine analysis, chat summaries, raw data exports, follow-up questions, PDF, PPTX, CSV, or conversation completion alone.
---

# Generate interactive business report

Turn the available analysis into a business report that explains the questions examined, what the evidence establishes, why it matters, and what remains unresolved. Adapt the narrative to descriptive, diagnostic, comparative, exploratory, forecast, or scenario work. Communication quality, material coverage, and analytical accuracy determine whether the report is finished.

Curate the story first, following [narrative-guide.md](narrative-guide.md). The steps below cover building and verifying the artifact.

## Activation and defaults

- Select this skill automatically for an explicit natural-language request to create or revise an interactive HTML business report. Each request covers that report work only; later analysis does not automatically regenerate it.
- Default to the user's language, standalone HTML, the packaged accessible theme, and a neutral `standard` composition. When the audience is not stated, write for mixed stakeholders without assuming they are executives. Honor a stated audience such as operators, analysts, boards, clients, regulators, or technical reviewers. For a non-English report, set `meta.language` and translate every `meta.ui_labels` entry so controls and accessibility text match the authored content.
- Select `executive-brief`, `analytical-narrative`, or `operational-review` only when the purpose clearly benefits from that emphasis; otherwise retain `standard`. Ask one purpose question only when two modes are genuinely plausible and would produce materially different emphasis.
- Use polished, professional language appropriate to the selected audience. Apply the writing standard in [narrative-guide.md](narrative-guide.md) to every visible string without forcing executive terminology into exploratory, monitoring, reference, or technical reports.
- Proceed directly from the conversation to the finished artifact. Do not generate style alternatives or ask for template approval.
- Honor supplied scope, audience, terminology, and format. Ask only when unavailable information materially changes the report's accuracy or interpretation. Use available context and disclose a gap when it can be handled without a question.

## Choose the report shape

Determine structure from the material questions and validated evidence before authoring the specification. Examples demonstrate capabilities, not a preferred outline. Do not target a section count, chart count, amount of interaction, or set of optional regions. A valid report can be one section of prose; a broad report can contain many sections and several evidence forms.

Use an optional element only when it performs a distinct job:

| Element | Include when |
| --- | --- |
| `opening` | Readers need synthesis or orientation that the title and first section cannot provide. |
| `opening.metrics` | A small set of validated, decision-relevant measures improves orientation. Do not create a KPI strip merely because values exist. |
| Additional section | It answers a distinct material question or separates evidence that would otherwise become confusing. Merge overlapping sections. |
| Chart or figure | Visual encoding reveals a comparison, trend, distribution, relationship, geography, process, or uncertainty more clearly than text or a table. |
| Interactive control | A reader needs to inspect a validated alternative view, scope, or record set; a static presentation would be materially less useful. |
| `closing` | The evidence supports actions, monitoring questions, unresolved decisions, or another useful closing list. Author its title to match that purpose. |
| `supporting` | Secondary definitions, methods, or lookup detail improve auditability without displacing material evidence from the main sections. |

Absence is intentional, not an incomplete template. Never invent content to fill an optional element. Use statement headings, neutral topic labels, current-status labels, or questions according to what the evidence supports; do not force every section into a finding or conclusion.

## 1. Build the artifact

HTML is rendered by `scripts/render_html_report.py`. Author a report specification following [report-specification.md](report-specification.md). The renderer validates plain text, datasets, content blocks, chart mappings, cross-references, and theme tokens; fills [report-shell.html](report-shell.html); embeds the packaged report CSS, ECharts runtime, and controllers; and adds a restrictive Content Security Policy. Do not hand-assemble the HTML or supply raw HTML, CSS, JavaScript, callbacks, or ECharts options.

Three files are required reading before you compose: [narrative-guide.md](narrative-guide.md) for what goes into the report and in what order, [report-design.md](report-design.md) for the visual system and theme contract, and [report-specification.md](report-specification.md) for the input contract. Read the others only when this report needs them.

| Read | When |
| --- | --- |
| [report-specification.md](report-specification.md) | Authoring datasets, sections, content blocks, tables, and charts |
| [report-template.md](report-template.md) | Composing sections, optional openings, metric strips, view switchers, or supporting tables |
| [chart-library.md](chart-library.md) | Writing `CHARTS` specifications |
| [visualization-patterns.md](visualization-patterns.md) | Choosing between visual forms for a question |
| [security-notes.md](security-notes.md) | A fragment was rejected, or you are extending the package |

### Where the files go

This skill runs from the user's working directory, not from the skill folder. Resolve the skill directory once (the folder holding this `SKILL.md`) and invoke the builder by absolute path; it finds its own packaged resources from any current directory.

```bash
python -B "<skill-dir>/scripts/render_html_report.py" report-specification.json report.html
```

When the user provides a compatible theme file, add `--theme`:

```bash
python -B "<skill-dir>/scripts/render_html_report.py" report-specification.json report.html --theme custom-theme.json
```

Never generate arbitrary CSS from a prompt. Translate supplied brand guidance into
the validated JSON tokens documented in [report-design.md](report-design.md), choose
light or dark mode as appropriate, and run `--check` before building with a new
theme. Add a logo through `meta.logo`, not through theme CSS.

Python 3.10+ and the standard library are sufficient. Write
`report-specification.json` to a temporary working directory: it is a build input,
not a deliverable. Write the report where the user asked; absent instruction,
write it into their working directory and return that path.

### Report specification

The input is one JSON object with required `meta` and `sections`.
`composition`, `opening`, `datasets`, `charts`, `closing`, and `supporting` are optional. Charts and
tables reference a dataset by id so the builder can guarantee that both use the
same rows and values. Navigation is generated only when the report has more than one
section. In that case, give each section a unique, descriptive `navigation_label` of
at most 72 characters and one to six words; never use generic labels such as
"Section 1" or "Finding 2." Section eyebrows are optional and need not be numbered.

Start with [examples/minimal-reference-fixture.json](examples/minimal-reference-fixture.json)
to see the smallest complete shape. It intentionally omits an opening, datasets,
charts, navigation, controls, closing list, and supporting detail.

Use [examples/streaming-audience-fixture.json](examples/streaming-audience-fixture.json)
only as a capability-rich synthetic example. It demonstrates metrics, charts,
semantic status, interval data, section filters, chart-to-table drill-down,
searchable detail, a closing list, and definitions. Its number and order of sections
are not a template. Replace every illustrative name and value with validated evidence.

Use [examples/flexibility-fixture.json](examples/flexibility-fixture.json) when a
report needs rich paragraphs, lists, citations, subsections, or a controlled
embedded image. It demonstrates a one-section report without an opening, closing,
or supporting region. It is synthetic and must not be copied as business evidence.

Use the data-driven families in [chart-library.md](chart-library.md) when they suit the analytical question. Compose complementary charts and local comparison views as needed; the library does not impose a report layout, section count, or visual quota. The model accepts supported chart semantics, never arbitrary library configuration.

Create charts when they improve understanding of the validated results; use
structured paragraphs, lists, subsections, comparisons, or timelines when better
suited to the evidence. The rich-text grammar supports controlled emphasis, HTTPS
links, and inline citations without accepting HTML. Use these features where they
improve hierarchy or provenance, not as decoration.

For ordinary editorial imagery, add a validated `image` block. For a bounded brand
mark, use `meta.logo`. Run `scripts/encode_image.py path/to/image.png --pretty` to
validate a local PNG/JPEG and produce the exact embeddable object. Images are always
embedded in the HTML; external URLs and file paths are not report content.

If the packaged chart families cannot express material evidence, the agent may
generate a custom static visual with an appropriate trusted plotting or image tool
and embed it through the controlled `figure` block. Use this route for specialized
maps, networks, annotated processes, or statistical diagnostics—not to bypass the
structured charts for cosmetic control. The figure must be an inspected PNG or
JPEG within the schema limit, with an evidence-based title, meaningful alt text,
and a caption covering source, scope, and limitations. Pair any quantitative
custom figure with a structured table of exact validated values. Never place raw
SVG, HTML, CSS, JavaScript, library configuration, external image URLs, or local
file paths in report content.

Add interactive exploration only when it materially improves comparisons, segments,
trends, scenarios, or exact detail. Use declarative section filters to update linked charts and tables, local
view switchers for alternative comparisons, and chart-to-table drill-down for exact
records. Keep filters local unless every affected headline and interpretation can update with them.
Build every offered control so it actually changes relevant evidence. The narrative,
chart summary, and exact values must remain understandable if JavaScript fails.

Supply raw text rather than HTML entities; the builder escapes it exactly once. Never work around a rejected model: simplify the content or extend the package with validation and tests. If the secured build cannot run, disclose the limitation. `security-notes.md` states the boundary and how to extend it.

Match methodological detail to the report's purpose and audience. Include the
definitions, assumptions, lineage, and method needed to interpret, audit, or reproduce
the evidence. Exclude implementation noise that serves no reader purpose. The report
model does not accept raw HTML, executable code, or arbitrary configuration.

Deliver one downloadable `.html` with embedded visuals/data and no required build tools, CDN, external fonts, live queries, or authenticated dependencies for the reader. This skill does not create companion PDF, CSV, spreadsheet, or slide files. Do not export placeholder text or template sample values.

## 2. Verify and deliver

- Reconcile figures with final results: totals, denominators, signs, units, rounding, and percent versus percentage-point changes. Check all alternate views, not just the default.
- Ensure headlines and recommendations do not overstate the evidence. Preserve exceptions that could change a decision.
- Compare the finished report with the working coverage map. Every material in-scope question, conclusion, exception, and unresolved issue must be represented or explicitly identified as unavailable. Increase report depth when coverage requires it; do not discard evidence to fit a count or page target. Disclose any material coverage limit imposed by missing context or output constraints.
- Run a shape audit: remove any opening, metric, section, visual, control, closing item, or appendix element whose removal would not reduce understanding, auditability, or usefulness. Confirm that no material perspective was hidden by ranking, aggregation, or collapsed detail.
- Open/render when available; inspect the first screen and every section. For a routine report, check one representative desktop width and one narrow mobile width. Run the complete 370/576/768/992/1440 matrix for package changes, new themes, or unusually dense reports. Check readable labels, unclipped charts, table overflow, and keyboard operation.
- Exercise every control that exists, including reset, empty search, and alternate views. Check offline dependencies and the no-JavaScript evidence fallback. Fix defects before delivery; disclose checks unavailable in the environment.
- Remove repetition and improve hierarchy while retaining evidence that changes interpretation or confidence. A concise opening can lead to a substantial report; omitting an opening can also be the clearest choice.
- Proofread all visible language as a final quality gate. Correct grammar,
  punctuation, parallelism, terminology, and awkward phrasing without changing
  validated meaning, scope, uncertainty, or numerical precision.

Return the actual artifact link, a short description, and material evidence or verification limitations. Keep implementation commentary out of the report. Do not reproduce the full report in chat.

## Sandbox integration (Vibe-Trading)

Inside the Vibe-Trading sandbox this engine is the adaptive path of the
`html-report` skill; the delivery contract there overrides "return that path":

- Deliver the rendered HTML through the sibling `html-report` skill's
  `upload_report.py` (RM STS direct upload to private OSS). The deliverable is
  the printed `report_url` as a clickable link, never a bare filesystem path:

  ```bash
  python /home/opencode/.config/opencode/skills/html-report/scripts/reports/upload_report.py report.html
  ```

- For CN A-share reports, render with the bundled `theme.ashare.json`
  (red = up, green = down; CJK-first font stack):

  ```bash
  python -B /home/opencode/.config/opencode/skills/generate-interactive-business-report/scripts/render_html_report.py spec.json report.html --theme /home/opencode/.config/opencode/skills/generate-interactive-business-report/theme.ashare.json
  ```

The engine output is self-contained (inline ECharts, hash-locked CSP) and
passes `upload_report.py`'s self-containment gate unchanged.
