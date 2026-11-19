"""Offline contracts for the six RM control-plane scheduling tools (0005).

Every test runs without a network or a real runtime root: a stateful
``httpx.MockTransport`` stands in for the RM agent API, the strategy store is a
throwaway SQLite database bound to the process singleton, and
``VIBE_TRADING_HOME`` points at ``tmp_path``. The behaviours pinned here are the
ones the assistant's honesty depends on:

* the six actions relay intent to RM and mirror RM's state;
* nothing is ever written to the deprecated in-box ``scheduled_research`` store;
* missing configuration fails closed instead of inventing a local schedule;
* a non-2xx RM response never renders as "scheduled";
* the bearer token never reaches a log, response, exception or ``repr``;
* the in-box entry mounts no strategy-schedule route and starts no scheduler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

import strategy_run_server
from src.agent_schedules import tools as schedule_tools
from src.agent_schedules.client import InvalidRequest, load_client
from src.scheduled_research.store import _STORE_FILENAME
from src.strategy_library.store import (
    create_draft,
    set_readiness,
    snapshot,
    update_draft,
)
from src.strategy_store import _shared
from src.strategy_store.sqlite_store import SqliteStrategyStore

AGENT_DIR = Path(__file__).resolve().parent.parent
_API_PREFIX = "/api/v1/agent/strategy-schedules"
_TOKEN = "dummy-agent-token-4b9e-never-real"
_NEXT_RUN = "2026-10-27T01:00:00Z"
_PARAMETER_NAMES = (
    "schedule_strategy",
    "list_strategy_schedules",
    "pause_strategy_schedule",
    "resume_strategy_schedule",
    "delete_strategy_schedule",
    "retarget_strategy_schedule",
)


# ---------------------------------------------------------------------------
# Fake RM control plane
# ---------------------------------------------------------------------------


class _FakeRm:
    """Stateful in-memory stand-in for the seven RM agent scheduling routes."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.fail_status: int | None = None
        self.fail_body: Any = None
        self._next_id = 0

    def fail(self, status: int, body: Any = None) -> None:
        """Make every subsequent request answer *status* with *body*."""
        self.fail_status = status
        self.fail_body = body

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Serve one request against the in-memory schedule table."""
        self.requests.append(request)
        if self.fail_status is not None:
            return httpx.Response(self.fail_status, json=self.fail_body, request=request)

        path = request.url.path
        if path == _API_PREFIX:
            if request.method == "GET":
                return httpx.Response(200, json=list(self.rows.values()), request=request)
            if request.method == "POST":
                return self._create(request)
            return httpx.Response(405, request=request)

        if not path.startswith(f"{_API_PREFIX}/"):
            return httpx.Response(404, request=request)

        parts = path[len(_API_PREFIX) + 1 :].split("/")
        schedule_id = parts[0]
        row = self.rows.get(schedule_id)
        if row is None:
            return httpx.Response(404, json={"detail": "not found"}, request=request)

        action = parts[1] if len(parts) > 1 else ""
        if not action and request.method == "GET":
            return httpx.Response(200, json=row, request=request)
        if not action and request.method == "DELETE":
            del self.rows[schedule_id]
            return httpx.Response(204, request=request)
        if action == "pause" and request.method == "POST":
            row["enabled"] = False
            row["next_run_at"] = "2026-10-28T01:00:00Z"
            return httpx.Response(200, json=row, request=request)
        if action == "resume" and request.method == "POST":
            row["enabled"] = True
            row["next_run_at"] = _NEXT_RUN
            return httpx.Response(200, json=row, request=request)
        if action == "retarget" and request.method == "POST":
            payload = json.loads(request.content or b"{}")
            version = payload["strategy_version"]
            row["strategy_version"] = version
            row["pinned_version"] = version
            return httpx.Response(
                200,
                json={"schedule_id": schedule_id, "pinned_version": version},
                request=request,
            )
        return httpx.Response(405, request=request)

    def _create(self, request: httpx.Request) -> httpx.Response:
        """Create one row from the posted schedule body."""
        payload = json.loads(request.content or b"{}")
        self._next_id += 1
        schedule_id = f"ss_{self._next_id:04d}"
        row = {
            "schedule_id": schedule_id,
            "strategy_id": payload["strategy_id"],
            "strategy_version": payload["strategy_version"],
            "pinned_version": payload["strategy_version"],
            "name": payload.get("name"),
            "kind": payload.get("kind"),
            "enabled": True,
            "next_run_at": _NEXT_RUN,
        }
        self.rows[schedule_id] = row
        return httpx.Response(
            200,
            json={**row, "created_at": "2026-10-26T00:00:00Z"},
            request=request,
        )


@dataclass(frozen=True)
class _Harness:
    """The fake RM plus the dummy credential a test can assert on."""

    rm: _FakeRm
    token: str
    token_path: Path
    environ: dict[str, str]


# ---------------------------------------------------------------------------
# Fixtures and local-library helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every runtime-root resolution at a per-test directory."""
    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("VIBE_TRADING_HOME", str(root))
    for name in (
        "VIBE_TRADING_STRATEGY_STORE_DB_PATH",
        "VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH",
        "RM_AGENT_API_URL",
        "RM_AGENT_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SqliteStrategyStore:
    """Bind a throwaway SQLite store to the process-wide singleton."""
    bound = SqliteStrategyStore(tmp_path / "strategy_store.db")
    monkeypatch.setattr(_shared, "_store", bound)
    return bound


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    """Install a client factory backed by the fake RM and a dummy token."""
    rm = _FakeRm()
    token_path = tmp_path / "agent-token"
    token_path.write_text(_TOKEN + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    environ = {
        "RM_AGENT_API_URL": "http://rm.test",
        "RM_AGENT_TOKEN_FILE": str(token_path),
        "HOME": str(tmp_path),
    }
    monkeypatch.setattr(
        schedule_tools,
        "_client_factory",
        lambda: load_client(environ=environ, transport=httpx.MockTransport(rm.handler)),
    )
    return _Harness(rm=rm, token=_TOKEN, token_path=token_path, environ=environ)


@pytest.fixture
def ready_code_strategy(store: SqliteStrategyStore, runtime_root: Path) -> tuple[str, str]:
    """A ready code strategy with one fully materialised version."""
    return _seed_ready(store, runtime_root, _code_spec())


def _code_spec(*, name: str = "Momentum Code", thesis: str | None = None) -> dict:
    return {
        "name": name,
        "kind": "code",
        "thesis": thesis or "Cross-sectional momentum on liquid names.",
        "universe": "cn_a",
        "inputs": [{"name": "prices", "source": "backtest", "required": True}],
    }


def _workflow_spec(name: str = "Momentum Workflow") -> dict:
    return {
        "name": name,
        "kind": "workflow",
        "thesis": "Buy strength after a volume-confirmed breakout.",
        "universe": "cn_a",
        "steps": [
            {
                "id": "s1",
                "action": "load prices",
                "tool": "get_market_data",
                "output": "price table",
            }
        ],
    }


def _seed_ready(store: SqliteStrategyStore, root: Path, spec: dict) -> tuple[str, str]:
    """Create a draft, materialise its version and mark it ready to schedule."""
    draft = create_draft(spec, store=store, runtime_root=root)
    if spec["kind"] == "code":
        base = root / "strategies" / draft.id
        (base / "code").mkdir(parents=True, exist_ok=True)
        (base / "config.json").write_text('{"source": "local"}\n', encoding="utf-8")
        (base / "code" / "signal_engine.py").write_text(
            "def generate_signals(frame):\n    return frame\n", encoding="utf-8"
        )
    version = snapshot(draft.id, store=store, runtime_root=root)
    set_readiness(draft.id, "ready", store=store)
    return draft.id, version


def _legacy_jobs_path(root: Path) -> Path:
    return root / "scheduled_research" / _STORE_FILENAME


def _write_legacy_jobs(root: Path, count: int = 2) -> Path:
    """Create a legacy in-box job store that must never be touched again."""
    path = _legacy_jobs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [{"id": f"legacy-{index}", "name": f"Legacy {index}", "schedule": "0 9 * * *"} for index in range(count)]
    path.write_text(json.dumps({"schema_version": 1, "jobs": jobs}), encoding="utf-8")
    return path


def _legacy_state(root: Path) -> dict[str, tuple[int, bytes]]:
    """Bytes and modification time of every file under ``scheduled_research``."""
    directory = root / "scheduled_research"
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _runtime_listing(root: Path) -> set[str]:
    """Every runtime file except the strategy fixtures and the store database."""
    listing: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("strategies/"):
            continue
        if relative.startswith("strategy_store.db"):
            continue
        listing.add(relative)
    return listing


def _run_all_six(strategy_id: str) -> list[dict[str, Any]]:
    """Call all six actions against an arbitrary schedule id."""
    return [
        schedule_tools.schedule_strategy(strategy_id=strategy_id, kind="cron", cron_spec="0 9 * * *"),
        schedule_tools.list_strategy_schedules(),
        schedule_tools.pause_strategy_schedule("ss_missing"),
        schedule_tools.resume_strategy_schedule("ss_missing"),
        schedule_tools.retarget_strategy_schedule("ss_missing"),
        schedule_tools.delete_strategy_schedule("ss_missing"),
    ]


def _run_python(script: str, workdir: Path, **extra_env: str) -> subprocess.CompletedProcess:
    """Run *script* in a clean child whose HOME and cwd are throwaways."""
    home = workdir / "home"
    cwd = workdir / "cwd"
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "PYTHONPATH": str(AGENT_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


# ---------------------------------------------------------------------------
# B1: the six actions round-trip against RM
# ---------------------------------------------------------------------------


def test_six_actions_round_trip_against_the_fake_rm(harness: _Harness, ready_code_strategy: tuple[str, str]) -> None:
    strategy_id, version = ready_code_strategy

    created = schedule_tools.schedule_strategy(
        strategy_id=strategy_id,
        kind="cron",
        name="Morning run",
        cron_spec="0 9 * * *",
        tz="Asia/Shanghai",
    )
    assert created["ok"] is True
    schedule_id = created["schedule_id"]
    row = harness.rm.rows[schedule_id]
    assert created["next_run_at"] == row["next_run_at"] == _NEXT_RUN
    assert created["pinned_version"] == row["pinned_version"] == version

    request = harness.rm.requests[0]
    assert request.method == "POST"
    assert request.url.path == _API_PREFIX
    assert json.loads(request.content) == {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "name": "Morning run",
        "kind": "cron",
        "tz": "Asia/Shanghai",
        "cron_spec": "0 9 * * *",
    }

    listed = schedule_tools.list_strategy_schedules()
    assert listed["ok"] is True
    entries = listed["schedules"]
    assert [entry["schedule_id"] for entry in entries] == [schedule_id]
    assert entries[0]["pinned_version"] == version
    assert entries[0]["current_version"] == version
    assert entries[0]["drift"] is False

    paused = schedule_tools.pause_strategy_schedule(schedule_id)
    assert paused == {
        "ok": True,
        "schedule_id": schedule_id,
        "enabled": False,
        "next_run_at": row["next_run_at"],
    }
    assert harness.rm.rows[schedule_id]["enabled"] is False

    resumed = schedule_tools.resume_strategy_schedule(schedule_id)
    assert resumed == {
        "ok": True,
        "schedule_id": schedule_id,
        "enabled": True,
        "next_run_at": harness.rm.rows[schedule_id]["next_run_at"],
    }

    retargeted = schedule_tools.retarget_strategy_schedule(schedule_id, strategy_version=version)
    assert retargeted == {
        "ok": True,
        "schedule_id": schedule_id,
        "pinned_version": version,
    }

    deleted = schedule_tools.delete_strategy_schedule(schedule_id)
    assert deleted == {"ok": True, "schedule_id": schedule_id, "deleted": True}
    assert schedule_id not in harness.rm.rows
    assert schedule_tools.list_strategy_schedules()["schedules"] == []

    blob = json.dumps([created, listed, paused, resumed, retargeted, deleted], ensure_ascii=False)
    assert harness.token not in blob


def test_a_workflow_strategy_schedules_once_its_spec_is_snapshotted(
    harness: _Harness, store: SqliteStrategyStore, runtime_root: Path
) -> None:
    strategy_id, version = _seed_ready(store, runtime_root, _workflow_spec())

    created = schedule_tools.schedule_strategy(strategy_id=strategy_id, kind="daily", time_of_day="09:00")

    assert created["ok"] is True
    assert created["pinned_version"] == version
    assert json.loads(harness.rm.requests[0].content)["time_of_day"] == "09:00"


# ---------------------------------------------------------------------------
# B2: zero local writes
# ---------------------------------------------------------------------------


def test_six_actions_write_nothing_locally(
    harness: _Harness,
    ready_code_strategy: tuple[str, str],
    runtime_root: Path,
) -> None:
    strategy_id, version = ready_code_strategy
    _write_legacy_jobs(runtime_root, 2)
    legacy_before = _legacy_state(runtime_root)
    listing_before = _runtime_listing(runtime_root)

    created = schedule_tools.schedule_strategy(strategy_id=strategy_id, kind="cron", cron_spec="0 9 * * *")
    schedule_id = created["schedule_id"]
    schedule_tools.list_strategy_schedules()
    schedule_tools.pause_strategy_schedule(schedule_id)
    schedule_tools.resume_strategy_schedule(schedule_id)
    schedule_tools.retarget_strategy_schedule(schedule_id, strategy_version=version)
    schedule_tools.delete_strategy_schedule(schedule_id)

    assert _legacy_state(runtime_root) == legacy_before
    assert _runtime_listing(runtime_root) == listing_before


# ---------------------------------------------------------------------------
# B3: fail-closed configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["missing_url", "missing_token"])
def test_missing_configuration_fails_closed_without_local_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store: SqliteStrategyStore,
    runtime_root: Path,
    mode: str,
) -> None:
    strategy_id, _version = _seed_ready(store, runtime_root, _code_spec())
    _write_legacy_jobs(runtime_root, 2)
    before = _legacy_state(runtime_root)
    rm = _FakeRm()
    token_path = tmp_path / "agent-token"
    token_path.write_text(_TOKEN + "\n", encoding="utf-8")

    if mode == "missing_url":
        environ = {"RM_AGENT_TOKEN_FILE": str(token_path), "HOME": str(tmp_path)}
    else:
        environ = {
            "RM_AGENT_API_URL": "http://rm.test",
            "RM_AGENT_TOKEN_FILE": str(tmp_path / "absent-token"),
            "HOME": str(tmp_path),
        }
    monkeypatch.setattr(
        schedule_tools,
        "_client_factory",
        lambda: load_client(environ=environ, transport=httpx.MockTransport(rm.handler)),
    )

    results = _run_all_six(strategy_id)

    for result in results:
        assert result["ok"] is False
        assert result["error"] == "scheduling_unavailable"
        assert "schedule_id" not in result
    assert rm.requests == []
    assert _legacy_state(runtime_root) == before


# ---------------------------------------------------------------------------
# B4: no false success
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {"detail": "denied"}, "unauthorized"),
        (403, {"detail": "denied"}, "unauthorized"),
        (404, {"detail": "gone"}, "not_found"),
        (409, {"error": "schedule_limit_reached"}, "schedule_limit_reached"),
        (409, {"detail": "schedule_limit_reached"}, "schedule_limit_reached"),
        (409, {"error": "conflict"}, "invalid_request"),
        (422, {"detail": "cadence rejected"}, "invalid_request"),
        (500, {"detail": "boom"}, "rm_unreachable"),
        (503, {"error": "scheduler_disabled"}, "scheduling_unavailable"),
    ],
)
def test_rm_failures_never_render_as_a_successful_schedule(
    harness: _Harness, status: int, body: dict[str, str], code: str
) -> None:
    harness.rm.fail(status, body)

    result = schedule_tools.pause_strategy_schedule("ss_01")
    text = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is False
    assert result["error"] == code
    assert "schedule_id" not in result
    assert result.get("ok") is not True
    for forbidden in ("已排程", "created", "success"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# B5: token hygiene
# ---------------------------------------------------------------------------


def test_token_never_reaches_headers_logs_repr_or_responses(
    harness: _Harness,
    ready_code_strategy: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    strategy_id, _version = ready_code_strategy
    caplog.set_level(logging.DEBUG, logger="src.agent_schedules")

    created = schedule_tools.schedule_strategy(strategy_id=strategy_id, kind="interval", interval_s=3600)
    assert created["ok"] is True
    request = harness.rm.requests[0]
    assert request.headers["authorization"] == f"Bearer {harness.token}"
    assert "x-sandbox-id" not in request.headers

    harness.rm.fail(
        422,
        {"detail": f"credential={harness.token} Bearer {harness.token} rejected"},
    )
    failed = schedule_tools.pause_strategy_schedule("ss_01")
    assert failed["error"] == "invalid_request"
    assert "[redacted]" in failed["detail"]

    client = load_client(
        environ=harness.environ,
        transport=httpx.MockTransport(harness.rm.handler),
    )
    assert harness.token not in repr(client)

    with pytest.raises(InvalidRequest) as excinfo:
        client.pause("ss_01")
    assert harness.token not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)

    assert harness.token not in json.dumps([created, failed], ensure_ascii=False)
    assert harness.token not in caplog.text


# ---------------------------------------------------------------------------
# B6: the in-box entry has no scheduler and no schedule routes
# ---------------------------------------------------------------------------


def test_production_api_keeps_strategy_schedule_routes_off_by_default(
    tmp_path: Path,
) -> None:
    script = (
        "import json, api_server; "
        "print(json.dumps(sorted(p for p in api_server.app.openapi()['paths'] "
        "if 'strategy-schedules' in p)))"
    )

    off = _run_python(script, tmp_path / "routes-off")
    on = _run_python(script, tmp_path / "routes-on", VT_STRATEGY_SCHEDULE_ROUTES_ENABLED="1")

    assert off.returncode == 0, off.stderr
    assert on.returncode == 0, on.stderr
    assert json.loads(off.stdout) == []
    assert json.loads(on.stdout) == [
        "/strategy-schedules",
        "/strategy-schedules/{job_id}",
        "/strategy-schedules/{job_id}/run-now",
        "/strategy-schedules/{job_id}/runs",
    ]


def test_entry_app_starts_no_scheduler_and_mounts_no_schedule_routes() -> None:
    source = (AGENT_DIR / "strategy_run_server.py").read_text(encoding="utf-8")
    assert "_start_scheduled_research_executor" not in source

    app = strategy_run_server.create_app(lambda: object())
    paths = set(app.openapi()["paths"])
    assert not any("strategy-schedules" in path for path in paths)


# ---------------------------------------------------------------------------
# B7: drift and retarget
# ---------------------------------------------------------------------------


def test_drift_is_reported_and_retarget_re_pins_the_same_schedule(
    harness: _Harness, store: SqliteStrategyStore, runtime_root: Path
) -> None:
    strategy_id, first_version = _seed_ready(store, runtime_root, _code_spec())
    harness.rm.rows["ss_drift"] = {
        "schedule_id": "ss_drift",
        "strategy_id": strategy_id,
        "strategy_version": first_version,
        "pinned_version": first_version,
        "name": "Drifting schedule",
        "kind": "cron",
        "enabled": True,
        "next_run_at": _NEXT_RUN,
    }

    before = schedule_tools.list_strategy_schedules()["schedules"][0]
    assert before["current_version"] == first_version
    assert before["drift"] is False

    updated = update_draft(
        strategy_id,
        _code_spec(thesis="A newer momentum thesis."),
        store=store,
        runtime_root=runtime_root,
    )
    second_version = updated.artifact_version
    assert second_version != first_version
    assert snapshot(strategy_id, store=store, runtime_root=runtime_root) == second_version
    set_readiness(strategy_id, "ready", store=store)

    drifted = schedule_tools.list_strategy_schedules()["schedules"][0]
    assert drifted["pinned_version"] == first_version
    assert drifted["current_version"] == second_version
    assert drifted["drift"] is True

    retargeted = schedule_tools.retarget_strategy_schedule("ss_drift")
    assert retargeted == {
        "ok": True,
        "schedule_id": "ss_drift",
        "pinned_version": second_version,
    }
    retarget_request = harness.rm.requests[-1]
    assert retarget_request.url.path == f"{_API_PREFIX}/ss_drift/retarget"
    assert json.loads(retarget_request.content) == {"strategy_version": second_version}
    assert harness.rm.rows["ss_drift"]["pinned_version"] == second_version

    settled = schedule_tools.list_strategy_schedules()["schedules"][0]
    assert settled["schedule_id"] == "ss_drift"
    assert settled["drift"] is False
    assert settled["pinned_version"] == second_version


def test_list_passes_through_the_rm_last_run(
    harness: _Harness, ready_code_strategy: tuple[str, str]
) -> None:
    strategy_id, _version = ready_code_strategy
    created = schedule_tools.schedule_strategy(
        strategy_id=strategy_id,
        kind="cron",
        cron_spec="0 9 * * *",
        name="Morning run",
    )
    schedule_id = created["schedule_id"]
    last_run = {
        "status": "missed",
        "scheduled_for": "2026-10-27T01:00:00Z",
        "started_at": None,
        "finished_at": None,
        "error": "scheduler was down",
    }
    harness.rm.rows[schedule_id]["last_run"] = last_run

    listed = schedule_tools.list_strategy_schedules()["schedules"][0]
    assert listed["last_run"] == last_run

    harness.rm.rows[schedule_id].pop("last_run")
    without = schedule_tools.list_strategy_schedules()["schedules"][0]
    assert without["last_run"] is None


# ---------------------------------------------------------------------------
# B9: the legacy job store is counted read-only
# ---------------------------------------------------------------------------


def test_legacy_jobs_are_counted_and_never_modified(harness: _Harness, runtime_root: Path) -> None:
    _write_legacy_jobs(runtime_root, 2)
    before = _legacy_state(runtime_root)

    listed = schedule_tools.list_strategy_schedules()

    assert listed["legacy_jobs_ignored"] == 2
    assert _legacy_state(runtime_root) == before

    legacy_path = _legacy_jobs_path(runtime_root)
    legacy_path.write_text("{not json", encoding="utf-8")
    corrupt_before = _legacy_state(runtime_root)

    corrupt = schedule_tools.list_strategy_schedules()

    assert corrupt["legacy_jobs_ignored"] == 1
    assert _legacy_state(runtime_root) == corrupt_before


def test_a_legacy_directory_without_the_store_file_is_still_reported(
    harness: _Harness, runtime_root: Path
) -> None:
    legacy_dir = runtime_root / "scheduled_research"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "leftover-job.json").write_text("{}", encoding="utf-8")
    before = _legacy_state(runtime_root)

    listed = schedule_tools.list_strategy_schedules()

    assert listed["legacy_jobs_ignored"] == 1
    assert _legacy_state(runtime_root) == before


# ---------------------------------------------------------------------------
# Local validation fails before RM is contacted
# ---------------------------------------------------------------------------


def test_unknown_strategy_never_calls_rm(harness: _Harness) -> None:
    result = schedule_tools.schedule_strategy(strategy_id="ghost", kind="cron", cron_spec="0 9 * * *")

    assert result["ok"] is False
    assert result["error"] == "strategy_not_found"
    assert harness.rm.requests == []


def test_non_ready_strategy_never_calls_rm(harness: _Harness, store: SqliteStrategyStore, runtime_root: Path) -> None:
    draft = create_draft(_code_spec(name="Not Ready"), store=store, runtime_root=runtime_root)

    result = schedule_tools.schedule_strategy(strategy_id=draft.id, kind="cron", cron_spec="0 9 * * *")

    assert result["ok"] is False
    assert result["error"] == "strategy_not_ready"
    assert harness.rm.requests == []


def test_missing_version_never_calls_rm(harness: _Harness, ready_code_strategy: tuple[str, str]) -> None:
    strategy_id, _version = ready_code_strategy

    result = schedule_tools.schedule_strategy(
        strategy_id=strategy_id,
        kind="cron",
        cron_spec="0 9 * * *",
        strategy_version="v9-deadbeef",
    )

    assert result["ok"] is False
    assert result["error"] == "version_not_found"
    assert harness.rm.requests == []


@pytest.mark.parametrize(
    "cadence",
    [
        {"kind": "hourly"},
        {"kind": "cron", "cron_spec": "0 9 * *"},
        {"kind": "interval", "interval_s": 0},
        {"kind": "interval", "interval_s": True},
        {"kind": "daily", "time_of_day": "24:00"},
    ],
)
def test_invalid_cadence_is_refused_before_rm(
    harness: _Harness,
    ready_code_strategy: tuple[str, str],
    cadence: dict[str, Any],
) -> None:
    strategy_id, _version = ready_code_strategy

    result = schedule_tools.schedule_strategy(strategy_id=strategy_id, **cadence)

    assert result["ok"] is False
    assert result["error"] == "invalid_request"
    assert harness.rm.requests == []


# ---------------------------------------------------------------------------
# MCP wrappers
# ---------------------------------------------------------------------------


def test_mcp_registers_all_six_scheduling_tools() -> None:
    import mcp_server

    names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}

    assert set(_PARAMETER_NAMES) <= names


def test_mcp_wrapper_returns_the_action_envelope(harness: _Harness) -> None:
    import mcp_server

    payload = json.loads(mcp_server.list_strategy_schedules())

    assert payload["ok"] is True
    assert payload["schedules"] == []
    assert payload["legacy_jobs_ignored"] == 0


def test_mcp_wrapper_fails_closed_when_the_module_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mcp_server

    monkeypatch.setitem(sys.modules, "src.agent_schedules.tools", None)

    payload = json.loads(mcp_server.schedule_strategy(strategy_id="stg-001", kind="cron"))

    assert payload["ok"] is False
    assert payload["error"] == "scheduling_unavailable"
