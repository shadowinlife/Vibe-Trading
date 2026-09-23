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

Sandbox tenants (plan T17, ACS Agent Sandbox via the e2b backend) are a
subcommand instead — the flat form above stays byte-identical for docker:

    # claim a pool sandbox, wait for its route, register backend=e2b
    python OpencodeAgent/deploy/provision_tenant.py sandbox acme \\
        --public-host acme.example.com --out-dir /srv/vt-tenants \\
        --template opencode-poc

    # kill->rebind recovery after a lost sandbox (token and host are kept)
    python OpencodeAgent/deploy/provision_tenant.py reprovision-sandbox acme \\
        --out-dir /srv/vt-tenants
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Leading positional commands routed to the sandbox flow (plan T17). A
#: docker-flow invocation never starts with one of these (its first token is
#: always a ``--flag``), so the legacy flat form is untouched.
SANDBOX_COMMANDS = ("sandbox", "reprovision-sandbox")


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


def _sandbox_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"provision_tenant.py {command}",
        description="provision/recover one ACS sandbox (e2b) tenant (plan T17)",
    )
    parser.add_argument("tenant", help="tenant id (registry key)")
    if command == "sandbox":
        parser.add_argument(
            "--public-host", required=True, help="public Host routed to this tenant"
        )
    else:
        parser.set_defaults(public_host="")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="registry path (default: <out-dir>/tenant_registry.json)",
    )
    parser.add_argument(
        "--template",
        default="opencode-poc",
        help="sandbox pool template (gate D11 dedicated pool)",
    )
    parser.add_argument(
        "--ctl-bin",
        default="",
        help="e2b-sandbox-ctl binary (default: env VT_ROUTER_E2B_CTL_BIN "
        "or the stable sandbox-ctl venv)",
    )
    parser.add_argument(
        "--route-budget", type=int, default=180, help="route-wait budget seconds"
    )
    parser.add_argument(
        "--create-timeout",
        type=float,
        default=360.0,
        help="outer kill budget for sandbox-ctl create (sits above the CLI's "
        "own 320s request timeout)",
    )
    if command == "sandbox":
        parser.add_argument("--rotate-key", action="store_true")
    parser.add_argument(
        "--print-key",
        action="store_true",
        help="print the API_AUTH_KEY once (hand it to the tenant)",
    )
    return parser


def _sandbox_main(provision, argv: list[str]) -> int:
    command, rest = argv[0], argv[1:]
    args = _sandbox_parser(command).parse_args(rest)
    out_dir = args.out_dir.expanduser()
    spec = provision.SandboxSpec(
        tenant_id=args.tenant,
        out_dir=out_dir,
        registry_path=(args.registry or out_dir / "tenant_registry.json").expanduser(),
        public_host=args.public_host,
        template=args.template,
        ctl_bin=args.ctl_bin,
        route_wait_budget_s=args.route_budget,
        create_timeout_s=args.create_timeout,
        rotate_key=getattr(args, "rotate_key", False),
    )
    flow = (
        provision.provision_sandbox
        if command == "sandbox"
        else provision.reprovision_sandbox
    )
    try:
        result = flow(spec)
    except (provision.ProvisionError, provision.RegistryError) as exc:
        print(f"{command} failed: {exc}", file=sys.stderr)
        return 1
    print(result.to_json())
    if args.print_key:
        print(f"API_AUTH_KEY={provision.read_tenant_key(spec.env_file)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Provision one tenant (docker flow) or dispatch the sandbox subcommands."""
    argv = list(sys.argv[1:] if argv is None else argv)
    provision = _provision_module()
    if argv and argv[0] in SANDBOX_COMMANDS:
        return _sandbox_main(provision, argv)
    return _docker_main(provision, argv)


def _docker_main(provision, argv: list[str]) -> int:
    """The original flat docker-tenant flow, unchanged."""
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
