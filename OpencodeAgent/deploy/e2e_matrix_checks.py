"""T12 isolation matrix — HTTP-surface items (plan T12: 双租户同机验收).

Covers the matrix items that are pure HTTP against the router/upstreams:

    m1  A 的 key 打 B upstream → 401            (direct, bypassing the router)
    m2  浏览器经 router POST → 200 非 403        (Host + API_ALLOWED_HOSTS, B2)
    m3  SSE ticket 各自独立                      (mint/scope/single-use + the
        silent-witness stream helper used during the live turn)

State-level items (m4 session/store isolation, m5 channels config, m6
process/network/filesystem) live in :mod:`e2e_state_checks`; the IM runtime
probes (m5) live in :mod:`e2e_im_checks`. Every verdict is an observed HTTP
status or command result — nothing is estimated (plan T12 Must-NOT), and the
level proven per item is documented in ``OpencodeAgent/docs/tenancy_report.md``.
"""

from __future__ import annotations

import threading
import time

import httpx

from e2e_rig import Rig, TenantRig

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _pair(rig: Rig) -> tuple[TenantRig, TenantRig]:
    tenants = list(rig.tenants.values())
    if len(tenants) < 2:
        raise RuntimeError("the isolation matrix needs at least two tenants")
    return tenants[0], tenants[1]


# --- M1: cross-tenant credential rejection (direct, bypassing the router) ----


def check_cross_key_isolation(rig: Rig) -> None:
    """Tenant A's API key must never authenticate against tenant B's stack."""
    rec = rig.recorder
    first, second = _pair(rig)
    for attacker, victim in ((first, second), (second, first)):
        # Direct to the victim's published upstream — router bypassed entirely.
        direct = rig.client.get(
            f"{victim.upstream}/sessions",
            headers={"Authorization": f"Bearer {attacker.api_key}"},
            timeout=30.0,
        )
        rec.check(
            "m1",
            f"{attacker.tenant_id}'s key -> {victim.tenant_id}'s upstream DIRECT -> 401",
            direct.status_code == 401,
            f"HTTP {direct.status_code}",
        )
        # Same, but presenting the victim's public Host (attacker knows the name).
        with_host = rig.client.get(
            f"{victim.upstream}/sessions",
            headers={
                "Authorization": f"Bearer {attacker.api_key}",
                "Host": victim.public_host,
            },
            timeout=30.0,
        )
        rec.check(
            "m1",
            f"{attacker.tenant_id}'s key + {victim.tenant_id}'s Host direct -> 401",
            with_host.status_code == 401,
            f"HTTP {with_host.status_code}",
        )
        # Anonymous direct (no credential at all).
        anonymous = rig.client.get(f"{victim.upstream}/sessions", timeout=30.0)
        rec.check(
            "m1",
            f"anonymous -> {victim.tenant_id}'s upstream direct -> 401",
            anonymous.status_code == 401,
            f"HTTP {anonymous.status_code}",
        )
        # Through the router: attacker token + victim Host is a routing conflict.
        crossed = rig.request(
            "GET", "/sessions", tenant=attacker, host=victim.public_host
        )
        rec.check(
            "m1",
            f"{attacker.tenant_id}'s token + {victim.tenant_id}'s Host via router -> 403",
            crossed.status_code == 403 and "x-vt-tenant" not in crossed.headers,
            f"HTTP {crossed.status_code}",
        )
        # Positive control: the victim's OWN key on the same direct path works,
        # so the 401s above are credential discrimination, not blanket failure.
        own = rig.client.get(
            f"{victim.upstream}/sessions",
            headers={"Authorization": f"Bearer {victim.api_key}"},
            timeout=30.0,
        )
        rec.check(
            "m1",
            f"positive control: {victim.tenant_id}'s own key direct -> 200",
            own.status_code == 200,
            f"HTTP {own.status_code}",
        )


# --- M2: browser-style POST through the router (B2 recipe end-to-end) --------


def check_browser_post(rig: Rig) -> dict[str, str]:
    """A browser POST via the router is 200/201 — never the B2 403 trap.

    Returns ``{tenant_id: session_id}`` — the sessions later matrix items
    (SSE streams, session-list isolation) reuse them.
    """
    rec = rig.recorder
    created: dict[str, str] = {}
    for tenant in rig.tenants.values():
        browser_headers = {
            "Origin": f"http://{tenant.public_host}",
            "Referer": f"http://{tenant.public_host}/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "User-Agent": BROWSER_UA,
        }
        response = rig.request(
            "POST",
            "/sessions",
            tenant=tenant,
            json_body={"title": f"T12 browser POST {tenant.tenant_id}"},
            extra_headers=browser_headers,
        )
        ok = response.status_code == 201
        rec.check(
            "m2",
            f"tenant {tenant.tenant_id}: browser-style POST via router -> 201 (not 403)",
            ok,
            f"HTTP {response.status_code} (Host preserved + API_ALLOWED_HOSTS + Origin match)",
        )
        if ok:
            created[tenant.tenant_id] = str(response.json().get("session_id", ""))

        # Negative control: a CROSS-SITE browser POST must still be denied —
        # the recipe is an allow-list, not a blanket disable of the guard.
        others = [t for t in rig.tenants.values() if t.tenant_id != tenant.tenant_id]
        if others:
            foreign = rig.request(
                "POST",
                "/sessions",
                tenant=tenant,
                json_body={"title": "cross-site"},
                extra_headers={
                    **browser_headers,
                    "Origin": f"http://{others[0].public_host}",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            rec.check(
                "m2",
                f"tenant {tenant.tenant_id}: cross-site Origin POST -> 403 (guard alive)",
                foreign.status_code == 403,
                f"HTTP {foreign.status_code}",
            )
    return created


# --- M3: SSE ticket + stream independence ------------------------------------


def _mint(rig: Rig, tenant: TenantRig) -> tuple[int, str]:
    response = rig.request("POST", "/auth/sse-ticket", tenant=tenant)
    ticket = ""
    if response.status_code == 200:
        ticket = str(response.json().get("ticket", ""))
    return response.status_code, ticket


def _redact(ticket: str) -> str:
    return f"{ticket[:6]}…" if ticket else "<none>"


def check_sse_ticket_scoping(rig: Rig, sessions: dict[str, str]) -> None:
    """Tickets are per-gateway-process, single-use, and never cross tenants."""
    rec = rig.recorder
    first, second = _pair(rig)
    for minter, other in ((first, second), (second, first)):
        status, ticket = _mint(rig, minter)
        rec.check(
            "m3",
            f"tenant {minter.tenant_id}: POST /auth/sse-ticket (key auth) -> 200 + ticket",
            status == 200 and bool(ticket),
            f"HTTP {status} ticket={_redact(ticket)}",
        )
        if not ticket:
            continue
        # The minting tenant's own stream accepts the ticket (browser shape:
        # no Authorization header — EventSource cannot send one).
        session_id = sessions.get(minter.tenant_id, "")
        if session_id:
            own = rig.open_stream(
                f"/sessions/{session_id}/events?ticket={ticket}",
                tenant=minter,
                auth=False,
            )
            rec.check(
                "m3",
                f"tenant {minter.tenant_id}: own ticket opens own SSE stream -> 200",
                own.status_code == 200
                and own.headers.get("content-type", "").startswith("text/event-stream"),
                f"HTTP {own.status_code}",
            )
            own.close()
            # Single-use: the SAME ticket replayed immediately is rejected.
            replay = rig.open_stream(
                f"/sessions/{session_id}/events?ticket={ticket}",
                tenant=minter,
                auth=False,
            )
            rec.check(
                "m3",
                f"tenant {minter.tenant_id}: ticket is single-use (replay -> 401)",
                replay.status_code == 401,
                f"HTTP {replay.status_code}",
            )
            replay.close()
        # Cross-tenant: the same minted ticket on the OTHER tenant's stream.
        status2, ticket2 = _mint(rig, minter)
        other_session = sessions.get(other.tenant_id, "")
        if status2 == 200 and other_session:
            crossed = rig.open_stream(
                f"/sessions/{other_session}/events?ticket={ticket2}",
                tenant=other,
                auth=False,
            )
            rec.check(
                "m3",
                f"{minter.tenant_id}'s ticket on {other.tenant_id}'s stream -> 401",
                crossed.status_code == 401,
                f"HTTP {crossed.status_code} (ticket store is per-gateway-process)",
            )
            crossed.close()
    # Unauthenticated minting is refused (the ticket endpoint is key-gated).
    anonymous = rig.request("POST", "/auth/sse-ticket", tenant=first, auth=False)
    rec.check(
        "m3",
        "minting without a key -> 401",
        anonymous.status_code == 401,
        f"HTTP {anonymous.status_code}",
    )
    # Bearer header path (non-browser clients) still works — T11 re-assert.
    bearer = rig.open_stream(
        f"/sessions/{sessions.get(first.tenant_id, 'x')}/events", tenant=first
    )
    rec.check(
        "m3",
        f"tenant {first.tenant_id}: Bearer-auth SSE stream -> 200",
        bearer.status_code == 200,
        f"HTTP {bearer.status_code}",
    )
    bearer.close()


class StreamWitness:
    """Collects every byte one SSE stream receives (thread-owned)."""

    def __init__(self, rig: Rig, tenant: TenantRig, session_id: str) -> None:
        self._rig = rig
        self._tenant = tenant
        self._session_id = session_id
        self.frames: list[tuple[float, bytes]] = []
        self.raw = b""
        self.status: int | None = None
        self._response: httpx.Response | None = None
        self._stop = False
        self._thread: object | None = None

    def __enter__(self) -> "StreamWitness":
        self._response = self._rig.open_stream(
            f"/sessions/{self._session_id}/events",
            tenant=self._tenant,
            timeout=600.0,
        )
        self.status = self._response.status_code
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()  # type: ignore[union-attr]
        return self

    def _drain(self) -> None:
        assert self._response is not None
        try:
            for chunk in self._response.iter_raw():
                arrived = time.time()
                self.raw += chunk
                self.frames.append((arrived, chunk))
                if self._stop:
                    break
        except httpx.HTTPError:
            pass

    def __exit__(self, *exc: object) -> None:
        self._stop = True
        if self._response is not None:
            self._response.close()

    def event_names(self) -> list[str]:
        names = []
        for _, frame in self.frames:
            for line in frame.decode("utf-8", "replace").splitlines():
                if line.startswith("event:"):
                    names.append(line[len("event:") :].strip())
        return names
