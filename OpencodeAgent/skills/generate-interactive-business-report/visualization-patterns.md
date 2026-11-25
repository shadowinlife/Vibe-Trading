# Visual and interaction patterns

Choose the chart for the question, then add interaction only when an alternate view or detail improves understanding. Never force a particular chart merely because the shell includes a place for it.

| Question | Primary visual | Useful interaction when supported |
| --- | --- | --- |
| How did it change over time? | Line, slope, or dumbbell | Compare validated periods or segments |
| Where is change concentrated? | Sorted horizontal or diverging bars | Switch value/percentage; retain explicit units and denominators |
| What contributed to a total? | Waterfall or diverging contribution bars | Switch evidenced components or drill into reconciled subtotals |
| How does mix differ? | Stacked or 100% stacked bars | Switch totals/share with the correct denominator |
| Where are targets missed? | Bullet or variance chart with reference line | Select an available business segment |
| Which observations are unusual? | Dot plot, histogram, or scatter | Focus/tap details; select a defined cohort |
| Does location explain the pattern? | Map only with reliable geographic boundaries/coordinates | Select geography; provide a ranked comparison as an accessible alternative |
| Which exact records matter? | Summary chart plus compact table | Search/sort supporting detail |
| What could happen, and how uncertain is it? | Observed/forecast line with supported intervals, scenario comparison, or sensitivity plot | Switch analyzed scenarios with assumptions and horizons visible |
| What does qualitative evidence establish? | Thematic evidence blocks, an event timeline, or a comparison matrix | Expand source context or compare supported themes |

## Visual contract

Each visual has a descriptive or evidence-supported takeaway title, period,
metric/unit, comparison basis, readable labels, and a short evidence caption. Use a
neutral title for exploratory or inconclusive evidence. Use selective direct annotations
to explain concentration, offsets, or exceptions. Label observations; do not manufacture causal annotations.

Use honest scales: bars start at zero, diverging bars share a zero line, and truncated line/scatter axes are clearly marked. Keep comparable charts on consistent scales or explicitly disclose a changed range. Do not treat missing values as zero. A percent change from a zero baseline is undefined; label it accordingly. Use verified arithmetic for contribution shares and distinguish gross losses from net change.

Show all categories when they remain readable and relevant. When density would obscure
the evidence, choose and explain a principled initial subset without targeting a fixed
category count. Maintain a full-population denominator when appropriate, aggregate
"Other" only when analytically valid, and keep material exceptions visible. Tables in
the main narrative answer the reader's lookup question; move a larger table into
expandable detail only when its initial display would impede the main reading path.

Category counts guide visual density, not analytical coverage. If a selection hides a material exception, distribution, segment difference, or trend reversal, show additional panels or a more suitable visual. A section may use several complementary charts and evidence sources, or no chart. Preserve supplied uncertainty intervals, sample sizes, and sensitivity results where they affect interpretation; do not invent statistical certainty. Qualitative evidence does not require fabricated metrics or a numeric chart.

## Interaction contract

- Make the most informative view the default. Display its categories and important values without hover.
- Prefer declarative section filters over a global dashboard filter. Link only the
  chart and table datasets named by the filter, and label the control's section scope:
  changing one section must not silently appear to change the opening headline.
- Use the pre-rendered view switcher in `report-template.md` for a small number of validated alternatives. Each view includes its own chart, caption, units, interpretation, and active-scope label.
- Use chart-to-table drill-down when selecting a category should expose exact records.
  For more complex exploration, split into meaningful local comparisons, use section
  filters and supporting-table controls, or embed a specialized static plot. Any
  renderer must recompute all affected labels, counts, denominators, and annotations
  together. Never average already-aggregated rates without their weights. Mark
  unsupported filter combinations as unavailable.
- Keep the original opening headline tied to its stated scope. Either clearly label exploration as local or recompute every affected claim if implementing a global filter.
- Tooltips work on focus/tap as well as hover and carry supplemental information only. SVG charts have a title and description; essential evidence also appears as text or an accessible data table.
- Supply reset, selected-state feedback, and an empty-result message. Validate every control's outputs against its source values. Avoid speculative scenario sliders unless the user requests a modeled scenario.
- The HTML contains a complete evidence fallback before JavaScript runs. Keep the narrative, chart summary, and exact values available when enhancement fails.

Use `chart-library.md` for the 13 reusable SVG families, section filters,
chart-to-table drill-down, and local view controls. For a
specialized static plot, use an available trusted plotting or image tool and embed
the inspected PNG/JPEG output through the structured `figure` block. Pair a
quantitative figure with a structured exact-values table. Raw SVG is not report
content. Nothing may depend on a CDN or map tiles. New interaction patterns beyond
the declared controls are package extensions; `security-notes.md` sets out that process.
