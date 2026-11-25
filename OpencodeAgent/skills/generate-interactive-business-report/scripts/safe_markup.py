"""Rebuild a deliberately small HTML/SVG grammar. Reject unsupported markup.

This is not a general-purpose sanitizer for arbitrary web pages. No raw-text,
foreign HTML, CSS programs, executable attributes, or external assets are accepted.
"""
import base64
import binascii
import html
import math
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

HTML_TAGS = set("section article aside header footer nav div span p h1 h2 h3 h4 h5 h6 a strong em b i small sub sup br hr ul ol li dl dt dd figure figcaption details summary table caption thead tbody tfoot tr th td button input label select option img blockquote time".split())
SVG_TAGS = set("svg g title desc rect line circle ellipse path polyline polygon text tspan".split())
VOID = {"br", "hr", "input", "img"}
GLOBAL = {"id", "class", "role", "title", "lang", "hidden", "aria-label", "aria-labelledby", "aria-describedby", "aria-hidden", "aria-live", "aria-pressed", "aria-expanded", "aria-controls", "aria-sort", "tabindex"}
DATA = set("data-report-explorer data-view-controls data-view-target data-report-view data-reset-view data-view-status data-report-table data-table-controls data-table-search data-reset-table data-table-status data-sort-column data-sort-type data-value data-label data-chart-root data-chart-host data-chart-spec data-chart-actions data-chart-toggle data-chart-empty data-chart-definition data-report-filters data-report-filter data-reset-filters data-filter-status data-filter-id data-filter-ids data-filter-values data-report-drilldown data-drilldown-chart data-drilldown-column data-drilldown-controls data-drilldown-status data-reset-drilldown".split())
ATTRS = {
    "a": {"href", "rel"}, "button": {"type", "disabled"}, "input": {"type", "placeholder", "value"},
    "select": set(), "option": {"value", "selected"},
    "label": {"for"}, "details": {"open"}, "th": {"scope", "colspan", "rowspan"},
    "td": {"colspan", "rowspan"}, "ol": {"start"}, "time": {"datetime"},
    "img": {"src", "alt", "width", "height"}, "div": {"hidden"},
}
SVG_ATTRS = set("x y x1 x2 y1 y2 cx cy r rx ry width height viewbox xmlns d points fill stroke stroke-width stroke-dasharray opacity fill-opacity stroke-opacity font-size font-weight text-anchor dominant-baseline dx dy transform preserveaspectratio".split())
NUMERIC = set("x y x1 x2 y1 y2 cx cy r rx ry width height stroke-width opacity fill-opacity stroke-opacity font-size dx dy".split())
COLOR = re.compile(r"(?:#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|none|currentColor|white|black)")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}")
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def fail(message):
    raise ValueError("Unsafe or unsupported report markup: " + message)


def finite(value):
    if not re.fullmatch(NUMBER, value) or not math.isfinite(float(value)) or abs(float(value)) > 1e15:
        fail("invalid numeric attribute")


class RestrictedMarkup(HTMLParser):
    def __init__(self, ids=None, anchors=None):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.output = []
        self.ids = ids if ids is not None else set()
        self.anchors = anchors if anchors is not None else set()
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 100000 or len(self.stack) > 64:
            fail("markup complexity limit exceeded; split the report")
        in_svg = "svg" in self.stack
        if tag not in (SVG_TAGS if in_svg or tag == "svg" else HTML_TAGS):
            fail(f"element {tag!r} is not allowed")
        if in_svg and tag == "svg":
            fail("nested SVG is not supported")
        if self.stack and self.stack[-1] in {"title", "desc", "text", "tspan"} and tag != "tspan":
            fail("only text/tspan content is allowed in SVG text elements")
        if self.stack and self.stack[-1] in {"title", "desc"}:
            fail("SVG title and desc accept plain text only")
        svg = in_svg or tag == "svg"
        seen, safe = set(), []
        for key, raw in attrs:
            if key in seen:
                fail("duplicate attribute " + key)
            seen.add(key)
            value = "" if raw is None else raw
            if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
                fail("control character in attribute")
            allowed = GLOBAL | (SVG_ATTRS if svg else DATA | ATTRS.get(tag, set()))
            if key not in allowed:
                fail(f"attribute {key!r} on {tag!r} is not allowed")
            if key == "id":
                if not IDENT.fullmatch(value) or value in self.ids:
                    fail("invalid or duplicate id " + repr(value))
                self.ids.add(value)
            elif key in {"href", "src"}:
                self.check_url(tag, key, value)
                if key == "href" and value.startswith("#"):
                    self.anchors.add(value[1:])
            elif key in NUMERIC and (svg or tag == "img"):
                finite(value)
            elif svg:
                self.check_svg(key, value)
            if key == "type" and value != ("button" if tag == "button" else "search"):
                fail("only buttons and search inputs are supported")
            if key == "tabindex" and value not in {"0", "-1"}:
                fail("tabindex must be 0 or -1")
            if key in {"data-sort-column", "colspan", "rowspan"} and not re.fullmatch(r"\d{1,3}", value):
                fail("invalid column index or span")
            if key == "data-sort-type" and value not in {"number", "text", "date", "boolean"}:
                fail("invalid sort type")
            if key == "data-value" and len(value) > 1000:
                fail("data value is too long")
            canonical = {"viewbox": "viewBox", "preserveaspectratio": "preserveAspectRatio"}.get(key, key)
            safe.append(f'{canonical}="{html.escape(value, quote=True)}"')
        if tag == "a" and "href" in seen and "rel" not in seen:
            safe.append('rel="noopener noreferrer"')
        self.output.append("<" + tag + (" " + " ".join(safe) if safe else "") + ">")
        if tag not in VOID:
            self.stack.append(tag)

    @staticmethod
    def check_url(tag, key, value):
        if key == "href":
            if value.startswith("#") and IDENT.fullmatch(value[1:]):
                return
            parsed = urlsplit(value)
            if (parsed.scheme == "https" and parsed.hostname and not parsed.username and
                    not re.search(r"[\s\\\x00-\x1f]", value)):
                return
            fail("links must be local anchors or explicit https source links")
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", value)
        if tag != "img" or not match or len(value) > 8_000_000:
            fail("images must be bounded embedded PNG/JPEG data")
        try:
            data = base64.b64decode(match[2], validate=True)
        except (ValueError, binascii.Error):
            fail("invalid image encoding")
        signature = b"\x89PNG\r\n\x1a\n" if match[1] == "png" else b"\xff\xd8\xff"
        if not data.startswith(signature):
            fail("image format does not match its signature")

    @staticmethod
    def check_svg(key, value):
        if key in {"fill", "stroke"} and not COLOR.fullmatch(value):
            fail("SVG paint must be a literal color; resource references are not allowed")
        if key == "xmlns" and value != "http://www.w3.org/2000/svg":
            fail("unsupported SVG namespace")
        if key in {"d", "points", "viewbox", "stroke-dasharray"}:
            grammar = r"[MmLlHhVvCcSsQqTtAaZz0-9eE+.,\s-]+" if key == "d" else r"[0-9eE+.,\s-]+"
            if not re.fullmatch(grammar, value):
                fail("invalid SVG geometry")
            for number in re.findall(NUMBER, value):
                finite(number)
        if key == "transform" and not re.fullmatch(r"(?:(?:translate|scale|rotate|matrix)\([0-9eE+.,\s-]+\)\s*)+", value):
            fail("unsupported SVG transform")
        enums = {"text-anchor": {"start", "middle", "end"}, "dominant-baseline": {"auto", "middle", "hanging", "central"}, "font-weight": {"normal", "bold", "400", "500", "600", "700"}, "preserveaspectratio": {"xMidYMid meet", "xMinYMin meet"}}
        if key in enums and value not in enums[key]:
            fail("unsupported SVG presentation value")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            fail("tags must be explicitly balanced: " + tag)
        self.stack.pop()
        self.output.append("</" + tag + ">")

    def handle_data(self, data):
        if "\x00" in data:
            fail("NUL character")
        if self.stack and self.stack[-1] in {"title", "desc"}:
            line, column = self.getpos()
            offset = sum(len(part) for part in self.rawdata.splitlines(keepends=True)[:line - 1]) + column
            if self.rawdata[offset:offset + 1] == "<":
                fail("SVG title and desc accept plain text only")
        self.output.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        pass  # Comments are omitted from the artifact, never copied verbatim.

    def handle_decl(self, decl):
        fail("declarations are not allowed in fragments")

    def unknown_decl(self, data):
        fail("unknown declaration")

    def handle_pi(self, data):
        fail("processing instructions are not allowed")


def locate(fragment, position, message, label):
    """Point at the offending source: slot name, line and column, and a short excerpt."""
    line, column = position
    lines = fragment.split("\n")
    excerpt = lines[line - 1][max(0, column - 8):][:60].strip() if 0 < line <= len(lines) else ""
    where = (label + " ") if label else ""
    return f"{where}line {line} col {column}: {message}" + (" near: " + excerpt if excerpt else "")


def validate_fragment(fragment, ids=None, anchors=None, label=None):
    if not isinstance(fragment, str) or len(fragment) > 20_000_000:
        fail("fragment must be text under 20 MB")
    parser = RestrictedMarkup(ids, anchors)
    try:
        parser.feed(fragment)
        parser.close()
        if parser.stack:
            fail("unclosed element " + parser.stack[-1])
    except (AssertionError, RecursionError) as error:
        raise ValueError(locate(fragment, parser.getpos(), "malformed markup: " + str(error), label)) from None
    except ValueError as error:
        raise ValueError(locate(fragment, parser.getpos(), str(error), label)) from None
    return "".join(parser.output)
