"""Operator entry points for the tenant router: serve / show / reclaim."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from typing import assert_never

from .backend import build_backend
from .config import RouterSettings
from .fence import TenantFence
from .reclaim import ActivityLedger, ReclaimContext, evaluate_tenant, reclaim_idle
from .registry import RegistryError, TenantRegistry


def main(argv: list[str] | None = None) -> int:
    """Parse argv and dispatch one subcommand."""
    parser = argparse.ArgumentParser(prog="router", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the reverse proxy")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    commands.add_parser("show", help="print the routing table (no secrets)")
    reclaim = commands.add_parser("reclaim", help="one-shot idle reclaim pass")
    reclaim.add_argument(
        "--dry-run", action="store_true", help="evaluate only, stop nothing"
    )
    args = parser.parse_args(argv)
    settings = RouterSettings.from_env()
    match args.command:
        case "serve":
            return _serve(settings, args.host, args.port)
        case "show":
            return _show(settings)
        case "reclaim":
            return asyncio.run(_reclaim(settings, args.dry_run))
        case unreachable:
            assert_never(unreachable)


def _serve(settings: RouterSettings, host: str | None, port: int | None) -> int:
    import uvicorn

    from .app import create_app

    # The router's own logger needs a handler: uvicorn configures only its own
    # loggers, and without a root handler the INFO-level routing/wake trail is
    # dropped (Python's last-resort handler starts at WARNING).
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    uvicorn.run(
        create_app(settings),
        host=host or settings.host,
        port=port or settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def _show(settings: RouterSettings) -> int:
    try:
        registry = TenantRegistry.load(settings.registry_path)
    except RegistryError as exc:
        print(f"registry error: {exc}")
        return 1
    print(
        json.dumps(
            {
                "registry": str(settings.registry_path),
                "tenants": {
                    tenant_id: {
                        "public_host": tenant.public_host,
                        "upstream": tenant.upstream,
                        "container": tenant.container,
                        "volume": tenant.volume,
                    }
                    for tenant_id, tenant in registry.tenants.items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


async def _reclaim(settings: RouterSettings, dry_run: bool) -> int:
    """Run one reclaim pass.

    A one-shot CLI pass has an EMPTY proxy ledger by construction, so every
    verdict comes from the engine truth check — which is the point of the
    opencode-router recipe (proxy-side activity alone is not evidence).
    """
    try:
        registry = TenantRegistry.load(settings.registry_path)
    except RegistryError as exc:
        print(f"registry error: {exc}")
        return 1
    context = ReclaimContext(
        backend=build_backend(
            settings.backend,
            docker_bin=settings.docker_bin,
            serve_url=settings.serve_url,
        ),
        fence=TenantFence(),
        ledger=ActivityLedger(),
        idle_ttl_ms=int(settings.idle_ttl_s * 1000),
        drain_timeout_s=settings.drain_timeout_s,
    )
    tenants = list(registry.tenants.values())
    if dry_run:
        decisions = [await evaluate_tenant(tenant, context) for tenant in tenants]
    else:
        decisions = await reclaim_idle(tenants, context)
    print(json.dumps([asdict(decision) for decision in decisions], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
