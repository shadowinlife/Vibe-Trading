"""Validate a local PNG or JPEG and print its embeddable report image object."""
import argparse
import base64
import json
from pathlib import Path

from report_specification import validate_image


def encode_image(path: Path) -> dict:
    suffix = path.suffix.lower()
    mime_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix)
    if not mime_type:
        raise ValueError("image must use a .png, .jpg, or .jpeg extension")
    payload = {"mime_type": mime_type, "base64": base64.b64encode(path.read_bytes()).decode("ascii")}
    return validate_image(payload, "image")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Local PNG or JPEG to validate and encode")
    parser.add_argument("--pretty", action="store_true", help="Indent the emitted JSON")
    args = parser.parse_args()
    try:
        encoded = encode_image(args.image)
    except (OSError, ValueError) as error:
        raise SystemExit("encode_image: " + str(error)) from None
    print(json.dumps(encoded, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
