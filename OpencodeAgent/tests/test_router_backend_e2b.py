"""E2bSandboxBackend tests (plan T16): the sandbox-ctl shell-out contract.

Everything is mocked at the subprocess layer — no CLI, no gateway, no network.
Pins the mapping table from the sandbox-ctl README ("Router integration
contract"): exit codes 0/3/4/5/6 and the strict-JSON stdout shapes onto the
ContainerBackend conventions the docker path already established, so
wake.py/reclaim.py keep working without a single line of consumer change.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from router.backend import BackendUnavailable, E2bSandboxBackend, build_backend

CTL = "/fake/venv/bin/e2b-sandbox-ctl"
SID = "default--opencode-poc-z8n52"

# exit codes from the CLI contract (sandbox_ctl/output.py Exit enum)
OK, UNAVAILABLE, ABSENT, OP_FAILED, AUTH = 0, 3, 4, 5, 6


class FakeProc:
    """Stands in for asyncio.subprocess.Process around communicate()."""

    def __init__(self, code: int, out: str, err: str = "", hang: bool = False):
        self._code, self._out, self._err, self._hang = code, out, err, hang
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(self._hang)
        self.returncode = self._code
        return self._out.encode(), self._err.encode()

    def kill(self) -> None:
        self.killed = True


class SpawnRecorder:
    """Patch target for asyncio.create_subprocess_exec."""

    def __init__(self, proc: FakeProc):
        self.proc = proc
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, exe: str, *args: str, **kwargs: object) -> FakeProc:
        self.calls.append((exe, *args))
        return self.proc


def backend(**timeouts) -> E2bSandboxBackend:
    return E2bSandboxBackend(ctl_bin=CTL, **timeouts)


def run(coro):
    return asyncio.run(coro)


def spawn(monkeypatch, proc: FakeProc) -> SpawnRecorder:
    recorder = SpawnRecorder(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    return recorder


def ok_doc(payload: dict) -> FakeProc:
    return FakeProc(OK, json.dumps(payload))


def fail_doc(code: int, name: str, message: str) -> FakeProc:
    return FakeProc(
        code, json.dumps({"ok": False, "error": {"code": name, "message": message}})
    )


# --- construction / selection -------------------------------------------------


def test_build_backend_selects_the_e2b_case() -> None:
    assert isinstance(build_backend("e2b"), E2bSandboxBackend)


def test_ctl_bin_defaults_to_the_stable_sandbox_ctl_venv(monkeypatch) -> None:
    monkeypatch.delenv("VT_ROUTER_E2B_CTL_BIN", raising=False)
    sut = E2bSandboxBackend()
    assert sut._ctl == os.path.expanduser(
        "~/.acs-sandbox/sandbox-ctl-venv/bin/e2b-sandbox-ctl"
    )


def test_env_var_overrides_the_ctl_path(monkeypatch) -> None:
    monkeypatch.setenv("VT_ROUTER_E2B_CTL_BIN", "~/custom/e2b-sandbox-ctl")
    sut = E2bSandboxBackend()
    assert sut._ctl == os.path.expanduser("~/custom/e2b-sandbox-ctl")


def test_explicit_argument_beats_the_env(monkeypatch) -> None:
    monkeypatch.setenv("VT_ROUTER_E2B_CTL_BIN", "/from/env")
    sut = E2bSandboxBackend(ctl_bin="/from/arg")
    assert sut._ctl == "/from/arg"


def test_tilde_in_env_is_expanded(monkeypatch) -> None:
    monkeypatch.setenv("VT_ROUTER_E2B_CTL_BIN", "~/x/bin/e2b-sandbox-ctl")
    assert not E2bSandboxBackend()._ctl.startswith("~")


# --- inspect_state ------------------------------------------------------------


def test_state_running_is_returned_verbatim(monkeypatch) -> None:
    recorder = spawn(monkeypatch, ok_doc({"state": "running", "raw": {"x": 1}}))
    assert run(backend().inspect_state(SID)) == "running"
    assert recorder.calls == [(CTL, "state", SID)]


def test_state_paused_maps_to_the_docker_stopped_equivalent(monkeypatch) -> None:
    # wake.py starts anything non-"running"; reclaim.py skips non-"running";
    # docker answers "exited" for a stopped container — paused is that state.
    spawn(monkeypatch, ok_doc({"state": "paused", "raw": {"x": 1}}))
    assert run(backend().inspect_state(SID)) == "exited"


def test_state_absent_is_none(monkeypatch) -> None:
    spawn(monkeypatch, ok_doc({"state": "absent", "raw": None}))
    assert run(backend().inspect_state(SID)) is None


def test_state_backend_unavailable_raises(monkeypatch) -> None:
    spawn(monkeypatch, fail_doc(UNAVAILABLE, "backend_unavailable", "gateway dead"))
    with pytest.raises(BackendUnavailable, match="gateway dead"):
        run(backend().inspect_state(SID))


def test_state_other_nonzero_exits_are_conservatively_none(monkeypatch) -> None:
    # Absent/op-failed/auth are NOT "unreachable backend": never raise here or
    # wake would blame the backend for a tenant whose sandbox simply is not.
    for proc in (
        fail_doc(ABSENT, "sandbox_absent", "no such sandbox"),
        fail_doc(OP_FAILED, "operation_failed", "probe refused"),
        fail_doc(AUTH, "auth_failure", "key rejected"),
        FakeProc(2, "", "usage error"),
    ):
        spawn(monkeypatch, proc)
        assert run(backend().inspect_state(SID)) is None


def test_state_malformed_json_raises_backend_unavailable(monkeypatch) -> None:
    # exit 0 but stdout is not the promised strict JSON document.
    spawn(monkeypatch, FakeProc(OK, "not-json-at-all", "traceback on stderr"))
    with pytest.raises(BackendUnavailable, match="JSON contract"):
        run(backend().inspect_state(SID))


def test_state_json_non_object_raises(monkeypatch) -> None:
    spawn(monkeypatch, FakeProc(OK, "[1,2]"))
    with pytest.raises(BackendUnavailable, match="non-object"):
        run(backend().inspect_state(SID))


def test_state_timeout_kills_the_cli_and_raises(monkeypatch) -> None:
    proc = FakeProc(OK, "", hang=5.0)
    spawn(monkeypatch, proc)
    with pytest.raises(BackendUnavailable, match="timed out"):
        run(backend(state_timeout_s=0.05).inspect_state(SID))
    assert proc.killed is True


def test_missing_ctl_binary_raises_backend_unavailable(monkeypatch) -> None:
    async def refuse(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", str(args[0]))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
    with pytest.raises(BackendUnavailable, match="cannot exec"):
        run(backend().inspect_state(SID))


# --- start --------------------------------------------------------------------


def test_start_success_returns_none(monkeypatch) -> None:
    recorder = spawn(
        monkeypatch,
        ok_doc(
            {
                "started": True,
                "connect_s": 0.63,
                "route_ready_s": 0.15,
                "route_attempts": 1,
                "relaunch_fired": False,
            }
        ),
    )
    assert run(backend().start(SID)) is None
    assert recorder.calls == [(CTL, "start", SID)]


def test_start_unavailable_raises(monkeypatch) -> None:
    spawn(monkeypatch, fail_doc(UNAVAILABLE, "backend_unavailable", "sdk dead"))
    with pytest.raises(BackendUnavailable, match="sdk dead"):
        run(backend().start(SID))


def test_start_absent_raises_like_docker_start_failure(monkeypatch) -> None:
    # DockerCliBackend raises on any non-zero docker start; wake converts the
    # BackendUnavailable into the 503 + Retry-After fallback page.
    spawn(monkeypatch, fail_doc(ABSENT, "sandbox_absent", "not found"))
    with pytest.raises(BackendUnavailable, match=r"start failed \(exit 4\)"):
        run(backend().start(SID))


def test_start_route_budget_exhausted_raises_for_the_wake_page(monkeypatch) -> None:
    spawn(monkeypatch, fail_doc(OP_FAILED, "operation_failed", "route budget"))
    with pytest.raises(BackendUnavailable, match="exit 5"):
        run(backend().start(SID))


def test_start_timeout_raises_backend_unavailable(monkeypatch) -> None:
    proc = FakeProc(OK, "", hang=5.0)
    spawn(monkeypatch, proc)
    with pytest.raises(BackendUnavailable, match="timed out"):
        run(backend(start_timeout_s=0.05).start(SID))
    assert proc.killed is True


# --- stop ---------------------------------------------------------------------


def test_stop_success_returns_none(monkeypatch) -> None:
    recorder = spawn(
        monkeypatch,
        ok_doc({"paused": True, "already_paused": False, "pause_api_s": 3.0}),
    )
    assert run(backend().stop(SID)) is None
    assert recorder.calls == [(CTL, "stop", SID)]


def test_stop_already_paused_is_idempotent_ok(monkeypatch) -> None:
    spawn(monkeypatch, ok_doc({"paused": True, "already_paused": True}))
    assert run(backend().stop(SID)) is None


def test_stop_unavailable_raises(monkeypatch) -> None:
    spawn(monkeypatch, fail_doc(UNAVAILABLE, "backend_unavailable", "gateway dead"))
    with pytest.raises(BackendUnavailable, match="gateway dead"):
        run(backend().stop(SID))


def test_stop_other_nonzero_raises(monkeypatch) -> None:
    spawn(monkeypatch, fail_doc(OP_FAILED, "operation_failed", "pause refused"))
    with pytest.raises(BackendUnavailable, match="exit 5"):
        run(backend().stop(SID))


# --- engine_get ---------------------------------------------------------------


def test_engine_get_200_returns_the_body_string(monkeypatch) -> None:
    recorder = spawn(
        monkeypatch,
        ok_doc({"status": 200, "body": "[]", "password_path": "envs-param"}),
    )
    assert run(backend().engine_get(SID, "/session")) == "[]"
    assert recorder.calls == [(CTL, "engine-get", SID, "/session")]


def test_engine_get_paused_is_none_never_a_wake(monkeypatch) -> None:
    # exit 0 + {"status": null, "unavailable": "paused"} is DATA: the truth
    # check reads None and reclaims nothing it cannot verify (fail closed).
    spawn(monkeypatch, ok_doc({"status": None, "body": None, "unavailable": "paused"}))
    assert run(backend().engine_get(SID, "/session?limit=1&roots=true")) is None


def test_engine_get_non_200_is_none(monkeypatch) -> None:
    spawn(monkeypatch, ok_doc({"status": 502, "body": "<html>", "error": {}}))
    assert run(backend().engine_get(SID, "/session")) is None


def test_engine_get_empty_body_is_none(monkeypatch) -> None:
    spawn(monkeypatch, ok_doc({"status": 200, "body": "", "password_path": "env"}))
    assert run(backend().engine_get(SID, "/session")) is None


def test_engine_get_unavailable_raises(monkeypatch) -> None:
    spawn(monkeypatch, fail_doc(UNAVAILABLE, "backend_unavailable", "envd dead"))
    with pytest.raises(BackendUnavailable, match="envd dead"):
        run(backend().engine_get(SID, "/session"))


def test_engine_get_other_nonzero_is_none(monkeypatch) -> None:
    # mirrors `curl -sf` fail-silent: reclaim treats None as "engine silent".
    spawn(monkeypatch, fail_doc(OP_FAILED, "operation_failed", "command error"))
    assert run(backend().engine_get(SID, "/session")) is None


def test_engine_get_malformed_json_raises(monkeypatch) -> None:
    spawn(monkeypatch, FakeProc(OK, "{oops", "stderr detail here"))
    with pytest.raises(BackendUnavailable, match="stderr detail here"):
        run(backend().engine_get(SID, "/session"))
