"""Copy a manually exported HTML report into the control-plane reports archive."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

DEFAULT_LOCAL_DIR = "/home/opencode/.vibe-trading/reports"

_PATH_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_path_component(value: str, label: str) -> None:
    if not _PATH_SAFE_RE.match(value):
        raise ValueError(
            f"Invalid characters in {label}: {value!r}. "
            "Only alphanumeric, dots, underscores, and hyphens are allowed."
        )


def export_report(
    html_path: str,
    stock_code: str,
    report_name: str = "",
    local_dir: str = DEFAULT_LOCAL_DIR,
) -> str:
    _validate_path_component(stock_code, "stock_code")
    if report_name:
        _validate_path_component(report_name, "report_name")

    source = Path(html_path)
    if not source.is_file():
        raise FileNotFoundError(f"HTML file not found: {html_path}")

    name = report_name or source.stem
    dest_dir = Path(local_dir) / stock_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.html"
    shutil.copy2(source, dest)
    return str(dest)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy a manually exported HTML report into the reports archive."
    )
    parser.add_argument("html_path", help="Path to the HTML file to export")
    parser.add_argument("--stock", required=True, help="Stock code (e.g. 588000.SH)")
    parser.add_argument(
        "--name", default="", help="Report name (default: filename stem)"
    )
    parser.add_argument(
        "--local-dir",
        default=DEFAULT_LOCAL_DIR,
        help=f"Reports archive directory (default: {DEFAULT_LOCAL_DIR})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the destination")
    args = parser.parse_args()

    name = args.name or Path(args.html_path).stem
    dest = Path(args.local_dir) / args.stock / f"{name}.html"

    if args.dry_run:
        print(f"[DRY RUN] cp {args.html_path} {dest}")
        return

    try:
        exported = export_report(args.html_path, args.stock, args.name, args.local_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Exported: {exported}")


if __name__ == "__main__":
    main()
