"""Fetch BacktestBench (KDD 2026) data with size + sha256 verification.

Official source: https://github.com/jensenw1/BacktestBench (KDD 2026,
arXiv:2605.17937), pinned commit recorded in the committed manifest
``artifacts/backtestbench_data_manifest.json``. Dataset files are Git-LFS
objects served from ``media.githubusercontent.com``.

Guarantees:

* SIZE + SHA256 of every byte are checked against the committed manifest
  BEFORE a file is moved into place; any mismatch refuses the file with a
  structured error naming the reason (todo-6 failure-QA scenario).
* Idempotent: an already-cached file that still verifies is skipped; a
  cached file whose hash no longer matches is refused as corrupt.
* Bounded: connect timeout + per-file deadline + at most ``retries``
  attempts with 5/15/45 s backoff; partial downloads resume via HTTP Range
  on the ``<dest>.part`` file (timeout+resume policy).
* Files whose manifest entry carries ``sha256: null`` (oversized, not yet
  checksummed) are NEVER fetched silently — they refuse with
  ``sha256_unknown`` so unverified bytes can never pose as benchmark data.

Usage (from ``agent/``):

    python -m src.evals.harness_bench.scripts.fetch_backtestbench \
        [--cache-dir PATH] [--with-tables] [--manifest PATH]

Exit codes: 0 all requested files verified, 1 any refusal, 2 usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = _PKG_DIR / "artifacts" / "backtestbench_data_manifest.json"
CHUNK_SIZE = 1024 * 256


class FetchIntegrityError(RuntimeError):
    """A download or cached file failed size/sha256 verification."""

    def __init__(self, reason: str, detail: dict[str, Any]):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {json.dumps(detail, ensure_ascii=False)}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_existing(dest: Path, entry: dict[str, Any]) -> tuple[bool, str]:
    if not dest.exists():
        return False, "absent"
    size = dest.stat().st_size
    if size != int(entry["size_bytes"]):
        raise FetchIntegrityError(
            "cached_file_corrupt",
            {
                "file": entry["path"],
                "reason": "size_mismatch",
                "expected_size": entry["size_bytes"],
                "observed_size": size,
            },
        )
    observed = _sha256(dest)
    if observed != entry["sha256"]:
        raise FetchIntegrityError(
            "cached_file_corrupt",
            {
                "file": entry["path"],
                "reason": "sha256_mismatch",
                "expected_sha256": entry["sha256"],
                "observed_sha256": observed,
            },
        )
    return True, "verified_cached"


def _download_once(
    url: str, part: Path, connect_timeout_s: float, deadline: float
) -> None:
    """One bounded download attempt with Range-resume on an existing .part."""
    resume_from = part.stat().st_size if part.exists() else 0
    request = urllib.request.Request(url)
    if resume_from:
        request.add_header("Range", f"bytes={resume_from}-")
    mode = "ab" if resume_from else "wb"
    part.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=connect_timeout_s) as response:
        status = getattr(response, "status", 200)
        if resume_from and status == 200:
            resume_from = 0  # server ignored Range -> restart
            mode = "wb"
        with part.open(mode) as handle:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("per-file deadline exceeded (resume kept .part)")
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)


def download_verified(entry: dict[str, Any], dest: Path, policy: dict[str, Any]) -> str:
    """Download one manifest entry to ``dest``; return the outcome label."""
    if not entry.get("sha256"):
        raise FetchIntegrityError(
            "sha256_unknown",
            {
                "file": entry["path"],
                "note": "manifest carries no checksum for this file (oversized, "
                "not yet fetched upstream); refusing to install "
                "unverified bytes as benchmark data",
            },
        )
    ok, label = _verify_existing(dest, entry)
    if ok:
        return label
    url = entry["url"]
    part = dest.with_suffix(dest.suffix + ".part")
    backoffs = list(policy.get("retry_backoff_s", [5, 15, 45]))
    attempts = int(policy.get("retries", 3))
    deadline = time.monotonic() + float(policy.get("per_file_timeout_s", 1800))
    connect_timeout_s = float(policy.get("connect_timeout_s", 30))
    last_error = "unknown"
    for attempt in range(attempts):
        try:
            _download_once(url, part, connect_timeout_s, deadline)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 >= attempts or time.monotonic() > deadline:
                raise FetchIntegrityError(
                    "download_failed",
                    {
                        "file": entry["path"],
                        "attempts": attempt + 1,
                        "last_error": last_error[:300],
                        "resume": f"partial bytes kept at {part.name}",
                    },
                ) from exc
            time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
    size = part.stat().st_size if part.exists() else 0
    if size != int(entry["size_bytes"]):
        part.unlink(missing_ok=True)
        raise FetchIntegrityError(
            "size_mismatch",
            {
                "file": entry["path"],
                "expected_size": entry["size_bytes"],
                "observed_size": size,
            },
        )
    observed = _sha256(part)
    if observed != entry["sha256"]:
        part.unlink(missing_ok=True)
        raise FetchIntegrityError(
            "sha256_mismatch",
            {
                "file": entry["path"],
                "expected_sha256": entry["sha256"],
                "observed_sha256": observed,
            },
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    part.replace(dest)
    return "downloaded_verified"


def fetch_all(manifest_path: Path, cache_dir: Path, with_tables: bool) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy = manifest.get("fetch_policy", {})
    groups = ("qa", "tables") if with_tables else ("qa",)
    outcomes: list[dict[str, Any]] = []
    failed = False
    for entry in manifest["files"]:
        if entry.get("group") not in groups:
            continue
        dest = cache_dir / entry["cache_path"]
        try:
            label = download_verified(entry, dest, policy)
            outcomes.append({"file": entry["path"], "status": label, "dest": str(dest)})
        except FetchIntegrityError as exc:
            failed = True
            outcomes.append(
                {
                    "file": entry["path"],
                    "status": "refused",
                    "reason": exc.reason,
                    "detail": exc.detail,
                }
            )
    payload = {
        "status": "error" if failed else "ok",
        "cache_dir": str(cache_dir),
        "manifest": str(manifest_path),
        "pinned_commit": manifest.get("source", {}).get("pinned_commit"),
        "outcomes": outcomes,
    }
    print(
        json.dumps(payload, indent=2, ensure_ascii=False),
        file=sys.stderr if failed else sys.stdout,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.evals.harness_bench.scripts.fetch_backtestbench",
        description="Fetch BacktestBench data with size+sha256 verification.",
    )
    repo_root = _PKG_DIR.parents[3]
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=repo_root / ".venv-eval" / "data" / "backtestbench",
        help="data cache (default: <repo>/.venv-eval/data/backtestbench)",
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST, help="manifest path"
    )
    parser.add_argument(
        "--with-tables",
        action="store_true",
        help="also fetch tables/stock_tables.zip (refuses while its manifest "
        "sha256 is null — see manifest oversized_note)",
    )
    args = parser.parse_args(argv)
    if not args.manifest.exists():
        print(f"error: manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    try:
        return fetch_all(args.manifest, args.cache_dir, args.with_tables)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"error: fetch aborted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
