"""Frozen HTTP contracts for ``POST /state/export`` and ``POST /state/import``.

The dedicated entry app is driven with a real ``TestClient`` and its three
module-level service seams are monkeypatched, so each case asserts the wire
status and the exact top-level body without touching a real runtime root. Auth
is exercised through the real dependency, and a captured log proves neither the
service token nor the blob reaches a log line.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import strategy_run_server
from src.api import state_routes
from src.state_export import _run_report as run_report_module
from src.state_export._errors import BlobCorrupt, ExportUnstable, ImportFailed, ImportInProgress, UnknownRunId
from src.state_export._models import ImportResult
from src.state_export.manifest import LAYOUT_VERSION, BlobTooLarge, LayoutUnsupported, ManifestInvalid, PathEscape
from src.state_export.service import MAX_RUN_PAYLOAD_BYTES, export_run
from src.strategy_runs.store import StrategyRunStore

TOKEN = "dummy-service-token-state-routes-1f7c"
TOKEN_ENV = "RM_VT_STRATEGY_RUN_TOKEN_FILE"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RUN_FIELDS = {
    "vt_run_id",
    "strategy_id",
    "strategy_version",
    "status",
    "started_at",
    "finished_at",
    "title",
    "strategy",
    "metrics",
    "equity_curve",
    "trades",
    "report_markdown",
    "truncated",
}


class _FakeService:
    """Minimal strategy-run service; state routes never touch it."""

    async def submit(self, command):  # noqa: ANN001, ANN201 — duck-typed seam
        return SimpleNamespace(vt_run_id="vt-unused")

    def status(self, vt_run_id: str):  # noqa: ARG002 — duck-typed seam
        return None


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient runtime roots out of the route process."""
    for name in (
        "VIBE_TRADING_HOME",
        "VIBE_TRADING_STRATEGY_STORE_DB_PATH",
        "VIBE_TRADING_STRATEGY_DISCOVERY_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "rm_vt_strategy_run_token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(TOKEN_ENV, str(path))
    return path


@pytest.fixture()
def client(token_env: Path) -> TestClient:  # noqa: ARG001 — dependency ordering
    app = strategy_run_server.create_app(lambda: _FakeService())
    with TestClient(app) as test_client:
        yield test_client


def _bundle(blob: bytes = b"state-blob", digest: str = "a" * 64) -> SimpleNamespace:
    manifest = SimpleNamespace(layout_version=LAYOUT_VERSION, content_sha256=digest)
    return SimpleNamespace(
        blob=blob,
        manifest=manifest,
        layout_version=LAYOUT_VERSION,
        content_sha256=digest,
    )


def _post_export(client: TestClient, body: dict[str, object] | None = None) -> object:
    return client.post("/state/export", json=body or {"scope": "full"}, headers=AUTH)


def _post_import(
    client: TestClient,
    body: bytes = b"state-blob",
    *,
    layout: str | None = LAYOUT_VERSION,
) -> object:
    headers = dict(AUTH)
    if layout is not None:
        headers[state_routes.LAYOUT_HEADER] = layout
    return client.post("/state/import", content=body, headers=headers)


@pytest.mark.parametrize(
    ("headers", "status_code", "body"),
    [
        pytest.param({}, 401, {"detail": "unauthorized"}, id="missing"),
        pytest.param({"Authorization": "Bearer definitely-not-the-token"}, 403, {"detail": "forbidden"}, id="wrong"),
    ],
)
def test_auth_failures_have_fixed_bodies_on_both_routes(
    client: TestClient, headers: dict[str, str], status_code: int, body: dict[str, str]
) -> None:
    export_response = client.post("/state/export", json={"scope": "full"}, headers=headers)
    import_response = client.post("/state/import", content=b"blob", headers=headers)
    for response in (export_response, import_response):
        assert response.status_code == status_code
        assert response.json() == body
    if status_code == 401:
        assert export_response.headers["www-authenticate"] == "Bearer"


def test_full_export_returns_gzip_with_both_state_headers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "b" * 64
    monkeypatch.setattr(state_routes, "_export_full", lambda: _bundle(b"\x1f\x8bpayload", digest))

    response = _post_export(client)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/gzip")
    assert response.headers[state_routes.LAYOUT_HEADER] == LAYOUT_VERSION
    assert response.headers[state_routes.CONTENT_SHA_HEADER] == digest
    assert response.content == b"\x1f\x8bpayload"


def test_unknown_scope_and_missing_run_id_are_400(client: TestClient) -> None:
    unknown = _post_export(client, {"scope": "everything"})
    missing = _post_export(client, {"scope": "run"})
    assert unknown.status_code == 400
    assert unknown.json() == {"error": "unknown_scope"}
    assert missing.status_code == 400
    assert missing.json() == {"error": "missing_vt_run_id"}


def test_unknown_run_id_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def unknown(vt_run_id: str) -> dict[str, object]:
        raise UnknownRunId(f"unknown {vt_run_id}")

    monkeypatch.setattr(state_routes, "_export_run", unknown)

    response = _post_export(client, {"scope": "run", "vt_run_id": "vt-missing"})

    assert response.status_code == 404
    assert response.json() == {"error": "unknown_vt_run_id"}


@pytest.mark.parametrize(
    ("error", "status_code", "expected"),
    [
        pytest.param(ExportUnstable("tree changed"), 409, "export_unstable", id="export_unstable"),
        pytest.param(BlobTooLarge("over budget"), 413, "blob_too_large", id="blob_too_large"),
    ],
)
def test_export_error_table(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    expected: str,
) -> None:
    def fail() -> object:
        raise error

    monkeypatch.setattr(state_routes, "_export_full", fail)

    response = _post_export(client)

    assert response.status_code == status_code
    assert response.json() == {"error": expected}


def test_import_over_budget_is_413(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_routes, "MAX_BLOB_BYTES", 4)

    response = _post_import(client, b"five!")

    assert response.status_code == 413
    assert response.json() == {"error": "blob_too_large"}


def test_import_requires_a_layout_header(client: TestClient) -> None:
    response = _post_import(client, layout=None)

    assert response.status_code == 415
    assert response.json() == {"error": "layout_version_unsupported"}


@pytest.mark.parametrize(
    ("error", "status_code", "expected"),
    [
        pytest.param(LayoutUnsupported("bad layout"), 415, "layout_version_unsupported", id="layout"),
        pytest.param(ManifestInvalid("bad manifest"), 422, "manifest_invalid", id="manifest_invalid"),
        pytest.param(PathEscape("bad path"), 422, "path_escape", id="path_escape"),
        pytest.param(BlobCorrupt("bad bytes"), 422, "blob_corrupt", id="blob_corrupt"),
        pytest.param(BlobTooLarge("over budget"), 413, "blob_too_large", id="blob_too_large"),
        pytest.param(ImportInProgress("busy"), 409, "import_in_progress", id="import_in_progress"),
        pytest.param(ImportFailed("install failed"), 500, "import_failed", id="import_failed"),
    ],
)
def test_import_error_table(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    expected: str,
) -> None:
    def fail(blob: bytes, layout: str) -> object:  # noqa: ARG001 — seam signature
        raise error

    monkeypatch.setattr(state_routes, "_import_full", fail)

    response = _post_import(client, layout="vt-state-v2")

    assert response.status_code == status_code
    assert response.json() == {"error": expected}


def test_generic_export_and_import_failures_are_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_export() -> object:
        raise RuntimeError("boom")

    def fail_import(blob: bytes, layout: str) -> object:  # noqa: ARG001 — seam signature
        raise RuntimeError("boom")

    monkeypatch.setattr(state_routes, "_export_full", fail_export)
    monkeypatch.setattr(state_routes, "_import_full", fail_import)

    export_response = _post_export(client)
    import_response = _post_import(client)

    assert export_response.status_code == 500
    assert export_response.json() == {"error": "export_failed"}
    assert import_response.status_code == 500
    assert import_response.json() == {"error": "import_failed"}


def test_import_success_has_exactly_the_five_frozen_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = ImportResult(
        layout_version=LAYOUT_VERSION,
        content_sha256="c" * 64,
        strategies=2,
        versions=5,
        reopened=True,
    )
    monkeypatch.setattr(state_routes, "_import_full", lambda blob, layout: result)

    response = _post_import(client)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"layout_version", "content_sha256", "strategies", "versions", "reopened"}
    assert body["content_sha256"] == "c" * 64
    assert body["reopened"] is True
    assert "ok" not in body


def test_health_is_unchanged_and_the_dedicated_app_mounts_nothing_else(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "healthy"

    for method in ("get", "post"):
        schedules = getattr(client, method)("/strategy-schedules", headers=AUTH)
        assert schedules.status_code == 404
    assert client.get("/state/export", headers=AUTH).status_code == 405


def test_token_and_blob_never_reach_logs_or_responses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    marker = "blob-marker-4b9e-never-logged"
    caplog.set_level(logging.DEBUG)

    def leak(blob: bytes, layout: str) -> object:  # noqa: ARG001 — seam signature
        raise RuntimeError(f"{TOKEN} {marker}")

    monkeypatch.setattr(state_routes, "_import_full", leak)
    failure = _post_import(client, marker.encode("utf-8"))
    assert failure.status_code == 500

    monkeypatch.setattr(
        state_routes,
        "_import_full",
        lambda blob, layout: ImportResult(LAYOUT_VERSION, "d" * 64, 1, 1, True),
    )
    success = _post_import(client, marker.encode("utf-8"))
    assert success.status_code == 200

    combined = caplog.text + failure.text + success.text
    assert TOKEN not in combined
    assert marker not in combined
    assert TOKEN not in json.dumps(success.json())


def _seed_run(tmp_path: Path) -> tuple[Path, str]:
    """Create one completed ledger row and its run directory under ``tmp_path``."""
    root = tmp_path / "runtime"
    root.mkdir()
    strategy_id = "strat_momentum"
    strategy_dir = root / "strategies" / strategy_id
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.json").write_text(
        json.dumps({"name": "Momentum Alpha"}), encoding="utf-8"
    )
    store = StrategyRunStore(root / "strategy_runs.sqlite3")
    claim = store.claim("run-key-1", strategy_id, "v1-abc12345", None, None, 3600, now_ms=1_700_000_000_000)
    assert claim.won
    vt_run_id = claim.row.vt_run_id
    run_dir = root / "runs" / "scheduled" / f"svc-{vt_run_id}" / "job-1"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "run_card.json").write_text(
        json.dumps({"title": "Momentum run", "metrics": {"sharpe": 1.5}}), encoding="utf-8"
    )
    (run_dir / "artifacts" / "equity.csv").write_text("ts,value\n1,100\n2,101\n", encoding="utf-8")
    (run_dir / "artifacts" / "trades.csv").write_text("ts,side\n1,buy\n", encoding="utf-8")
    store.set_handle(vt_run_id, json.dumps({"run_dir": str(run_dir)}))
    store.mark_terminal(vt_run_id, "completed", now_ms=1_700_000_010_000)
    return root, vt_run_id


def test_run_scope_payload_has_the_thirteen_frozen_fields(tmp_path: Path) -> None:
    root, vt_run_id = _seed_run(tmp_path)

    payload = export_run(vt_run_id, runtime_root=root)

    assert set(payload) == RUN_FIELDS
    assert payload["vt_run_id"] == vt_run_id
    assert payload["strategy_id"] == "strat_momentum"
    assert payload["strategy_version"] == "v1-abc12345"
    assert payload["status"] == "completed"
    assert payload["strategy"] == "Momentum Alpha"
    assert payload["title"] == "Momentum run"
    assert payload["metrics"] == {"sharpe": 1.5}
    assert payload["equity_curve"] == [{"ts": 1, "value": 100}, {"ts": 2, "value": 101}]
    assert payload["trades"] == [{"ts": 1, "side": "buy"}]
    assert payload["truncated"] is False
    assert payload["finished_at"] is not None


def test_a_byte_capped_csv_reports_truncation_and_drops_the_partial_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, vt_run_id = _seed_run(tmp_path)
    csv_path = (
        root / "runs" / "scheduled" / f"svc-{vt_run_id}" / "job-1" / "artifacts" / "equity.csv"
    )
    csv_path.write_text("ts,value\n1,100\n2,101\n", encoding="utf-8")
    monkeypatch.setattr(run_report_module, "_MAX_SOURCE_FILE_BYTES", 18)

    payload = export_run(vt_run_id, runtime_root=root)

    assert payload["truncated"] is True
    assert payload["equity_curve"] == [{"ts": 1, "value": 100}]


def test_a_row_capped_csv_reports_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, vt_run_id = _seed_run(tmp_path)
    monkeypatch.setattr(run_report_module, "_MAX_CSV_ROWS", 1)

    payload = export_run(vt_run_id, runtime_root=root)

    assert payload["truncated"] is True
    assert payload["equity_curve"] == [{"ts": 1, "value": 100}]


def test_a_byte_capped_report_markdown_reports_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, vt_run_id = _seed_run(tmp_path)
    run_dir = root / "runs" / "scheduled" / f"svc-{vt_run_id}" / "job-1"
    (run_dir / "run_card.md").write_text("x" * 50, encoding="utf-8")
    monkeypatch.setattr(run_report_module, "_MAX_SOURCE_FILE_BYTES", 30)

    payload = export_run(vt_run_id, runtime_root=root)

    assert payload["truncated"] is True
    assert payload["report_markdown"] == "x" * 30


def test_run_route_serves_the_payload_and_truncates_an_oversized_artifact(
    tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, vt_run_id = _seed_run(tmp_path)
    monkeypatch.setattr(state_routes, "_export_run", lambda run_id: export_run(run_id, runtime_root=root))

    response = _post_export(client, {"scope": "run", "vt_run_id": vt_run_id})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == RUN_FIELDS
    assert body["truncated"] is False

    large_root = tmp_path / "large-runtime"
    large_root.mkdir()
    strategy_dir = large_root / "strategies" / "strat_momentum"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.json").write_text(json.dumps({"name": "Large"}), encoding="utf-8")
    store = StrategyRunStore(large_root / "strategy_runs.sqlite3")
    claim = store.claim("run-key-large", "strat_momentum", "v1-large123", None, None, 3600)
    run_dir = large_root / "runs" / "scheduled" / f"svc-{claim.row.vt_run_id}" / "job-1"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "run_card.json").write_text(json.dumps({"title": "Large run"}), encoding="utf-8")
    (run_dir / "run_card.md").write_text("x" * (3 * 1024 * 1024), encoding="utf-8")
    store.mark_terminal(claim.row.vt_run_id, "completed")

    large = export_run(claim.row.vt_run_id, runtime_root=large_root)

    assert large["truncated"] is True
    assert len(json.dumps(large, ensure_ascii=False).encode("utf-8")) <= MAX_RUN_PAYLOAD_BYTES
    assert json.loads(json.dumps(large, ensure_ascii=False)) == large
