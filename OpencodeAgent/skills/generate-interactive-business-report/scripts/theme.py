"""Load and validate report theme tokens, then render safe CSS variables."""

import json
import re
from pathlib import Path


COLOR_KEYS = {
    "primary",
    "primary_dark",
    "primary_light",
    "primary_soft",
    "secondary",
    "ink",
    "muted",
    "line",
    "paper",
    "surface",
    "positive",
    "negative",
    "caution",
}
TYPOGRAPHY_KEYS = {"text", "number"}
LAYOUT_LIMITS = {
    "content_width_px": (640, 2000),
    "reading_width_ch": (45, 120),
    "gutter_px": (12, 64),
    "section_gap_px": (24, 96),
    "radius_px": (0, 24),
}
GENERIC_FONTS = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
}
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def check(condition, message):
    if not condition:
        raise ValueError("Theme: " + message)


def _exact_keys(value, expected, label, optional=frozenset()):
    check(isinstance(value, dict), f"{label} must be an object")
    missing = expected - value.keys()
    unknown = value.keys() - expected - optional
    check(not missing, f"{label} is missing keys: {sorted(missing)}")
    check(not unknown, f"{label} has unknown keys: {sorted(unknown)}")


def _font_stack(value, label):
    check(isinstance(value, list) and 1 <= len(value) <= 8,
          f"typography.{label} must contain 1..8 font-family names")
    normalized = []
    for family in value:
        check(isinstance(family, str) and 1 <= len(family) <= 64,
              f"typography.{label} entries must be text up to 64 characters")
        check(all(character.isalnum() or character in " ._-" for character in family),
              f"typography.{label} contains an unsafe font-family name")
        normalized.append(family)
    return normalized


def _rgb(color):
    return tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _luminance(color):
    channels = [channel / 12.92 if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4 for channel in _rgb(color)]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first, second):
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def validate_theme(theme):
    _exact_keys(theme, {"name", "colors", "typography", "layout"}, "root", {"$schema", "mode"})
    check(isinstance(theme["name"], str) and 1 <= len(theme["name"].strip()) <= 100,
          "name must be nonempty text up to 100 characters")
    mode = theme.get("mode", "light")
    check(mode in {"light", "dark"}, "mode must be light or dark")

    colors = theme["colors"]
    _exact_keys(colors, COLOR_KEYS, "colors")
    for key, value in colors.items():
        check(isinstance(value, str) and bool(HEX_COLOR.fullmatch(value)),
              f"colors.{key} must use #RRGGBB")
    typography = theme["typography"]
    _exact_keys(typography, TYPOGRAPHY_KEYS, "typography")
    normalized_typography = {
        key: _font_stack(typography[key], key) for key in sorted(TYPOGRAPHY_KEYS)
    }

    layout = theme["layout"]
    _exact_keys(layout, set(LAYOUT_LIMITS), "layout")
    for key, (minimum, maximum) in LAYOUT_LIMITS.items():
        value = layout[key]
        check(type(value) is int and minimum <= value <= maximum,
              f"layout.{key} must be an integer from {minimum} to {maximum}")

    required_contrast = [
        ("ink", "paper", 4.5),
        ("muted", "paper", 4.5),
        ("primary_dark", "paper", 4.5),
        ("positive", "paper", 4.5),
        ("negative", "paper", 4.5),
        ("caution", "paper", 4.5),
        ("ink", "primary_soft", 4.5),
        ("ink", "surface", 4.5),
        ("primary", "paper", 3.0),
        ("primary_light", "paper", 3.0),
        ("secondary", "paper", 3.0),
    ]
    for foreground, background, minimum in required_contrast:
        ratio = contrast_ratio(colors[foreground], colors[background])
        check(ratio >= minimum,
              f"colors.{foreground} needs at least {minimum:.1f}:1 contrast against "
              f"colors.{background}; received {ratio:.2f}:1")

    return {
        "name": theme["name"].strip(),
        "mode": mode,
        "colors": {key: colors[key].lower() for key in sorted(COLOR_KEYS)},
        "typography": normalized_typography,
        "layout": {key: layout[key] for key in sorted(LAYOUT_LIMITS)},
    }


def load_theme(path):
    path = Path(path)
    try:
        theme = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Theme: invalid JSON in {path}: {error.msg}") from error
    return validate_theme(theme)


def _css_font_stack(families):
    return ", ".join(family if family in GENERIC_FONTS
                     else '"' + family.replace('"', '') + '"' for family in families)


def render_theme_css(theme):
    theme = validate_theme(theme)
    colors = theme["colors"]
    layout = theme["layout"]
    return "\n".join([
        ":root {",
        f"  color-scheme: {theme['mode']};",
        *[f"  --{key.replace('_', '-')}: {colors[key]};" for key in sorted(COLOR_KEYS)],
        f"  --font-text: {_css_font_stack(theme['typography']['text'])};",
        f"  --font-number: {_css_font_stack(theme['typography']['number'])};",
        f"  --content-width: {layout['content_width_px']}px;",
        f"  --reading-width: {layout['reading_width_ch']}ch;",
        f"  --gutter: {layout['gutter_px']}px;",
        f"  --section-gap: {layout['section_gap_px']}px;",
        f"  --radius: {layout['radius_px']}px;",
        "}",
    ])
