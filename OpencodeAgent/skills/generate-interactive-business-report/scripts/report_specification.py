"""Validate a report specification and compile trusted HTML fragments."""
import base64
import binascii
import html
import json
import math
import re
from datetime import date, datetime, timezone
from string import Formatter
from urllib.parse import urlsplit


IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);", re.I)
GENERIC_NAVIGATION_LABEL = re.compile(r"^(?:section|finding|chapter)\s+\d+$", re.I)
TONES = {"neutral", "positive", "negative", "caution", "info"}
COLUMN_TYPES = {"text", "number", "integer", "percent", "currency", "date",
                "datetime", "duration", "boolean"}
NUMERIC_COLUMN_TYPES = {"number", "integer", "percent", "currency", "duration"}
TEMPORAL_COLUMN_TYPES = {"date", "datetime"}
FILTER_COLUMN_TYPES = {"text", "date", "datetime", "boolean"}
COMPOSITIONS = {"standard", "executive-brief", "analytical-narrative", "operational-review"}
LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
BLOCK_TYPES = {"paragraph", "insight", "callout", "list", "subsection", "figure", "image",
               "chart", "views", "table", "definitions"}
PROSE_BLOCK_TYPES = {"paragraph", "insight", "callout"}
INLINE_TYPES = {"text", "strong", "emphasis", "link", "citation"}
IMAGE_SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
UI_LABELS = {
    "skip_to_report": "Skip to report",
    "report_sections": "Report sections",
    "jump_to": "Jump to",
    "supporting_detail": "Supporting detail",
    "all_filter": "All {label}",
    "reset_filters": "Reset filters",
    "filters_applied": "Filters applied: {count}.",
    "showing_all_section_data": "Showing all section data.",
    "view": "View",
    "reset_view": "Reset",
    "showing_view": "Showing {label}. This control applies to this section only.",
    "find_record": "Find a record",
    "reset_table": "Reset table",
    "clear_drilldown": "Clear drill-down",
    "rows_shown": "{visible} of {total} rows shown",
    "no_matching_rows": "No matching rows.",
    "showing_details": "Showing details for {value}.",
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
CHART_UI_KEYS = {
    "chart_values", "chart_values_region", "chart_unavailable",
    "no_filter_observations", "show_all_rows", "show_fewer_rows", "not_available",
    "observation", "target", "lower_bound", "upper_bound", "observed", "actual", "step",
    "date_or_position", "detail", "column", "stack_order", "stack_order_normalized",
    "missing_observations", "before_after_order",
}


def fail(path, message):
    raise ValueError(f"{path}: {message}")


def exact(value, required, optional, path):
    if not isinstance(value, dict):
        fail(path, "must be an object")
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing or unknown:
        fail(path, f"missing keys {sorted(missing)}; unknown keys {sorted(unknown)}")
    return value


def plain_text(value, path, *, allow_empty=False, maximum=5000):
    if not isinstance(value, str) or len(value) > maximum:
        fail(path, f"must be text no longer than {maximum} characters")
    if not allow_empty and not value.strip():
        fail(path, "must not be empty")
    if "\x00" in value:
        fail(path, "must not contain NUL characters")
    if ENTITY.search(value):
        fail(path, "must be raw text, not pre-escaped HTML")
    return value


def identifier(value, path):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        fail(path, "must be a short identifier beginning with a letter")
    return value


def finite_number(value, path):
    if type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 1e12:
        fail(path, "must be a finite number within +/-1e12")
    return value


def integer(value, path, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        fail(path, f"must be an integer from {minimum} to {maximum}")
    return value


def boolean(value, path):
    if type(value) is not bool:
        fail(path, "must be true or false")
    return value


def escaped(value):
    return html.escape(str(value), quote=True)


def validate_ui_labels(value, path):
    if not isinstance(value, dict):
        fail(path, "must be an object")
    unknown = set(value) - set(UI_LABELS)
    if unknown:
        fail(path, f"has unknown keys {sorted(unknown)}")
    result = dict(UI_LABELS)
    formatter = Formatter()
    for key, label in value.items():
        plain_text(label, f"{path}.{key}", maximum=500)
        try:
            parsed = list(formatter.parse(label))
        except ValueError:
            fail(f"{path}.{key}", "contains invalid format placeholders")
        fields = set()
        for _, field, format_spec, conversion in parsed:
            if field is None:
                continue
            if format_spec or conversion or not re.fullmatch(r"[a-z_]+", field):
                fail(f"{path}.{key}", "contains an unsupported format placeholder")
            fields.add(field)
        expected = set(re.findall(r"\{([a-z_]+)\}", UI_LABELS[key]))
        if fields != expected:
            fail(f"{path}.{key}", f"must preserve placeholders {sorted(expected)}")
        result[key] = label
    return result


def ui_text(labels, key, **values):
    return labels[key].format(**values)


def source_url(value, path):
    if not isinstance(value, str) or not value or len(value) > 2048:
        fail(path, "must be an explicit HTTPS URL no longer than 2048 characters")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        fail(path, "must be a valid HTTPS URL")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            re.search(r"[\s\\\x00-\x1f]", value)):
        fail(path, "must be an explicit HTTPS URL without credentials")
    return value


def validate_rich_text(value, path, *, maximum=5000):
    """Accept legacy plain text or a bounded list of typed inline nodes."""
    if isinstance(value, str):
        return plain_text(value, path, maximum=maximum)
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        fail(path, "must be text or a list with 1..100 inline nodes")
    total = 0
    for index, node in enumerate(value):
        node_path = f"{path}[{index}]"
        if not isinstance(node, dict) or node.get("type") not in INLINE_TYPES:
            fail(node_path + ".type", "must name text, strong, emphasis, link, or citation")
        kind = node["type"]
        if kind in {"text", "strong", "emphasis"}:
            exact(node, {"type", "text"}, set(), node_path)
            text = plain_text(node["text"], node_path + ".text", maximum=maximum)
        elif kind == "link":
            exact(node, {"type", "text", "href"}, set(), node_path)
            text = plain_text(node["text"], node_path + ".text", maximum=500)
            source_url(node["href"], node_path + ".href")
        else:
            exact(node, {"type", "label", "href"}, {"title"}, node_path)
            text = plain_text(node["label"], node_path + ".label", maximum=160)
            source_url(node["href"], node_path + ".href")
            if "title" in node:
                plain_text(node["title"], node_path + ".title", maximum=500)
        total += len(text)
        if total > maximum:
            fail(path, f"combined inline text must be no longer than {maximum} characters")
    return value


def render_rich_text(value):
    if isinstance(value, str):
        return escaped(value)
    parts = []
    for node in value:
        kind = node["type"]
        if kind == "text":
            parts.append(escaped(node["text"]))
        elif kind == "strong":
            parts.append(f'<strong>{escaped(node["text"])}</strong>')
        elif kind == "emphasis":
            parts.append(f'<em>{escaped(node["text"])}</em>')
        elif kind == "link":
            parts.append(f'<a href="{escaped(node["href"])}" rel="noopener noreferrer">'
                         f'{escaped(node["text"])}</a>')
        else:
            title = f' title="{escaped(node["title"])}"' if node.get("title") else ""
            parts.append(f'<sup class="citation"><a href="{escaped(node["href"])}"'
                         f'{title} rel="noopener noreferrer">[{escaped(node["label"])}]</a></sup>')
    return "".join(parts)


def validate_image(image, path):
    exact(image, {"mime_type", "base64"}, set(), path)
    mime_type = image["mime_type"]
    if mime_type not in IMAGE_SIGNATURES:
        fail(path + ".mime_type", "must be image/png or image/jpeg")
    payload = image["base64"]
    if not isinstance(payload, str) or not payload or len(payload) > 7_000_000:
        fail(path + ".base64", "must be bounded base64 image data")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        fail(path + ".base64", "must be valid base64 image data")
    if len(decoded) > 5_000_000:
        fail(path + ".base64", "decoded image must not exceed 5 MB")
    if not decoded.startswith(IMAGE_SIGNATURES[mime_type]):
        fail(path, "image format does not match its signature")
    width, height = image_dimensions(decoded, mime_type, path)
    if not (1 <= width <= 10_000 and 1 <= height <= 10_000 and width * height <= 40_000_000):
        fail(path, "image dimensions must be 1..10000 pixels per side and at most 40 megapixels")
    return image


def image_dimensions(data, mime_type, path):
    if mime_type == "image/png":
        if (len(data) < 45 or data[8:12] != b"\x00\x00\x00\r" or data[12:16] != b"IHDR" or
                not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")):
            fail(path, "must contain a complete PNG header and end marker")
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

    if len(data) < 12 or not data.endswith(b"\xff\xd9"):
        fail(path, "must contain a complete JPEG end marker")
    offset = 2
    start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset < len(data):
        if data[offset] != 0xFF:
            fail(path, "must contain a valid JPEG marker sequence")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if marker == 0xDA:
            break
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > len(data):
            fail(path, "contains an invalid JPEG segment")
        if marker in start_of_frame:
            if length < 7:
                fail(path, "contains an invalid JPEG frame")
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            return width, height
        offset += length
    fail(path, "must contain JPEG dimensions before image data")


def image_uri(image):
    return f'data:{image["mime_type"]};base64,{image["base64"]}'


def validate_column(column, path):
    exact(column, {"key", "label", "type"},
          {"decimals", "prefix", "suffix", "currency", "duration_unit",
           "true_label", "false_label"}, path)
    key = identifier(column["key"], path + ".key")
    label = plain_text(column["label"], path + ".label", maximum=120)
    kind = column["type"]
    if kind not in COLUMN_TYPES:
        fail(path + ".type", "must be a supported column type")
    decimals = column.get("decimals", 0 if kind in NUMERIC_COLUMN_TYPES else None)
    if decimals is not None:
        integer(decimals, path + ".decimals", 0, 6)
    if kind not in NUMERIC_COLUMN_TYPES and decimals is not None:
        fail(path + ".decimals", f"is not valid for {kind} columns")
    if kind == "integer" and decimals != 0:
        fail(path + ".decimals", "must be zero for integer columns")
    prefix = plain_text(column.get("prefix", ""), path + ".prefix", allow_empty=True, maximum=20)
    suffix = plain_text(column.get("suffix", ""), path + ".suffix", allow_empty=True, maximum=20)
    if kind not in NUMERIC_COLUMN_TYPES and (prefix or suffix):
        fail(path, f"prefix and suffix are not valid for {kind} columns")
    currency = column.get("currency")
    if kind == "currency":
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            fail(path + ".currency", "must be a three-letter uppercase currency code")
    elif currency is not None:
        fail(path + ".currency", "is valid only for currency columns")
    duration_unit = column.get("duration_unit")
    if kind == "duration":
        if duration_unit not in {"milliseconds", "seconds", "minutes", "hours", "days"}:
            fail(path + ".duration_unit", "must be milliseconds, seconds, minutes, hours, or days")
    elif duration_unit is not None:
        fail(path + ".duration_unit", "is valid only for duration columns")
    true_label = plain_text(column.get("true_label", "Yes"), path + ".true_label", maximum=80)
    false_label = plain_text(column.get("false_label", "No"), path + ".false_label", maximum=80)
    if kind != "boolean" and ("true_label" in column or "false_label" in column):
        fail(path, "true_label and false_label are valid only for boolean columns")
    return {"key": key, "label": label, "type": kind, "decimals": decimals,
            "prefix": prefix, "suffix": suffix, "currency": currency,
            "duration_unit": duration_unit, "true_label": true_label,
            "false_label": false_label}


def validate_dataset(dataset, path):
    exact(dataset, {"columns", "rows"}, set(), path)
    columns = dataset["columns"]
    rows = dataset["rows"]
    if not isinstance(columns, list) or not 1 <= len(columns) <= 40:
        fail(path + ".columns", "must contain 1..40 columns")
    normalized_columns = [validate_column(column, f"{path}.columns[{index}]")
                          for index, column in enumerate(columns)]
    keys = [column["key"] for column in normalized_columns]
    if len(keys) != len(set(keys)):
        fail(path + ".columns", "column keys must be unique")
    if not isinstance(rows, list) or len(rows) > 10000:
        fail(path + ".rows", "must be a list with at most 10000 rows")
    normalized_rows = []
    by_key = {column["key"]: column for column in normalized_columns}
    for row_index, row in enumerate(rows):
        row_path = f"{path}.rows[{row_index}]"
        if not isinstance(row, dict) or set(row) != set(keys):
            fail(row_path, "must provide exactly one value for every declared column")
        normalized = {}
        for key in keys:
            value = row[key]
            column = by_key[key]
            value_path = f"{row_path}.{key}"
            if value is None:
                normalized[key] = None
            elif column["type"] == "text":
                normalized[key] = plain_text(value, value_path, maximum=1000)
            elif column["type"] == "integer":
                if type(value) is not int or abs(value) > 1e12:
                    fail(value_path, "must be an integer within +/-1e12")
                normalized[key] = value
            elif column["type"] == "boolean":
                normalized[key] = boolean(value, value_path)
            elif column["type"] in TEMPORAL_COLUMN_TYPES:
                normalized[key] = plain_text(value, value_path, maximum=64)
                try:
                    if column["type"] == "date":
                        date.fromisoformat(value)
                    else:
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                except (TypeError, ValueError):
                    fail(value_path, f"must be an ISO 8601 {column['type']}")
            else:
                normalized[key] = finite_number(value, value_path)
        normalized_rows.append(normalized)
    return {"columns": normalized_columns, "rows": normalized_rows}


def require_column(dataset, key, path, kinds=None):
    identifier(key, path)
    column = next((column for column in dataset["columns"] if column["key"] == key), None)
    if column is None:
        fail(path, "does not name a column in the dataset")
    if kinds and column["type"] not in kinds:
        fail(path, "names a column with an incompatible type")
    return column


def validate_chart(chart, datasets, path):
    common = {"type", "title", "unit", "period", "caption", "dataset", "category"}
    optional = {"decimals", "notes", "domain", "limit"}
    required_extra = {
        "bar": {"value"},
        "line": {"x", "value", "x_unit"},
        "waterfall": {"value", "kind"},
        "stacked": {"series"},
        "dumbbell": {"before", "after", "before_label", "after_label"},
        "scatter": {"x", "value", "x_unit"},
        "bullet": {"value", "target"},
        "variance": {"value"},
        "histogram": {"value", "bin_start", "bin_end"},
        "range": {"low", "high"},
        "heatmap": {"x_category", "value"},
        "timeline": {"x"},
        "scenario": {"series"},
    }
    optional_extra = {
        "bar": {"sort", "status"},
        "line": {"low", "high", "x_domain"},
        "waterfall": set(),
        "stacked": {"normalize"},
        "dumbbell": set(),
        "scatter": {"x_domain"},
        "bullet": {"status"},
        "variance": {"status", "sort"},
        "histogram": set(),
        "range": {"value"},
        "heatmap": set(),
        "timeline": {"detail"},
        "scenario": {"style"},
    }
    if not isinstance(chart, dict) or chart.get("type") not in required_extra:
        fail(path + ".type", "must name a supported chart family")
    kind = chart["type"]
    exact(chart, common | required_extra[kind], optional | optional_extra[kind], path)
    for key in ["title", "unit", "period", "caption"]:
        plain_text(chart[key], f"{path}.{key}", maximum=1500)
    dataset_id = identifier(chart["dataset"], path + ".dataset")
    if dataset_id not in datasets:
        fail(path + ".dataset", "does not name a dataset")
    dataset = datasets[dataset_id]
    if not dataset["rows"]:
        fail(path + ".dataset", "must contain at least one row for a chart")
    require_column(dataset, chart["category"], path + ".category", {"text"})
    digits = integer(chart.get("decimals", 1), path + ".decimals", 0, 6)
    limit = integer(chart.get("limit", min(20, max(1, len(dataset["rows"])))),
                    path + ".limit", 1, 500)
    notes = chart.get("notes", [])
    if not isinstance(notes, list) or len(notes) > 30:
        fail(path + ".notes", "must be a list with at most 30 notes")
    for index, note in enumerate(notes):
        plain_text(note, f"{path}.notes[{index}]", maximum=500)
    if "domain" in chart:
        domain = chart["domain"]
        if not isinstance(domain, list) or len(domain) != 2:
            fail(path + ".domain", "must be [minimum, maximum]")
        finite_number(domain[0], path + ".domain[0]")
        finite_number(domain[1], path + ".domain[1]")
    for key in ["x_domain"]:
        if key in chart:
            domain = chart[key]
            if not isinstance(domain, list) or len(domain) != 2:
                fail(path + "." + key, "must be [minimum, maximum]")
            finite_number(domain[0], path + "." + key + "[0]")
            finite_number(domain[1], path + "." + key + "[1]")
    if kind in {"bar", "variance"}:
        require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
        if chart.get("sort", "none") not in {"none", "ascending", "descending"}:
            fail(path + ".sort", "must be none, ascending, or descending")
        if "status" in chart:
            require_column(dataset, chart["status"], path + ".status", {"text"})
            for row_index, row in enumerate(dataset["rows"]):
                if row[chart["status"]] not in TONES:
                    fail(f"{path}.status", f"row {row_index} must be one of {sorted(TONES)}")
    elif kind in {"line", "scatter"}:
        x_kinds = NUMERIC_COLUMN_TYPES | (TEMPORAL_COLUMN_TYPES if kind == "line" else set())
        require_column(dataset, chart["x"], path + ".x", x_kinds)
        require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
        plain_text(chart["x_unit"], path + ".x_unit", maximum=120)
        if kind == "line" and ("low" in chart) != ("high" in chart):
            fail(path, "line charts require both low and high when intervals are used")
        for key in ["low", "high"]:
            if key in chart:
                require_column(dataset, chart[key], path + "." + key, NUMERIC_COLUMN_TYPES)
    elif kind == "waterfall":
        require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
        require_column(dataset, chart["kind"], path + ".kind", {"text"})
    elif kind == "stacked":
        series = chart["series"]
        if not isinstance(series, list) or not 1 <= len(series) <= 6 or len(series) != len(set(series)):
            fail(path + ".series", "must contain 1..6 distinct numeric column keys")
        for index, key in enumerate(series):
            require_column(dataset, key, f"{path}.series[{index}]", NUMERIC_COLUMN_TYPES)
        boolean(chart.get("normalize", False), path + ".normalize")
    elif kind == "dumbbell":
        require_column(dataset, chart["before"], path + ".before", NUMERIC_COLUMN_TYPES)
        require_column(dataset, chart["after"], path + ".after", NUMERIC_COLUMN_TYPES)
        plain_text(chart["before_label"], path + ".before_label", maximum=120)
        plain_text(chart["after_label"], path + ".after_label", maximum=120)
    elif kind == "bullet":
        require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
        require_column(dataset, chart["target"], path + ".target", NUMERIC_COLUMN_TYPES)
        if "status" in chart:
            require_column(dataset, chart["status"], path + ".status", {"text"})
            for row_index, row in enumerate(dataset["rows"]):
                if row[chart["status"]] not in TONES:
                    fail(f"{path}.status", f"row {row_index} must be one of {sorted(TONES)}")
    elif kind == "histogram":
        require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
        require_column(dataset, chart["bin_start"], path + ".bin_start", NUMERIC_COLUMN_TYPES)
        require_column(dataset, chart["bin_end"], path + ".bin_end", NUMERIC_COLUMN_TYPES)
        previous_end = None
        for row_index, row in enumerate(dataset["rows"]):
            start, end = row[chart["bin_start"]], row[chart["bin_end"]]
            if start is None or end is None or start >= end:
                fail(f"{path}.bin_start", f"row {row_index} must have bin_start below bin_end")
            if previous_end is not None and start < previous_end:
                fail(f"{path}.bin_start", "histogram bins must be ordered and non-overlapping")
            previous_end = end
    elif kind == "range":
        require_column(dataset, chart["low"], path + ".low", NUMERIC_COLUMN_TYPES)
        require_column(dataset, chart["high"], path + ".high", NUMERIC_COLUMN_TYPES)
        if "value" in chart:
            require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
        for row_index, row in enumerate(dataset["rows"]):
            low, high = row[chart["low"]], row[chart["high"]]
            value = row[chart["value"]] if "value" in chart else None
            if low is None or high is None or low > high:
                fail(f"{path}.low", f"row {row_index} must have low at or below high")
            if value is not None and not low <= value <= high:
                fail(f"{path}.value", f"row {row_index} must fall inside its range")
    elif kind == "heatmap":
        require_column(dataset, chart["x_category"], path + ".x_category", {"text", "date", "datetime"})
        require_column(dataset, chart["value"], path + ".value", NUMERIC_COLUMN_TYPES)
    elif kind == "timeline":
        require_column(dataset, chart["x"], path + ".x", NUMERIC_COLUMN_TYPES | TEMPORAL_COLUMN_TYPES)
        if "detail" in chart:
            require_column(dataset, chart["detail"], path + ".detail", {"text"})
    elif kind == "scenario":
        series = chart["series"]
        if not isinstance(series, list) or not 2 <= len(series) <= 6 or len(series) != len(set(series)):
            fail(path + ".series", "must contain 2..6 distinct numeric column keys")
        for index, key in enumerate(series):
            require_column(dataset, key, f"{path}.series[{index}]", NUMERIC_COLUMN_TYPES)
        if chart.get("style", "line") not in {"line", "bar"}:
            fail(path + ".style", "must be line or bar")
    return dict(chart, decimals=digits, limit=limit, notes=notes)


def validate_sort(sort, dataset, path):
    exact(sort, {"key", "direction"}, set(), path)
    require_column(dataset, sort["key"], path + ".key")
    if sort["direction"] not in {"ascending", "descending"}:
        fail(path + ".direction", "must be ascending or descending")
    return dict(sort)


def validate_block(block, datasets, charts, path, *, allow_subsection=True):
    if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
        fail(path + ".type", "must name a supported content block")
    kind = block["type"]
    if kind == "paragraph":
        exact(block, {"type", "text"}, set(), path)
        validate_rich_text(block["text"], path + ".text")
    elif kind == "insight":
        exact(block, {"type", "label", "text"}, set(), path)
        plain_text(block["label"], path + ".label", maximum=80)
        validate_rich_text(block["text"], path + ".text")
    elif kind == "callout":
        exact(block, {"type", "title", "text"}, {"tone"}, path)
        plain_text(block["title"], path + ".title", maximum=160)
        validate_rich_text(block["text"], path + ".text")
        if block.get("tone", "info") not in TONES:
            fail(path + ".tone", "must be a supported tone")
    elif kind == "list":
        exact(block, {"type", "items"}, {"style"}, path)
        if block.get("style", "unordered") not in {"ordered", "unordered"}:
            fail(path + ".style", "must be ordered or unordered")
        items = block["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            fail(path + ".items", "must contain 1..50 items")
        for index, item in enumerate(items):
            validate_rich_text(item, f"{path}.items[{index}]", maximum=2000)
    elif kind == "subsection":
        if not allow_subsection:
            fail(path, "subsections cannot be nested")
        exact(block, {"type", "title", "blocks"}, {"intro"}, path)
        plain_text(block["title"], path + ".title", maximum=500)
        if "intro" in block:
            validate_rich_text(block["intro"], path + ".intro", maximum=3000)
        blocks = block["blocks"]
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 30:
            fail(path + ".blocks", "must contain 1..30 blocks")
        for index, child in enumerate(blocks):
            validate_block(child, datasets, charts, f"{path}.blocks[{index}]",
                           allow_subsection=False)
    elif kind == "figure":
        exact(block, {"type", "title", "image", "alt", "caption"}, set(), path)
        plain_text(block["title"], path + ".title", maximum=300)
        validate_image(block["image"], path + ".image")
        plain_text(block["alt"], path + ".alt", maximum=1000)
        validate_rich_text(block["caption"], path + ".caption", maximum=2000)
    elif kind == "image":
        exact(block, {"type", "image", "alt"}, {"title", "caption", "width"}, path)
        validate_image(block["image"], path + ".image")
        plain_text(block["alt"], path + ".alt", maximum=1000)
        if "title" in block:
            plain_text(block["title"], path + ".title", maximum=300)
        if "caption" in block:
            validate_rich_text(block["caption"], path + ".caption", maximum=2000)
        if block.get("width", "reading") not in {"reading", "wide"}:
            fail(path + ".width", "must be reading or wide")
    elif kind == "chart":
        exact(block, {"type", "chart"}, set(), path)
        chart_id = identifier(block["chart"], path + ".chart")
        if chart_id not in charts:
            fail(path + ".chart", "does not name a chart")
    elif kind == "views":
        exact(block, {"type", "views"}, {"default", "label"}, path)
        views = block["views"]
        if not isinstance(views, list) or not 2 <= len(views) <= 5:
            fail(path + ".views", "must contain 2..5 related chart views")
        labels = set()
        chart_ids = set()
        for index, view in enumerate(views):
            view_path = f"{path}.views[{index}]"
            exact(view, {"label", "chart"}, set(), view_path)
            label = plain_text(view["label"], view_path + ".label", maximum=40)
            chart_id = identifier(view["chart"], view_path + ".chart")
            if chart_id not in charts:
                fail(view_path + ".chart", "does not name a chart")
            if label in labels or chart_id in chart_ids:
                fail(view_path, "labels and chart references must be distinct")
            labels.add(label)
            chart_ids.add(chart_id)
        integer(block.get("default", 0), path + ".default", 0, len(views) - 1)
        if "label" in block:
            plain_text(block["label"], path + ".label", maximum=80)
    elif kind == "table":
        exact(block, {"type", "dataset", "title"},
              {"columns", "limit", "search", "sort", "display", "summary",
               "drilldown_from"}, path)
        dataset_id = identifier(block["dataset"], path + ".dataset")
        if dataset_id not in datasets:
            fail(path + ".dataset", "does not name a dataset")
        dataset = datasets[dataset_id]
        plain_text(block["title"], path + ".title", maximum=300)
        columns = block.get("columns", [column["key"] for column in dataset["columns"]])
        if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
            fail(path + ".columns", "must be a nonempty list of distinct column keys")
        for index, key in enumerate(columns):
            require_column(dataset, key, f"{path}.columns[{index}]")
        integer(block.get("limit", min(100, max(1, len(dataset["rows"]) or 1))),
                path + ".limit", 1, 1000)
        boolean(block.get("search", False), path + ".search")
        if "sort" in block:
            validate_sort(block["sort"], dataset, path + ".sort")
        if block.get("display", "inline") not in {"inline", "detail"}:
            fail(path + ".display", "must be inline or detail")
        if "summary" in block:
            plain_text(block["summary"], path + ".summary", maximum=200)
        if "drilldown_from" in block:
            drilldown = exact(block["drilldown_from"], {"chart", "column"}, set(),
                              path + ".drilldown_from")
            chart_id = identifier(drilldown["chart"], path + ".drilldown_from.chart")
            if chart_id not in charts:
                fail(path + ".drilldown_from.chart", "does not name a chart")
            chart = charts[chart_id]
            if chart["dataset"] != dataset_id:
                fail(path + ".drilldown_from", "chart and table must use the same dataset")
            require_column(dataset, drilldown["column"], path + ".drilldown_from.column")
            if drilldown["column"] != chart["category"]:
                fail(path + ".drilldown_from.column", "must match the chart category column")
    elif kind == "definitions":
        exact(block, {"type", "title", "items"}, {"display"}, path)
        plain_text(block["title"], path + ".title", maximum=200)
        items = block["items"]
        if not isinstance(items, list) or not items:
            fail(path + ".items", "must be a nonempty list")
        for index, item in enumerate(items):
            exact(item, {"term", "definition"}, set(), f"{path}.items[{index}]")
            plain_text(item["term"], f"{path}.items[{index}].term", maximum=160)
            validate_rich_text(item["definition"], f"{path}.items[{index}].definition")
        if block.get("display", "detail") not in {"inline", "detail"}:
            fail(path + ".display", "must be inline or detail")
    return dict(block)


def chart_references_for_block(block):
    if block["type"] == "chart":
        return [block["chart"]]
    if block["type"] == "views":
        return [view["chart"] for view in block["views"]]
    if block["type"] == "subsection":
        return [chart_id for child in block["blocks"]
                for chart_id in chart_references_for_block(child)]
    return []


def chart_placements_for_block(block, heading_level=3):
    if block["type"] == "chart":
        return [(block["chart"], heading_level)]
    if block["type"] == "views":
        return [(view["chart"], heading_level) for view in block["views"]]
    if block["type"] == "subsection":
        return [placement for child in block["blocks"]
                for placement in chart_placements_for_block(child, 4)]
    return []


def filter_token(value):
    if value is None:
        return "__missing__"
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def validate_filters(filters, datasets, path):
    if not isinstance(filters, list) or not 1 <= len(filters) <= 8:
        fail(path, "must contain 1..8 filters")
    ids = set()
    normalized = []
    for index, item in enumerate(filters):
        item_path = f"{path}[{index}]"
        exact(item, {"id", "label", "column", "datasets"}, {"default"}, item_path)
        filter_id = identifier(item["id"], item_path + ".id")
        if filter_id in ids:
            fail(item_path + ".id", "must be unique within the section")
        ids.add(filter_id)
        plain_text(item["label"], item_path + ".label", maximum=120)
        column_key = identifier(item["column"], item_path + ".column")
        dataset_ids = item["datasets"]
        if (not isinstance(dataset_ids, list) or not 1 <= len(dataset_ids) <= 10 or
                len(dataset_ids) != len(set(dataset_ids))):
            fail(item_path + ".datasets", "must contain 1..10 distinct dataset ids")
        values = []
        kinds = set()
        for dataset_index, dataset_id in enumerate(dataset_ids):
            identifier(dataset_id, f"{item_path}.datasets[{dataset_index}]")
            if dataset_id not in datasets:
                fail(f"{item_path}.datasets[{dataset_index}]", "does not name a dataset")
            column = require_column(datasets[dataset_id], column_key, item_path + ".column",
                                    FILTER_COLUMN_TYPES)
            kinds.add(column["type"])
            values.extend(row[column_key] for row in datasets[dataset_id]["rows"])
        if len(kinds) != 1:
            fail(item_path + ".column", "must have the same type in every linked dataset")
        if "default" in item and item["default"] not in values:
            fail(item_path + ".default", "must match a value in a linked dataset")
        normalized.append(dict(item, datasets=list(dataset_ids)))
    return normalized


def validate_specification(content):
    exact(content, {"meta", "sections"},
          {"datasets", "charts", "opening", "closing", "supporting", "composition"}, "report")
    composition = content.get("composition", "standard")
    if composition not in COMPOSITIONS:
        fail("report.composition", "must be standard, executive-brief, analytical-narrative, or operational-review")
    meta = exact(content["meta"], {"title", "scope", "source"},
                 {"period", "freshness", "language", "logo", "ui_labels"}, "report.meta")
    for key in ["title", "scope", "source"]:
        plain_text(meta[key], "report.meta." + key, maximum=1000)
    if "period" in meta:
        plain_text(meta["period"], "report.meta.period", maximum=1000)
    if "freshness" in meta:
        plain_text(meta["freshness"], "report.meta.freshness", maximum=500)
    if "language" in meta:
        if not isinstance(meta["language"], str) or not LANGUAGE.fullmatch(meta["language"]):
            fail("report.meta.language", "must be a BCP 47-style language tag")
    if "logo" in meta:
        logo = exact(meta["logo"], {"image", "alt"}, set(), "report.meta.logo")
        validate_image(logo["image"], "report.meta.logo.image")
        plain_text(logo["alt"], "report.meta.logo.alt", maximum=300)
    ui_labels = validate_ui_labels(meta.get("ui_labels", {}), "report.meta.ui_labels")
    opening = content.get("opening")
    summaries = []
    if opening is not None:
        opening = exact(opening, {"headline"}, {"context", "summary", "metrics"},
                        "report.opening")
        plain_text(opening["headline"], "report.opening.headline", maximum=1500)
        if "context" in opening:
            plain_text(opening["context"], "report.opening.context", maximum=300)
        if "summary" in opening:
            summaries = opening["summary"] if isinstance(opening["summary"], list) else [opening["summary"]]
            if not 1 <= len(summaries) <= 6:
                fail("report.opening.summary", "must contain 1..6 paragraphs")
            for index, summary in enumerate(summaries):
                validate_rich_text(summary, f"report.opening.summary[{index}]", maximum=3000)
        metrics = opening.get("metrics", [])
        if not isinstance(metrics, list) or len(metrics) > 8:
            fail("report.opening.metrics", "must be a list with at most 8 metrics")
        for index, metric in enumerate(metrics):
            path = f"report.opening.metrics[{index}]"
            exact(metric, {"label", "value", "detail"}, {"tone"}, path)
            plain_text(metric["label"], path + ".label", maximum=100)
            plain_text(metric["value"], path + ".value", maximum=80)
            plain_text(metric["detail"], path + ".detail", maximum=200)
            if metric.get("tone", "neutral") not in TONES:
                fail(path + ".tone", "must be a supported tone")
    datasets = content.get("datasets", {})
    if not isinstance(datasets, dict) or len(datasets) > 100:
        fail("report.datasets", "must be an object with at most 100 datasets")
    normalized_datasets = {}
    for dataset_id, dataset in datasets.items():
        identifier(dataset_id, "report.datasets key")
        normalized_datasets[dataset_id] = validate_dataset(dataset, f"report.datasets.{dataset_id}")
    charts = content.get("charts", {})
    if not isinstance(charts, dict) or len(charts) > 200:
        fail("report.charts", "must be an object with at most 200 charts")
    normalized_charts = {}
    for chart_id, chart in charts.items():
        identifier(chart_id, "report.charts key")
        normalized_charts[chart_id] = validate_chart(chart, normalized_datasets,
                                                     f"report.charts.{chart_id}")
    sections = content["sections"]
    if not isinstance(sections, list) or not sections or len(sections) > 50:
        fail("report.sections", "must contain 1..50 sections")
    section_ids = set()
    navigation_labels = set()
    chart_references = []
    normalized_sections = []
    for section_index, section in enumerate(sections):
        path = f"report.sections[{section_index}]"
        exact(section, {"id", "title", "blocks"},
              {"eyebrow", "intro", "navigation_label", "filters"}, path)
        section_id = identifier(section["id"], path + ".id")
        if section_id in section_ids:
            fail(path + ".id", "must be unique")
        section_ids.add(section_id)
        if "eyebrow" in section:
            plain_text(section["eyebrow"], path + ".eyebrow", maximum=200)
        plain_text(section["title"], path + ".title", maximum=800)
        navigation_path = path + ".navigation_label" if "navigation_label" in section else path + ".title"
        navigation_label = section.get("navigation_label", section["title"]).strip()
        if "navigation_label" in section:
            plain_text(section["navigation_label"], path + ".navigation_label", maximum=72)
            if not 1 <= len(navigation_label.split()) <= 6:
                fail(path + ".navigation_label", "must contain 1..6 words")
        if GENERIC_NAVIGATION_LABEL.fullmatch(navigation_label):
            fail(navigation_path, "must describe the section, not use a generic number")
        normalized_label = navigation_label.casefold()
        if normalized_label in navigation_labels:
            fail(navigation_path, "must produce a unique navigation label")
        navigation_labels.add(normalized_label)
        if "intro" in section:
            validate_rich_text(section["intro"], path + ".intro", maximum=3000)
        section_filters = validate_filters(section["filters"], normalized_datasets,
                                           path + ".filters") if "filters" in section else []
        blocks = section["blocks"]
        if not isinstance(blocks, list) or not blocks or len(blocks) > 50:
            fail(path + ".blocks", "must contain 1..50 blocks")
        for block_index, block in enumerate(blocks):
            validate_block(block, normalized_datasets, normalized_charts,
                           f"{path}.blocks[{block_index}]")
            chart_references.extend(chart_references_for_block(block))
        normalized_sections.append(dict(section, filters=section_filters))
    if set(chart_references) != set(normalized_charts):
        fail("report.charts", "every chart must be referenced by at least one section")
    closing = content.get("closing")
    if closing is not None:
        closing = exact(closing, {"title", "items"}, set(), "report.closing")
        plain_text(closing["title"], "report.closing.title", maximum=300)
        items = closing["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            fail("report.closing.items", "must contain 1..20 items")
        for index, item in enumerate(items):
            path = f"report.closing.items[{index}]"
            exact(item, {"label", "text"}, set(), path)
            plain_text(item["label"], path + ".label", maximum=120)
            validate_rich_text(item["text"], path + ".text", maximum=1200)
    supporting = content.get("supporting", [])
    if not isinstance(supporting, list) or len(supporting) > 30:
        fail("report.supporting", "must be a list with at most 30 blocks")
    for index, block in enumerate(supporting):
        validate_block(block, normalized_datasets, normalized_charts,
                       f"report.supporting[{index}]")
        if chart_references_for_block(block):
            fail(f"report.supporting[{index}]", "charts belong in report sections")
    normalized = dict(content)
    normalized["composition"] = composition
    if opening is not None:
        normalized["opening"] = opening
    if closing is not None:
        normalized["closing"] = closing
    normalized["datasets"] = normalized_datasets
    normalized["charts"] = normalized_charts
    normalized["sections"] = normalized_sections
    normalized["_summaries"] = summaries
    normalized["_ui_labels"] = ui_labels
    return normalized


def display_value(value, column, not_available="Not available"):
    if value is None:
        return not_available
    if column["type"] == "text":
        return value
    if column["type"] in TEMPORAL_COLUMN_TYPES:
        return value
    if column["type"] == "boolean":
        return column["true_label"] if value else column["false_label"]
    digits = column["decimals"]
    formatted = f"{value:,.{digits}f}"
    if column["type"] == "currency":
        formatted = column["currency"] + " " + formatted
    if column["type"] == "duration":
        formatted += " " + column["duration_unit"]
    return column["prefix"] + formatted + column["suffix"] + ("%" if column["type"] == "percent" else "")


def sorted_rows(dataset, sort):
    rows = list(dataset["rows"])
    if not sort:
        return rows
    key = sort["key"]
    column = next(column for column in dataset["columns"] if column["key"] == key)
    present = [row for row in rows if row[key] is not None]
    missing = [row for row in rows if row[key] is None]
    def sort_value(row):
        value = row[key]
        if column["type"] == "date":
            return date.fromisoformat(value).toordinal()
        if column["type"] == "datetime":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return value
    present.sort(key=sort_value, reverse=sort["direction"] == "descending")
    return present + missing


def chart_spec(chart, datasets, filters=(), definition_id=None, ui_labels=None):
    dataset = datasets[chart["dataset"]]
    rows = list(dataset["rows"])
    kind = chart["type"]
    if kind in {"bar", "variance"} and chart.get("sort", "none") != "none":
        key = chart["value"]
        present = [row for row in rows if row[key] is not None]
        missing = [row for row in rows if row[key] is None]
        present.sort(key=lambda row: row[key], reverse=chart["sort"] == "descending")
        rows = present + missing
    rows = rows[:chart["limit"]]
    spec = {key: chart[key] for key in ["type", "title", "unit", "period", "caption", "decimals", "notes"]}
    spec["dataset"] = chart["dataset"]
    if definition_id:
        spec["definition_id"] = definition_id
    if ui_labels:
        spec["ui_labels"] = {key: ui_labels[key] for key in CHART_UI_KEYS}
    for key in ["domain", "sort", "x_unit", "x_domain", "before_label", "after_label",
                "normalize", "style"]:
        if key in chart:
            spec[key] = chart[key]
    if kind in {"line", "scatter", "timeline"}:
        x_column = next(column for column in dataset["columns"] if column["key"] == chart["x"])
        spec["x_type"] = x_column["type"]
    applicable = [flt for flt in filters if chart["dataset"] in flt["datasets"]]
    spec["filter_ids"] = [flt["id"] for flt in applicable]
    data = []
    for row in rows:
        item = {"label": row[chart["category"]]}
        if kind in {"bar", "variance"}:
            item["value"] = row[chart["value"]]
            if "status" in chart:
                item["status"] = row[chart["status"]]
        elif kind in {"line", "scatter"}:
            item.update(x=row[chart["x"]], value=row[chart["value"]])
            if kind == "line" and "low" in chart:
                item.update(low=row[chart["low"]], high=row[chart["high"]])
        elif kind == "waterfall":
            item.update(value=row[chart["value"]], kind=row[chart["kind"]])
        elif kind == "stacked":
            item["values"] = [row[key] for key in chart["series"]]
        elif kind == "dumbbell":
            item.update(before=row[chart["before"]], after=row[chart["after"]])
        elif kind == "bullet":
            item.update(value=row[chart["value"]], target=row[chart["target"]])
            if "status" in chart:
                item["status"] = row[chart["status"]]
        elif kind == "histogram":
            item.update(value=row[chart["value"]], low=row[chart["bin_start"]],
                        high=row[chart["bin_end"]])
        elif kind == "range":
            item.update(low=row[chart["low"]], high=row[chart["high"]])
            if "value" in chart:
                item["value"] = row[chart["value"]]
        elif kind == "heatmap":
            item.update(x_label=row[chart["x_category"]], value=row[chart["value"]])
        elif kind == "timeline":
            item["x"] = row[chart["x"]]
            if "detail" in chart:
                item["detail"] = row[chart["detail"]]
        elif kind == "scenario":
            item["values"] = [row[key] for key in chart["series"]]
        if applicable:
            item["filters"] = {flt["id"]: filter_token(row[flt["column"]]) for flt in applicable}
        data.append(item)
    spec["data"] = data
    if kind in {"stacked", "scenario"}:
        spec["series"] = [next(column["label"] for column in dataset["columns"] if column["key"] == key)
                          for key in chart["series"]]
    return spec


def encoded_values(values):
    return base64.b64encode(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")


def render_filters(filters, datasets, section_id, ui_labels):
    if not filters:
        return ""
    controls = []
    for index, flt in enumerate(filters):
        column = next(column for column in datasets[flt["datasets"][0]]["columns"]
                      if column["key"] == flt["column"])
        seen = set()
        options = []
        for dataset_id in flt["datasets"]:
            for row in datasets[dataset_id]["rows"]:
                token = filter_token(row[flt["column"]])
                if token in seen:
                    continue
                seen.add(token)
                label = display_value(row[flt["column"]], column, ui_labels["not_available"])
                selected = ' selected=""' if "default" in flt and row[flt["column"]] == flt["default"] else ""
                options.append(f'<option value="{escaped(token)}"{selected}>{escaped(label)}</option>')
        select_id = f"{section_id}-filter-{index}"
        controls.append(
            f'<label for="{select_id}">{escaped(flt["label"])}</label>'
            f'<select id="{select_id}" data-report-filter data-filter-id="{escaped(flt["id"])}">'
            f'<option value="">{escaped(ui_text(ui_labels, "all_filter", label=flt["label"]))}</option>{"".join(options)}</select>')
    return (
        '<div class="report-filters" data-report-filters hidden>' + "".join(controls) +
        f'<button type="button" data-reset-filters>{escaped(ui_labels["reset_filters"])}</button>'
        '<p class="control-status" data-filter-status aria-live="polite"></p></div>')


def render_table(block, datasets, block_id, filters=(), ui_labels=UI_LABELS):
    dataset = datasets[block["dataset"]]
    column_keys = block.get("columns", [column["key"] for column in dataset["columns"]])
    columns = [next(column for column in dataset["columns"] if column["key"] == key)
               for key in column_keys]
    sort = block.get("sort")
    rows = sorted_rows(dataset, sort)[:block.get("limit", 100)]
    control_parts = []
    if block.get("search", False):
        control_parts.extend([
            f'<label for="{block_id}-search">{escaped(ui_labels["find_record"])}</label>',
            f'<input id="{block_id}-search" type="search" data-table-search>',
            f'<button type="button" data-reset-table>{escaped(ui_labels["reset_table"])}</button>',
        ])
    if block.get("drilldown_from"):
        control_parts.extend([
            '<span class="control-status" data-drilldown-status aria-live="polite"></span>',
            f'<button type="button" data-reset-drilldown>{escaped(ui_labels["clear_drilldown"])}</button>',
        ])
    controls = ""
    if control_parts:
        controls = (f'<div class="report-controls" data-table-controls hidden>'
                    + "".join(control_parts)
                    + f'<p class="control-status" data-table-status aria-live="polite"></p></div>')
    headers = []
    for index, column in enumerate(columns):
        direction = sort["direction"] if sort and sort["key"] == column["key"] else "none"
        numeric = ' class="numeric"' if column["type"] in NUMERIC_COLUMN_TYPES else ""
        sort_type = ("number" if column["type"] in NUMERIC_COLUMN_TYPES else
                     "date" if column["type"] in TEMPORAL_COLUMN_TYPES else
                     "boolean" if column["type"] == "boolean" else "text")
        headers.append(
            f'<th scope="col"{numeric} aria-sort="{direction}"><button type="button" disabled '
            f'data-sort-column="{index}" data-sort-type="{sort_type}">{escaped(column["label"])}</button></th>')
    records = []
    applicable_filters = [flt for flt in filters if block["dataset"] in flt["datasets"]]
    for row in rows:
        cells = []
        for index, column in enumerate(columns):
            value = row[column["key"]]
            label = escaped(column["label"])
            display = escaped(display_value(value, column, ui_labels["not_available"]))
            data_value = "" if value is None else escaped(filter_token(value))
            attrs = f' data-label="{label}"'
            if column["type"] in NUMERIC_COLUMN_TYPES:
                attrs += f' class="numeric" data-value="{data_value}"'
            elif column["type"] != "text":
                attrs += f' data-value="{data_value}"'
            if index == 0:
                cells.append(f'<th scope="row"{attrs}>{display}</th>')
            else:
                cells.append(f'<td{attrs}>{display}</td>')
        row_values = {flt["id"]: filter_token(row[flt["column"]]) for flt in applicable_filters}
        if block.get("drilldown_from"):
            row_values["__drilldown__"] = filter_token(row[block["drilldown_from"]["column"]])
        row_attr = f' data-filter-values="{encoded_values(row_values)}"' if row_values else ""
        records.append(f"<tr{row_attr}>" + "".join(cells) + "</tr>")
    filter_ids = encoded_values([flt["id"] for flt in applicable_filters])
    table = (
        f'<div data-report-table data-filter-ids="{filter_ids}">{controls}<div class="table-wrap" tabindex="0" role="region" '
        f'aria-label="{escaped(block["title"])}"><table class="data-table">'
        f'<caption>{escaped(block["title"])}</caption><thead><tr>{"".join(headers)}</tr></thead>'
        f'<tbody>{"".join(records)}</tbody></table></div></div>')
    drilldown_attrs = ""
    if block.get("drilldown_from"):
        drilldown = block["drilldown_from"]
        drilldown_attrs = (f' data-report-drilldown data-drilldown-chart="{escaped(drilldown["chart"])}"'
                           f' data-drilldown-column="{escaped(drilldown["column"])}"')
    if block.get("display", "inline") == "detail":
        summary = escaped(block.get("summary", block["title"]))
        return f'<details class="appendix"{drilldown_attrs}><summary>{summary}</summary><div class="appendix-body">{table}</div></details>'
    return f'<div{drilldown_attrs}>{table}</div>' if drilldown_attrs else table


def render_definitions(block, heading_level=3):
    items = "".join(f'<dt>{escaped(item["term"])}</dt><dd>{render_rich_text(item["definition"])}</dd>'
                    for item in block["items"])
    body = (f'<div class="definition-block"><h{heading_level}>{escaped(block["title"])}</h{heading_level}>'
            f'<dl class="definition-list">{items}</dl></div>')
    if block.get("display", "detail") == "detail":
        return f'<details class="appendix"><summary>{escaped(block["title"])}</summary><div class="appendix-body"><dl class="definition-list">{items}</dl></div></details>'
    return body


def allocate_chart_id(definition_id, counters):
    used = counters.setdefault(None, set())
    count = counters.get(definition_id, 0) + 1
    while True:
        suffix = "" if count == 1 else f"-{count}"
        candidate = f"chart-{definition_id}{suffix}"
        if candidate not in used:
            counters[definition_id] = count
            used.add(candidate)
            return candidate
        count += 1


def render_chart_reference(definition_id, datasets, charts, chart_renderer, counters,
                           heading_level, filters, ui_labels):
    specification = chart_spec(charts[definition_id], datasets, filters, definition_id,
                               ui_labels)
    return chart_renderer(specification, allocate_chart_id(definition_id, counters),
                          heading_level=heading_level)


def render_block(block, datasets, charts, chart_renderer, chart_counters, block_id,
                 heading_level=3, filters=(), ui_labels=UI_LABELS):
    kind = block["type"]
    if kind == "paragraph":
        return f'<p>{render_rich_text(block["text"])}</p>'
    if kind == "insight":
        return (f'<p class="insight"><strong>{escaped(block["label"])}</strong> '
                f'{render_rich_text(block["text"])}</p>')
    if kind == "callout":
        tone = escaped(block.get("tone", "info"))
        return (f'<aside class="callout callout-{tone}"><h{heading_level}>{escaped(block["title"])}</h{heading_level}>'
                f'<p>{render_rich_text(block["text"])}</p></aside>')
    if kind == "list":
        tag = "ol" if block.get("style", "unordered") == "ordered" else "ul"
        items = "".join(f'<li>{render_rich_text(item)}</li>' for item in block["items"])
        return f'<{tag} class="report-list">{items}</{tag}>'
    if kind == "subsection":
        title_id = block_id + "-title"
        intro = (f'<p class="subsection-intro">{render_rich_text(block["intro"])}</p>'
                 if block.get("intro") else "")
        children = render_blocks(block["blocks"], datasets, charts, chart_renderer,
                                 chart_counters, block_id, heading_level=4,
                                 filters=filters, ui_labels=ui_labels)
        return (f'<section class="report-subsection" aria-labelledby="{title_id}">'
                f'<h3 id="{title_id}">{escaped(block["title"])}</h3>{intro}{children}</section>')
    if kind == "figure":
        title_id = block_id + "-title"
        return (f'<figure class="report-figure" aria-labelledby="{title_id}">'
                f'<h{heading_level} id="{title_id}">{escaped(block["title"])}</h{heading_level}>'
                f'<img src="{escaped(image_uri(block["image"]))}" alt="{escaped(block["alt"])}">'
                f'<figcaption>{render_rich_text(block["caption"])}</figcaption></figure>')
    if kind == "image":
        title = (f'<h{heading_level}>{escaped(block["title"])}</h{heading_level}>'
                 if block.get("title") else "")
        caption = (f'<figcaption>{render_rich_text(block["caption"])}</figcaption>'
                   if block.get("caption") else "")
        width = escaped(block.get("width", "reading"))
        return (f'<figure class="report-image report-image-{width}">{title}'
                f'<img src="{escaped(image_uri(block["image"]))}" alt="{escaped(block["alt"])}">'
                f'{caption}</figure>')
    if kind == "chart":
        return render_chart_reference(block["chart"], datasets, charts, chart_renderer,
                                      chart_counters, heading_level, filters, ui_labels)
    if kind == "views":
        default = block.get("default", 0)
        controls = []
        panels = []
        for index, view in enumerate(block["views"]):
            panel_id = f"{block_id}-view-{index}"
            controls.append(
                f'<button type="button" data-view-target="{panel_id}" aria-controls="{panel_id}" '
                f'aria-pressed="{str(index == default).lower()}">{escaped(view["label"])}</button>')
            rendered_chart = render_chart_reference(
                view["chart"], datasets, charts, chart_renderer, chart_counters,
                heading_level, filters, ui_labels)
            panels.append(
                f'<div class="chart-view" id="{panel_id}" data-report-view>'
                f'{rendered_chart}</div>')
        label = escaped(block.get("label", ui_labels["view"]))
        return (
            f'<div data-report-explorer><div class="report-controls view-controls" data-view-controls hidden>'
            f'<span>{label}:</span>{"".join(controls)}<button type="button" data-reset-view>{escaped(ui_labels["reset_view"])}</button>'
            f'<p class="control-status" data-view-status aria-live="polite"></p></div>'
            f'{"".join(panels)}</div>')
    if kind == "table":
        return render_table(block, datasets, block_id, filters, ui_labels)
    return render_definitions(block, heading_level)


def render_blocks(blocks, datasets, charts, chart_renderer, chart_counters, scope_id,
                  heading_level=3, filters=(), ui_labels=UI_LABELS):
    rendered = []
    prose = []

    def flush_prose():
        if prose:
            rendered.append('<div class="prose-grid">' + "".join(prose) + '</div>')
            prose.clear()

    for block_index, block in enumerate(blocks):
        fragment = render_block(block, datasets, charts, chart_renderer, chart_counters,
                                f'{scope_id}-block-{block_index}', heading_level, filters,
                                ui_labels)
        if block["type"] in PROSE_BLOCK_TYPES:
            prose.append(fragment)
        else:
            flush_prose()
            rendered.append(fragment)
    flush_prose()
    return "".join(rendered)


def compile_fragments(report, chart_renderer):
    report = validate_specification(report)
    chart_counters = {}
    ui_labels = report["_ui_labels"]
    opening = report.get("opening")
    summaries = "".join(f'<p>{render_rich_text(text)}</p>' for text in report["_summaries"])
    summary_block = f'<div class="hero-deck">{summaries}</div>' if summaries else ""
    metrics = ""
    if opening and opening.get("metrics"):
        items = "".join(
            f'<div class="metric metric-{escaped(metric.get("tone", "neutral"))}">'
            f'<dt>{escaped(metric["label"])}</dt><dd><span class="metric-value">{escaped(metric["value"])}</span>'
            f'<small>{escaped(metric["detail"])}</small></dd></div>' for metric in opening["metrics"])
        metrics = f'<dl class="metric-strip">{items}</dl>'
    meta = report["meta"]
    if opening:
        context = (f'<p class="eyebrow">{escaped(opening["context"])}</p>'
                   if opening.get("context") else "")
        hero = (
            '<header class="hero" id="opening" aria-labelledby="report-heading">'
            '<div class="hero-intro">'
            f'{context}<h1 id="report-heading">{escaped(opening["headline"])}</h1></div>'
            f'{summary_block}{metrics}</header>')
    else:
        hero = (
            '<header class="hero hero-minimal" id="opening" aria-labelledby="report-heading">'
            f'<div class="hero-intro"><h1 id="report-heading">{escaped(meta["title"])}</h1></div></header>')
    navigation = ""
    if len(report["sections"]) > 1:
        navigation = f'<nav class="section-nav" aria-label="{escaped(ui_labels["report_sections"])}">' + "".join(
            f'<a href="#section-{escaped(section["id"])}" '
            f'aria-label="{escaped(ui_labels["jump_to"])}: {escaped(section["title"])}">'
            f'{escaped(section.get("navigation_label", section["title"]))}</a>'
            for section in report["sections"]) + '</nav>'
    sections = []
    for section_index, section in enumerate(report["sections"]):
        eyebrow = (f'<p class="eyebrow">{escaped(section["eyebrow"])}</p>'
                    if section.get("eyebrow") else "")
        intro = (f'<p class="section-intro">{render_rich_text(section["intro"])}</p>'
                 if section.get("intro") else "")
        filters = render_filters(section.get("filters", []), report["datasets"], section["id"],
                                 ui_labels)
        blocks = render_blocks(section["blocks"], report["datasets"], report["charts"],
                               chart_renderer, chart_counters, section["id"],
                               filters=section.get("filters", []), ui_labels=ui_labels)
        sections.append(
            f'<section class="report-section" id="section-{escaped(section["id"])}" '
            f'aria-labelledby="section-{escaped(section["id"])}-title">'
            f'{eyebrow}'
            f'<h2 id="section-{escaped(section["id"])}-title">{escaped(section["title"])}</h2>'
            f'{intro}{filters}<div class="section-body">{blocks}</div></section>')
    closing = ""
    if report.get("closing"):
        items = "".join(f'<li><span class="closing-label">{escaped(item["label"])}</span>'
                        f'<span>{render_rich_text(item["text"])}</span></li>'
                        for item in report["closing"]["items"])
        closing = (f'<section class="closing"><h2>{escaped(report["closing"]["title"])}</h2>'
                   f'<ul class="closing-list">{items}</ul></section>')
    supporting = ""
    if report.get("supporting"):
        blocks = render_blocks(report["supporting"], report["datasets"], report["charts"],
                               chart_renderer, chart_counters, "supporting",
                               ui_labels=ui_labels)
        supporting = (f'<section class="supporting" aria-label="{escaped(ui_labels["supporting_detail"])}" '
                      f'id="supporting">{blocks}</section>')
    footer_parts = [meta["source"]]
    if meta.get("freshness"):
        footer_parts.append(meta["freshness"])
    return {
        "REPORT_TITLE": escaped(meta["title"]),
        "REPORT_META": " &middot; ".join(
            escaped(part) for part in [meta.get("period"), meta["scope"]] if part),
        "HTML_LANG": escaped(meta.get("language", "en")),
        "SKIP_TO_REPORT": escaped(ui_labels["skip_to_report"]),
        "UI_LABELS_ENCODED": encoded_values(ui_labels),
        "REPORT_COMPOSITION_CLASS": "composition-" + escaped(report["composition"]),
        "BRAND_LOGO_HTML": (f'<img class="brand-logo" src="{escaped(image_uri(meta["logo"]["image"]))}" '
                            f'alt="{escaped(meta["logo"]["alt"])}">' if meta.get("logo") else ""),
        "HERO_HTML": hero,
        "SECTION_NAV_HTML": navigation,
        "SECTIONS_HTML": "".join(sections),
        "CLOSING_HTML": closing,
        "SUPPORTING_HTML": supporting,
        "FOOTER_TEXT": escaped(" | ".join(footer_parts)),
    }
