"""Evidence bundle for the T12 tenancy matrix driver.

Kept out of ``e2e_tenancy_matrix.py`` so the driver stays a readable phase
list (same split T11 used with ``e2e_evidence.py``). Everything written here
is sanitized: the registry snapshot drops ``token_sha256``, tenant.env files
never leave the scratch dir, and cross-grep evidence records hit COUNTS, not
secret values.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from e2e_resource_checks import RssSampler
from e2e_rig import Recorder
from tenancy_lib import dumps, sanitize_registry, summarize_rss, wake_distribution

DEPLOY_DIR = Path(__file__).resolve().parent

SCRIPT_FILES = (
    "e2e_tenancy_matrix.py",
    "e2e_matrix_checks.py",
    "e2e_state_checks.py",
    "e2e_im_checks.py",
    "e2e_resource_checks.py",
    "e2e_reclaim_checks.py",
    "e2e_matrix_evidence.py",
    "tenancy_lib.py",
    "im_tenant_probe.py",
    "real_dual_bot_smoke.py",
)


def record_rss(recorder: Recorder, rss: RssSampler, out_dir: Path) -> None:
    """Summarize the sampled RSS windows into evidence + measurements."""
    summary = summarize_rss(rss.rows)
    (out_dir / "rss_summary.json").write_text(dumps(summary), encoding="utf-8")
    for tenant, labels in summary.items():
        for label, stats in labels.items():
            recorder.measure(
                f"rss_mib[{label}]",
                float(stats["median"] or 0.0),
                unit="MiB",
                tenant=tenant,
                n=stats["n"],
                min=stats["min"],
                max=stats["max"],
            )


def record_wakes(recorder: Recorder, out_dir: Path) -> None:
    """The cold-start wake distribution (plan T12: multiple samples)."""
    distribution = wake_distribution(recorder.measurements)
    (out_dir / "wake_timings.json").write_text(dumps(distribution), encoding="utf-8")
    recorder.check(
        "wake",
        "at least 3 cold-start wake samples were measured",
        distribution["n"] >= 3,
        f"n={distribution['n']} samples={distribution['samples']}",
    )
    if distribution["n"]:
        recorder.note(
            "cold_start_wake_s distribution: "
            f"min={distribution['min']} median={distribution['median']} "
            f"max={distribution['max']} (T11 baseline: 19.7-40.9s)"
        )


def write_bundle(
    recorder: Recorder,
    out_dir: Path,
    registry_path: Path,
    args: argparse.Namespace,
    tenant_ids: list[str],
    exit_code: int,
    aborted: bool,
) -> None:
    """Sanitized registry snapshot + script copies + the results JSON."""
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        (out_dir / "registry_snapshot.json").write_text(
            dumps(sanitize_registry(payload)), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError):
        pass
    scripts_dir = out_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in SCRIPT_FILES:
        source = DEPLOY_DIR / name
        if source.exists():
            shutil.copy2(source, scripts_dir / name)
    recorder.dump(
        out_dir / "matrix_results.json",
        phase="T12 tenancy matrix",
        router_port=args.router_port,
        tenants=tenant_ids,
        image=args.image,
        reclaim_ttl_s=args.reclaim_ttl_s,
        exit_code=exit_code,
        aborted=aborted,
        all_passed=exit_code == 0 and not aborted,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
