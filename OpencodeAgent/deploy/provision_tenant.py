#!/usr/bin/env python3
"""Provision one tenant into the T10 container fleet + the T11 router registry.

Thin CLI over :mod:`router.provision` (see that module for the recipe and the
idempotency rules). Examples:

    # local two-tenant rig (host-run router on 28080)
    python OpencodeAgent/deploy/provision_tenant.py \\
        --tenant a --public-host a.tenant.local --host-port 28081 \\
        --out-dir /tmp/vt-t11-rig/tenants \\
        --container-prefix vt-t11 --volume-prefix vt-t11 \\
        --base-env OpencodeAgent/.env

    # re-run is safe: secrets are reused, agent.json is never clobbered
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _provision_module():
    """Import router.provision with the deploy dir on sys.path (function-local
    so the module stays importable without a path hack at import time)."""
    deploy_dir = str(Path(__file__).resolve().parent)
    if deploy_dir not in sys.path:
        sys.path.insert(0, deploy_dir)
    from router import provision

    return provision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="provision_tenant.py", description=__doc__)
    parser.add_argument(
        "--tenant",
        required=True,
        help="tenant id (registry key, container/volume suffix)",
    )
    parser.add_argument(
        "--public-host",
        required=True,
        help="public Host the router preserves for this tenant",
    )
    parser.add_argument(
        "--host-port",
        type=int,
        required=True,
        help="host port published to the gateway's 8080",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="directory holding per-tenant generated files",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="registry path (default: <out-dir>/tenant_registry.json)",
    )
    parser.add_argument(
        "--image", default="opencode-serve:v3.0.0-tenant", help="T10 tenant image"
    )
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--container-prefix", default="vt-tenant")
    parser.add_argument("--volume-prefix", default="vt-tenant")
    parser.add_argument(
        "--upstream",
        default="",
        help="router-side upstream (default: http://127.0.0.1:<host-port>)",
    )
    parser.add_argument(
        "--sse-timeout", type=int, default=90, help="VIBE_TRADING_SSE_TIMEOUT seed"
    )
    parser.add_argument(
        "--base-env",
        type=Path,
        default=None,
        help="dotenv to pass shared model/data-source creds from",
    )
    parser.add_argument(
        "--skip-volume",
        action="store_true",
        help="skip docker volume create/seed (offline/tests)",
    )
    parser.add_argument(
        "--rotate-key",
        action="store_true",
        help="generate a NEW API_AUTH_KEY (invalidates the old one)",
    )
    parser.add_argument(
        "--print-key",
        action="store_true",
        help="print the API_AUTH_KEY once (hand it to the tenant)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Provision one tenant and print a machine-readable summary."""
    provision = _provision_module()
    args = _parser().parse_args(argv)
    out_dir = args.out_dir.expanduser()
    spec = provision.TenantSpec(
        tenant_id=args.tenant,
        public_host=args.public_host,
        host_port=args.host_port,
        image=args.image,
        out_dir=out_dir,
        registry_path=(args.registry or out_dir / "tenant_registry.json").expanduser(),
        platform=args.platform,
        container_prefix=args.container_prefix,
        volume_prefix=args.volume_prefix,
        upstream=args.upstream,
        sse_timeout_s=args.sse_timeout,
        base_env=args.base_env,
        skip_volume=args.skip_volume,
        rotate_key=args.rotate_key,
    )
    result = provision.provision(spec)
    print(result.to_json())
    if args.print_key:
        print(f"API_AUTH_KEY={provision.read_tenant_key(spec.env_file)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
