"""argparse surface: one subcommand per ContainerBackend Protocol method (T16).

Exit codes (docker-CLI-aligned, documented in README):
0 ok · 2 usage · 3 backend unavailable · 4 sandbox absent · 5 op failed · 6 auth
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from sandbox_ctl.client import (
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_TTL_S,
    ROUTE_BUDGET_S,
    SandboxClient,
)
from sandbox_ctl.output import CtlError, Exit, emit, emit_failure

_ID = dict(metavar="<sid>", help="sandbox id (e2b sandbox_id / kruise <ns>--<pod>)")


def _valid_path(path: str) -> str:
    if not path.startswith("/") or any(c in path for c in "\"' \t\n$`\\"):
        raise argparse.ArgumentTypeError(
            "path must start with '/' and contain no quotes/whitespace/$/backtick"
        )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2b-sandbox-ctl",
        description="ACS Agent Sandbox lifecycle CLI (e2b/kruise-agents SDK wrapper)",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command", required=False)

    p = sub.add_parser("state", help="running|paused|absent (non-mutating probe)")
    p.add_argument("sandbox_id", **_ID)

    p = sub.add_parser("start", help="one-step wake: connect -> route poll (+guard)")
    p.add_argument("sandbox_id", **_ID)
    p.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help="extend TTL seconds (0 = leave)")

    p = sub.add_parser("stop", help="pause (beta_pause keep_memory=False)")
    p.add_argument("sandbox_id", **_ID)

    p = sub.add_parser("engine-get", help="GET on in-pod serve via SDK envd channel")
    p.add_argument("sandbox_id", **_ID)
    p.add_argument("path", type=_valid_path, help='e.g. "/session"')

    p = sub.add_parser("create", help="claim a sandbox from the pool template")
    p.add_argument("--template", required=True)
    p.add_argument("--timeout", type=int, default=DEFAULT_TTL_S, help="sandbox TTL seconds")
    p.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT_S,
        help="SDK request budget (must exceed wait-ready + 30s)",
    )

    p = sub.add_parser("kill", help="hard-kill the sandbox")
    p.add_argument("sandbox_id", **_ID)

    p = sub.add_parser("route-wait", help="poll the gateway HTTP route until 200")
    p.add_argument("sandbox_id", **_ID)
    p.add_argument("--budget", type=float, default=ROUTE_BUDGET_S, help="seconds")

    return parser


def _dispatch(client: SandboxClient, args: argparse.Namespace) -> dict:
    match args.command:
        case "state":
            return client.state(args.sandbox_id)
        case "start":
            return client.start(args.sandbox_id, ttl_s=args.ttl)
        case "stop":
            return client.stop(args.sandbox_id)
        case "engine-get":
            return client.engine_get(args.sandbox_id, args.path)
        case "create":
            return client.create(args.template, args.timeout, args.request_timeout)
        case "kill":
            return client.kill(args.sandbox_id)
        case "route-wait":
            ready_s, attempts = client.poll_route(args.sandbox_id, budget_s=args.budget)
            return {"ready": True, "route_ready_s": ready_s, "attempts": attempts}
        case unknown:  # pragma: no cover — argparse guards this
            raise CtlError(Exit.USAGE, f"unknown command: {unknown}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from sandbox_ctl import __version__

        emit({"version": __version__})
        return int(Exit.OK)
    if not args.command:
        parser.print_help(sys.stderr)
        return int(Exit.USAGE)
    try:
        client = SandboxClient()
        result = _dispatch(client, args)
    except CtlError as err:
        return emit_failure(err)
    emit(result)
    return int(Exit.OK)


if __name__ == "__main__":
    sys.exit(main())
