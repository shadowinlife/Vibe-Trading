"""Admin CLI for the opt-in user-auth store (plan D10).

The first admin cannot be created by an admin, so bootstrap and day-2 user
management live here — server-side break-glass that touches the SQLite store
directly, never over HTTP.

Run from the ``agent/`` directory (the module imports ``src.*``)::

    python -m src.api.user_admin create-admin <username>
    python -m src.api.user_admin create-user <username> [--role user|admin]
    python -m src.api.user_admin invite [--uses N] [--days N] [--note TEXT]
    python -m src.api.user_admin list
    python -m src.api.user_admin deactivate <username>
    python -m src.api.user_admin reset-password <username>
    python -m src.api.user_admin revoke-sessions <username> | --all

Security rules baked in:

* Passwords are read via ``getpass`` ONLY — never a command-line argument
  (which would leak into shell history and ``ps`` output).
* Output never contains a password hash or a plaintext session token.
* Invite codes are printed in plaintext exactly once, at generation: the
  store keeps only their sha256, so nobody — including this CLI — can show
  the code again later.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timedelta, timezone

from src.api.password_hashing import hash_password
from src.api.user_store import get_user_store, reset_user_store
from src.api.user_store_schema import (
    InviteInvalidError,
    UsernameExistsError,
    UserNotFoundError,
    normalize_username,
)

_PASSWORD_MIN_LENGTH = 8
_PASSWORD_MAX_LENGTH = 128


def _prompt_password() -> str:
    """Read a password twice via getpass; exit non-zero on a policy mismatch."""
    password = getpass.getpass("Password: ")
    if not _PASSWORD_MIN_LENGTH <= len(password) <= _PASSWORD_MAX_LENGTH:
        raise SystemExit(
            f"error: password must be {_PASSWORD_MIN_LENGTH}-{_PASSWORD_MAX_LENGTH} characters"
        )
    if getpass.getpass("Confirm password: ") != password:
        raise SystemExit("error: passwords do not match")
    return password


def _create_user(username: str, role: str) -> int:
    store = get_user_store()
    try:
        user = store.create_user(
            normalize_username(username),
            hash_password(_prompt_password()),
            role=role,
            display_name=username.strip(),
        )
    except UsernameExistsError:
        print(
            f"error: user '{normalize_username(username)}' already exists",
            file=sys.stderr,
        )
        return 1
    print(f"Created {user.role} '{user.username}'")
    return 0


def _cmd_create_admin(args: argparse.Namespace) -> int:
    return _create_user(args.username, "admin")


def _cmd_create_user(args: argparse.Namespace) -> int:
    return _create_user(args.username, args.role)


def _cmd_invite(args: argparse.Namespace) -> int:
    store = get_user_store()
    expires_at = None
    if args.days is not None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=args.days)
        ).isoformat()
    code = store.create_invite(
        max_uses=args.uses, expires_at=expires_at, note=args.note
    )
    print(f"Invite code (shown once — the store keeps only its sha256):\n{code}")
    print(f"  uses: {args.uses}  expires: {expires_at or 'never'}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:  # noqa: ARG001
    store = get_user_store()
    users = store.list_users()
    if not users:
        print("no users")
        return 0
    print(f"{'USERNAME':<24} {'ROLE':<7} {'ACTIVE':<7} {'CREATED':<26} LAST LOGIN")
    for user in users:
        print(
            f"{user.username:<24} {user.role:<7} {'yes' if user.is_active else 'no':<7} "
            f"{user.created_at:<26} {user.last_login_at or '-'}"
        )
    return 0


def _cmd_deactivate(args: argparse.Namespace) -> int:
    store = get_user_store()
    try:
        store.set_active(args.username, False)
    except UserNotFoundError:
        print(
            f"error: no such user '{normalize_username(args.username)}'",
            file=sys.stderr,
        )
        return 1
    # No session purge needed: validation JOINs users on is_active=1 (D1.1),
    # so every outstanding session dies on its next request.
    print(f"Deactivated '{normalize_username(args.username)}' (sessions invalidated)")
    return 0


def _cmd_reset_password(args: argparse.Namespace) -> int:
    store = get_user_store()
    try:
        store.set_password(args.username, hash_password(_prompt_password()))
    except UserNotFoundError:
        print(
            f"error: no such user '{normalize_username(args.username)}'",
            file=sys.stderr,
        )
        return 1
    print(f"Password reset for '{normalize_username(args.username)}'")
    return 0


def _cmd_revoke_sessions(args: argparse.Namespace) -> int:
    store = get_user_store()
    if args.all:
        count = store.revoke_sessions()
        print(f"Revoked {count} session(s) for ALL users")
        return 0
    if not args.username:
        print("error: pass a username or --all", file=sys.stderr)
        return 2
    if store.get_user(args.username) is None:
        print(
            f"error: no such user '{normalize_username(args.username)}'",
            file=sys.stderr,
        )
        return 1
    count = store.revoke_sessions(args.username)
    print(f"Revoked {count} session(s) for '{normalize_username(args.username)}'")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.api.user_admin",
        description="Manage users, invites and sessions of the Vibe-Trading "
        "user-auth store (run from the agent/ directory).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "create-admin", help="create the first/next admin (getpass prompt)"
    )
    p.add_argument("username")
    p.set_defaults(func=_cmd_create_admin)

    p = sub.add_parser(
        "create-user", help="create a user without an invite (getpass prompt)"
    )
    p.add_argument("username")
    p.add_argument("--role", choices=("user", "admin"), default="user")
    p.set_defaults(func=_cmd_create_user)

    p = sub.add_parser(
        "invite", help="mint an invite code; plaintext shown exactly once"
    )
    p.add_argument("--uses", type=int, default=1)
    p.add_argument("--days", type=int, default=None, help="omit for never-expiring")
    p.add_argument("--note", default=None)
    p.set_defaults(func=_cmd_invite)

    p = sub.add_parser("list", help="list users (never prints hashes or tokens)")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser(
        "deactivate", help="set is_active=0; sessions die on next request"
    )
    p.add_argument("username")
    p.set_defaults(func=_cmd_deactivate)

    p = sub.add_parser("reset-password", help="set a new password (getpass prompt)")
    p.add_argument("username")
    p.set_defaults(func=_cmd_reset_password)

    p = sub.add_parser("revoke-sessions", help="revoke one user's sessions, or --all")
    p.add_argument("username", nargs="?", default=None)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=_cmd_revoke_sessions)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (
        UsernameExistsError,
        UserNotFoundError,
        InviteInvalidError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        reset_user_store()


if __name__ == "__main__":
    raise SystemExit(main())
