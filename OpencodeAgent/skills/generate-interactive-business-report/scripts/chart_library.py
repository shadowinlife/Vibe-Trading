"""Validate chart data and emit secured ECharts payloads; specs contain no code."""
import base64
import html
import json
import math
import re
from decimal import Decimal
from datetime import date, datetime
from string import Formatter

FAMILIES = {"bar", "line", "waterfall", "stacked", "dumbbell", "scatter",
            "bullet", "variance", "histogram", "range", "heatmap", "timeline",
            "scenario"}
COMMON = {"type", "title", "unit", "period", "caption", "data", "domain", "decimals",
          "notes", "dataset", "definition_id", "ui_labels", "filter_ids"}
UI_LABELS = {
    "chart_values": "Values and accessible chart detail",
    "chart_values_region": "Chart values",
    "chart_unavailable": "Interactive chart unavailable. Exact values follow.",
    "no_filter_observations": "No observations match the selected filters.",
    "show_all_rows": "Show all {count} rows",
    "show_fewer_rows": "Show fewer rows",
    "not_available": "Not available",
    "observation": "Observation",
    "target": "Target",
    "lower_bound": "Lower bound",
    "upper_bound": "Upper bound",
    "observed": "Observed",
    "actual": "Actual",
    "step": "Step",
    "date_or_position": "Date or position",
    "detail": "Detail",
    "column": "Column",
    "stack_order": "Stack order from left: {series}.",
    "stack_order_normalized": "Stack order from left: {series}. Widths show percent of each row total.",
    "missing_observations": "{count} missing observation(s); missing values are not plotted as zero.",
    "before_after_order": "{before} · {after}. Values follow this order.",
}
EXTRA = {
    "bar": {"sort"},
    "line": {"x_unit", "x_domain", "x_type"},
    "waterfall": set(),
    "stacked": {"series", "normalize"},
    "dumbbell": {"before_label", "after_label"},
    "scatter": {"x_unit", "x_domain", "x_type"},
    "bullet": set(),
    "variance": {"sort"},
    "histogram": set(),
    "range": set(),
    "heatmap": set(),
    "timeline": {"x_type"},
    "scenario": {"series", "style"},
}


def check(condition, message):
    if not condition:
        raise ValueError("Chart specification: " + message)


def text(value, name="text"):
    check(isinstance(value, str) and bool(value.strip()) and len(value) <= 1500, name + " must be nonempty text, at most 1500 characters")
    return value


def number(value, nullable=False):
    if value is None and nullable:
        return value
    check(type(value) in {int, float} and abs(value) <= 1e12 and math.isfinite(value), "values must be finite numbers within +/-1e12; use null for missing")
    return value


def esc(value):
    return html.escape(str(value), quote=True)


def ui_labels(spec):
    supplied = spec.get("ui_labels", {})
    check(isinstance(supplied, dict) and not (set(supplied) - set(UI_LABELS)),
          "ui_labels contains unknown fields")
    labels = dict(UI_LABELS)
    for key, value in supplied.items():
        label = text(value, "ui label")
        try:
            parsed = list(Formatter().parse(label))
        except ValueError:
            check(False, "ui label contains invalid format placeholders")
        fields = set()
        for _, field, format_spec, conversion in parsed:
            if field is None:
                continue
            check(not format_spec and not conversion and bool(re.fullmatch(r"[a-z_]+", field)),
                  "ui label contains an unsupported format placeholder")
            fields.add(field)
        expected = set(re.findall(r"\{([a-z_]+)\}", UI_LABELS[key]))
        check(fields == expected, "ui label must preserve its format placeholders")
        labels[key] = label
    return labels


def extent(values, supplied=None, zero=False):
    values = [v for v in values if v is not None]
    check(bool(values), "no numeric observations; use a text evidence block")
    low, high = min(values), max(values)
    if zero:
        low, high = min(0, low), max(0, high)
    if supplied is not None:
        check(isinstance(supplied, list) and len(supplied) == 2, "domain must be [minimum, maximum]")
        a, b = map(number, supplied)
        check(a < b and a <= low and b >= high, "domain must include every value and the required zero baseline")
        return a, b
    if low == high:
        return (0, 1) if low == 0 else (min(0, low), max(0, high))
    return low, high


def validate(spec):
    check(isinstance(spec, dict) and isinstance(spec.get("type"), str) and spec["type"] in FAMILIES, "unknown chart type")
    kind = spec["type"]
    check(not (spec.keys() - COMMON - EXTRA[kind]), "unknown fields: " + str(sorted(spec.keys() - COMMON - EXTRA[kind])))
    for key in ["title", "unit", "period", "caption"]:
        text(spec.get(key), key)
    digits = spec.get("decimals", 1)
    check(type(digits) is int and 0 <= digits <= 6, "decimals must be an integer from 0 to 6")
    rows = spec.get("data")
    check(isinstance(rows, list) and 1 <= len(rows) <= 500, "provide 1..500 rows; use panels or an embedded specialized plot for larger data")
    notes = spec.get("notes", [])
    check(isinstance(notes, list) and len(notes) <= 30, "notes must be a short list of text")
    for note in notes:
        text(note, "note")
    if "dataset" in spec:
        text(spec["dataset"], "dataset")
    if "definition_id" in spec:
        text(spec["definition_id"], "definition_id")
    filter_ids = spec.get("filter_ids", [])
    check(isinstance(filter_ids, list) and len(filter_ids) <= 8 and
          all(isinstance(filter_id, str) for filter_id in filter_ids) and
          len(filter_ids) == len(set(filter_ids)), "filter_ids must contain at most 8 unique ids")
    for filter_id in filter_ids:
        text(filter_id, "filter id")
    ui_labels(spec)
    if kind in {"line", "scatter"}:
        text(spec.get("x_unit"), "x_unit")
    if kind in {"line", "scatter", "timeline"}:
        check(spec.get("x_type", "number") in {"number", "integer", "percent", "currency",
                                               "duration", "date", "datetime"},
              "unsupported x_type")
    if kind in {"bar", "variance"}:
        check(spec.get("sort", "none") in {"none", "ascending", "descending"}, "unsupported bar sort")
    if kind == "dumbbell":
        text(spec.get("before_label"), "before_label")
        text(spec.get("after_label"), "after_label")
    if kind in {"stacked", "scenario"}:
        series = spec.get("series")
        minimum = 1 if kind == "stacked" else 2
        check(isinstance(series, list) and minimum <= len(series) <= 6,
              f"{kind} charts support {minimum}..6 named series; use panels for more")
        for s in series:
            text(s, "series name")
        check(len(set(series)) == len(series), "series names must be unique")
        if kind == "stacked":
            check(type(spec.get("normalize", False)) is bool, "normalize must be boolean")
        else:
            check(spec.get("style", "line") in {"line", "bar"}, "scenario style must be line or bar")
    running = Decimal(0)
    xs = []
    temporal_xs = []
    histogram_bins = []
    for index, row in enumerate(rows):
        check(isinstance(row, dict), "each row must be an object")
        text(row.get("label"), "row label")
        check(len(row["label"]) <= 240, "row labels must be at most 240 characters")
        fields = {
            "bar": {"value", "status"}, "line": {"x", "value", "low", "high"},
            "waterfall": {"value", "kind"}, "stacked": {"values"},
            "dumbbell": {"before", "after"}, "scatter": {"x", "value", "annotate"},
            "bullet": {"value", "target", "status"}, "variance": {"value", "status"},
            "histogram": {"value", "low", "high"}, "range": {"low", "high", "value"},
            "heatmap": {"x_label", "value"}, "timeline": {"x", "detail"},
            "scenario": {"values"},
        }[kind]
        fields.add("filters")
        check(not (row.keys() - fields - {"label"}), "unknown data fields")
        if "filters" in row:
            check(isinstance(row["filters"], dict), "filters must be an object")
            for filter_id, filter_value in row["filters"].items():
                text(filter_id, "filter id")
                check(isinstance(filter_value, str) and len(filter_value) <= 1000,
                      "filter values must be bounded text")
        if kind in {"bar", "line", "scatter", "variance", "histogram", "heatmap"}:
            check("value" in row, "value is required; null denotes missing")
            number(row["value"], True)
        if kind in {"bar", "bullet", "variance"} and "status" in row:
            check(row["status"] in {"neutral", "positive", "negative", "caution", "info"},
                  "status must be a supported semantic value")
        if kind in {"line", "scatter"}:
            x = row.get("x")
            if spec.get("x_type") in {"date", "datetime"}:
                check(isinstance(x, str) and bool(x), "temporal x values must be ISO text")
                try:
                    parsed_x = (date.fromisoformat(x) if spec["x_type"] == "date" else
                                datetime.fromisoformat(x.replace("Z", "+00:00")))
                except ValueError:
                    check(False, "temporal x values must be ISO text")
                temporal_xs.append(parsed_x)
            else:
                xs.append(number(x))
        if kind == "line":
            check(("low" in row) == ("high" in row), "both interval bounds are required")
            if "low" in row:
                low, high = number(row["low"], True), number(row["high"], True)
                check((low is None) == (high is None), "interval bounds must both be numeric or null")
                check(low is None or (row["value"] is not None and low <= row["value"] <= high), "interval must enclose the observed/estimated value")
        if kind == "scatter":
            check(type(row.get("annotate", False)) is bool, "annotate must be boolean")
        if kind == "dumbbell":
            check("before" in row and "after" in row, "before and after are required; use null for missing")
            number(row["before"], True)
            number(row["after"], True)
        if kind in {"stacked", "scenario"}:
            values = row.get("values")
            check(isinstance(values, list) and len(values) == len(spec["series"]), "each stack needs a value for each series")
            for value in values:
                value = number(value)
                if kind == "stacked":
                    check(value >= 0, "stacked values must be nonnegative; missing values require a different view")
            number(sum(values))
        if kind == "waterfall":
            value = number(row.get("value"))
            check(row.get("kind") in {"total", "change"}, "waterfall rows need kind total or change")
            check(index != 0 or row["kind"] == "total", "waterfall must start with an explicit total (zero is allowed)")
            if row["kind"] == "total":
                check(index == 0 or abs(Decimal(str(value)) - running) <= Decimal("1e-8"), "waterfall subtotal does not reconcile")
                running = Decimal(str(value))
            else:
                running += Decimal(str(value))
                check(abs(running) <= Decimal("1e12"), "waterfall running total exceeds the numeric limit")
        if kind == "bullet":
            number(row.get("value"))
            number(row.get("target"))
        if kind in {"histogram", "range"}:
            low, high = number(row.get("low")), number(row.get("high"))
            check(low <= high, "range bounds must be ordered")
            if kind == "histogram":
                check(low < high and row["value"] >= 0, "histogram bins need positive width and nonnegative counts")
                histogram_bins.append((low, high))
            if kind == "range" and "value" in row:
                value = number(row["value"], True)
                check(value is None or low <= value <= high, "range value must lie within its bounds")
        if kind == "heatmap":
            text(row.get("x_label"), "heatmap x label")
        if kind == "timeline":
            x = row.get("x")
            if spec.get("x_type") in {"date", "datetime"}:
                check(isinstance(x, str) and bool(x), "timeline dates must be ISO text")
                try:
                    date.fromisoformat(x) if spec["x_type"] == "date" else datetime.fromisoformat(x.replace("Z", "+00:00"))
                except ValueError:
                    check(False, "timeline dates must be ISO text")
            else:
                number(x)
            if "detail" in row:
                text(row["detail"], "timeline detail")
    if kind == "line" and spec.get("x_type") not in {"date", "datetime"}:
        check(all(b > a for a, b in zip(xs, xs[1:])), "line x values must increase; numeric spacing is preserved")
    if kind == "line" and temporal_xs:
        try:
            increasing = all(b > a for a, b in zip(temporal_xs, temporal_xs[1:]))
        except TypeError:
            increasing = False
        check(increasing, "line x values must increase with consistent timezone notation; temporal spacing is preserved")
    if kind == "histogram":
        first_width = histogram_bins[0][1] - histogram_bins[0][0]
        tolerance = max(1e-12, abs(first_width) * 1e-9)
        for (_, previous_high), (low, high) in zip(histogram_bins, histogram_bins[1:]):
            check(math.isclose(low, previous_high, rel_tol=0, abs_tol=tolerance),
                  "histogram bins must be contiguous and ordered")
            check(math.isclose(high - low, first_width, rel_tol=0, abs_tol=tolerance),
                  "histogram bins must have equal widths; use a custom figure for density-scaled unequal bins")
    return spec


def table(spec):
    kind, digits = spec["type"], spec.get("decimals", 1)
    ui = ui_labels(spec)
    keys = {
        "bar": ["value"], "variance": ["value"],
        "line": ["x", "value", "low", "high"], "scatter": ["x", "value"],
        "waterfall": ["kind", "value"], "dumbbell": ["before", "after"],
        "bullet": ["value", "target"], "histogram": ["low", "high", "value"],
        "range": ["low", "high", "value"], "heatmap": ["x_label", "value"],
        "timeline": ["x", "detail"],
    }.get(kind, [])
    if kind == "line" and not any("low" in row for row in spec["data"]):
        keys = ["x", "value"]
    if kind == "range" and not any("value" in row for row in spec["data"]):
        keys = ["low", "high"]
    labels = {"x": spec.get("x_unit", ui["date_or_position"]), "value": spec["unit"],
              "target": ui["target"], "low": ui["lower_bound"], "high": ui["upper_bound"],
              "kind": ui["step"], "before": spec.get("before_label"),
              "after": spec.get("after_label"), "x_label": ui["column"], "detail": ui["detail"]}
    headers = spec["series"] if kind in {"stacked", "scenario"} else [labels[key] for key in keys]
    records = []
    for row in spec["data"]:
        values = row["values"] if kind in {"stacked", "scenario"} else [row.get(key) for key in keys]
        # The supporting table retains original numeric precision, independent of display rounding.
        cells = "".join(
            '<td' + ('' if isinstance(value, str) else ' class="numeric"') +
            ' data-label="' + esc(header) + '" data-value="' +
            ("" if value is None else esc(value)) + '">' +
            esc(value if value is not None else ui["not_available"]) + '</td>'
            for header, value in zip(headers, values))
        filter_attr = ""
        if row.get("filters"):
            encoded = base64.b64encode(json.dumps(row["filters"], ensure_ascii=False,
                                                   separators=(",", ":")).encode("utf-8")).decode("ascii")
            filter_attr = f' data-filter-values="{encoded}"'
        records.append(f'<tr{filter_attr}><th scope="row" data-label="{esc(ui["observation"])}">' + esc(row["label"]) + '</th>' + cells + '</tr>')
    filter_ids = base64.b64encode(json.dumps(spec.get("filter_ids", []), ensure_ascii=False,
                                               separators=(",", ":")).encode("utf-8")).decode("ascii")
    return f'<details class="appendix"><summary>{esc(ui["chart_values"])}</summary><div class="appendix-body table-wrap" data-report-table data-filter-ids="{filter_ids}" tabindex="0" role="region" aria-label="{esc(ui["chart_values_region"])}"><table class="data-table"><caption>' + esc(spec["unit"] + " · " + spec["period"]) + f'</caption><thead><tr><th scope="col">{esc(ui["observation"])}</th>' + "".join('<th scope="col">' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + "".join(records) + '</tbody></table></div></details>'


def validate_domains(spec):
    kind = spec["type"]
    rows = spec["data"]
    if kind in {"bar", "variance"}:
        extent([row["value"] for row in rows], spec.get("domain"), zero=True)
    elif kind in {"line", "scatter"}:
        values = [row.get(key) for row in rows for key in ["value", "low", "high"]]
        extent(values, spec.get("domain"))
        if spec.get("x_type") not in {"date", "datetime"}:
            extent([row["x"] for row in rows], spec.get("x_domain"))
    elif kind == "dumbbell":
        extent([row[key] for row in rows for key in ["before", "after"]], spec.get("domain"))
    elif kind == "bullet":
        extent([row[key] for row in rows for key in ["value", "target"]], spec.get("domain"), zero=True)
    elif kind == "histogram":
        extent([row["value"] for row in rows], spec.get("domain"), zero=True)
    elif kind == "range":
        extent([row.get(key) for row in rows for key in ["low", "high", "value"]],
               spec.get("domain"))
    elif kind == "heatmap":
        extent([row["value"] for row in rows], spec.get("domain"))
    elif kind in {"stacked", "scenario"}:
        totals = [sum(row["values"]) for row in rows]
        if kind == "stacked":
            supplied = [0, 100] if spec.get("normalize") and "domain" not in spec else spec.get("domain")
            extent(([0, 100] if spec.get("normalize") else totals), supplied, zero=True)
        else:
            extent([value for row in rows for value in row["values"]], spec.get("domain"),
                   zero=spec.get("style", "line") == "bar")
    elif kind == "waterfall":
        running = 0
        observations = [0]
        for row in rows:
            start = 0 if row["kind"] == "total" else running
            end = row["value"] if row["kind"] == "total" else running + row["value"]
            running = end
            observations.extend([start, end])
        extent(observations, spec.get("domain"), zero=True)


def render_chart(spec, chart_id="chart", heading_level=3):
    validate(spec)
    validate_domains(spec)
    check(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,100}", chart_id)), "invalid chart id")
    check(heading_level in {3, 4}, "chart heading level must be 3 or 4")
    kind = spec["type"]
    ui = ui_labels(spec)
    payload = base64.b64encode(json.dumps(spec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    definition = esc(spec.get("definition_id", chart_id.removeprefix("chart-")))
    result = [f'<figure class="report-chart" id="{chart_id}" data-chart-root data-chart-definition="{definition}"><h{heading_level}>{esc(spec["title"])}</h{heading_level}>',
              '<p class="chart-meta">' + esc(spec["unit"] + " · " + spec["period"]) + '</p>',
              f'<div class="chart-canvas" data-chart-host data-chart-spec="{payload}" role="img" aria-label="{esc(spec["title"] + ". " + spec["caption"])}"></div>',
              f'<p class="chart-fallback">{esc(ui["chart_unavailable"])}</p>',
              f'<p class="chart-empty" data-chart-empty hidden>{esc(ui["no_filter_observations"])}</p>']
    if kind == "dumbbell":
        result.append('<p class="chart-meta">● ' + esc(ui["before_after_order"].format(
            before=spec["before_label"], after='■ ' + spec["after_label"])) + '</p>')
    if kind == "stacked":
        key = "stack_order_normalized" if spec.get("normalize") else "stack_order"
        result.append('<p class="chart-meta">' + esc(ui[key].format(series=" → ".join(spec["series"]))) + '</p>')
    missing = sum(row.get("value") is None for row in spec["data"]) if kind in {"line", "scatter", "bar", "variance"} else 0
    if missing:
        result.append(f'<p class="chart-meta">{esc(ui["missing_observations"].format(count=missing))}</p>')
    result.extend('<p class="chart-meta">' + esc(note) + '</p>' for note in spec.get("notes", []))
    if len(spec["data"]) > 10:
        result.append(f'<div class="chart-actions" data-chart-actions hidden><button type="button" data-chart-toggle aria-expanded="false">{esc(ui["show_all_rows"].format(count=len(spec["data"])))}</button></div>')
    result += ['<figcaption>' + esc(spec["caption"]) + '</figcaption>', '</figure>', table(spec)]
    return "".join(result)
