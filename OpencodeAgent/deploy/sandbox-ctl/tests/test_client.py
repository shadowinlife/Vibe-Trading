"""Client-layer tests against a fully mocked SDK + gateway (no live calls)."""

from __future__ import annotations

import httpx
import pytest
from e2b.exceptions import (
    AuthenticationException,
    SandboxNotFoundException,
    TimeoutException,
)

from sandbox_ctl import client as C
from sandbox_ctl.client import SandboxClient
from sandbox_ctl.output import CtlError, Exit


class FakeResult:
    def __init__(self, stdout="", stderr="", exit_code=0):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class FakeSandbox:
    """Scripted stand-in for the patched e2b Sandbox (classmethod surface)."""

    script: dict = {}
    last: "FakeSandbox | None" = None

    def __init__(self, sid="sid-1"):
        self.sandbox_id = sid
        self.sandbox_domain = "localhost:8888"
        self.envd_calls: list[tuple[str, dict]] = []
        self.set_timeout_calls: list[int] = []
        FakeSandbox.last = self

    # -- envd channel -------------------------------------------------------

    class _Commands:
        def __init__(self, owner):
            self._owner = owner

        def run(self, cmd, **kw):
            self._owner.envd_calls.append((cmd, kw))
            handler = FakeSandbox.script.get("commands_run")
            if handler:
                return handler(cmd, kw, self._owner)
            return FakeResult(stdout="ok")

    @property
    def commands(self):
        return FakeSandbox._Commands(self)

    def set_timeout(self, timeout, **opts):
        # Dual form mirrors the SDK's @class_method_variant: the instance call
        # (sbx.set_timeout(n), used by start) arrives with self=instance; the
        # class call (Sandbox.set_timeout(sid, n), used by the set-timeout op)
        # arrives with self=sandbox_id.
        if isinstance(self, FakeSandbox):
            self.set_timeout_calls.append(timeout)
            sid = self.sandbox_id
        else:
            sid = self
        FakeSandbox.script.setdefault("timeout_calls", []).append((sid, timeout))
        handler = FakeSandbox.script.get("set_timeout")
        if handler:
            handler(sid, timeout)

    # -- control plane classmethods ------------------------------------------

    @classmethod
    def create(cls, template=None, timeout=None, request_timeout=None, **opts):
        cls.script.setdefault("create_calls", []).append(
            {
                "template": template,
                "timeout": timeout,
                "request_timeout": request_timeout,
                "opts": opts,
            }
        )
        return cls("new-sandbox")

    @classmethod
    def connect(cls, sandbox_id, **opts):
        cls.script.setdefault("connect_calls", []).append((sandbox_id, opts))
        h = cls.script.get("connect")
        if h:
            return h(sandbox_id)
        return cls(sandbox_id)

    @classmethod
    def get_info(cls, sandbox_id, **opts):
        h = cls.script.get("get_info")
        if h:
            return h(sandbox_id)
        raise AssertionError("get_info not scripted")

    @classmethod
    def beta_pause(cls, sandbox_id, keep_memory=True, **opts):
        cls.script.setdefault("pause_calls", []).append({"keep_memory": keep_memory})
        h = cls.script.get("beta_pause")
        if h:
            return h(sandbox_id)
        return True

    @classmethod
    def kill(cls, sandbox_id, **opts):
        cls.script.setdefault("kill_calls", []).append(sandbox_id)
        h = cls.script.get("kill")
        if h:
            return h(sandbox_id)
        return True


class FakeInfo:
    def __init__(self, state):
        class _State:
            value = state

        self.state = _State()
        self.sandbox_id = "sid-1"
        self.template_id = "opencode-poc"
        self.name = None
        self.started_at = None
        self.end_at = None
        self.cpu_count = 2
        self.memory_mb = 4096
        self.envd_version = "0.5.2"
        self.metadata = {}


def make_response(status: int, body: bytes = b"") -> httpx.Response:
    request = httpx.Request("GET", "http://localhost:8888/kruise/sid-1/4096/session")
    return httpx.Response(status, content=body, request=request)


class RouteStub:
    """Fake httpx.Client for poll_route; cycles a list of responses/excursions."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.closed = False

    def __call__(self, auth=None, timeout=None):
        self.auth = auth
        self.timeout = timeout
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def get(self, url):
        self.calls += 1
        out = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return out


SERVE_PID1 = "opencode serve --hostname 0.0.0.0 --port 4096\x00"


@pytest.fixture
def client(monkeypatch, tmp_path):
    FakeSandbox.script = {"create_calls": [], "connect_calls": [], "pause_calls": [], "kill_calls": []}
    FakeSandbox.last = None
    monkeypatch.setattr(C, "Sandbox", FakeSandbox)
    pw = tmp_path / "pw"
    pw.write_text("sekret\n")
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    return SandboxClient(api_key="k", password_file=str(pw))


def stub_route(monkeypatch, outcomes):
    route = RouteStub(outcomes)

    def factory(self, auth):
        route.auth = auth
        return route

    monkeypatch.setattr(SandboxClient, "_route_client", factory)
    return route


def env_commands_run(pid1=SERVE_PID1, fetch_body=None, probe_visible=False):
    """commands.run handler for envd-mocked flows."""

    def handler(cmd, kw, owner):
        if "/proc/1/cmdline" in cmd:
            return FakeResult(stdout=pid1)
        if "OPENCODE_SERVER_PASSWORD ?" in cmd:
            return FakeResult(stdout="visible\n" if probe_visible else "invisible\n")
        if "fetch(" in cmd:
            if fetch_body is None:
                return FakeResult(stdout="200 []\n", stderr="", exit_code=0)
            return fetch_body(cmd, kw, owner)
        # relaunch branch
        return FakeResult(stdout="")

    return handler


# ---------------------------------------------------------------- state


class TestState:
    def test_running(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")
        out = client.state("sid-1")
        assert out["state"] == "running"
        assert out["raw"]["sandbox_id"] == "sid-1"
        assert out["raw"]["state"] == "running"

    def test_paused(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("paused")
        assert client.state("sid-1")["state"] == "paused"

    def test_absent_is_data_not_error(self, client):
        def raise404(sid):
            raise SandboxNotFoundException("gone")

        FakeSandbox.script["get_info"] = raise404
        assert client.state("sid-1") == {"state": "absent", "raw": None}

    def test_transport_dead_maps_to_backend(self, client):
        def raise_conn(sid):
            raise httpx.ConnectError("connection refused")

        FakeSandbox.script["get_info"] = raise_conn
        with pytest.raises(CtlError) as ei:
            client.state("sid-1")
        assert ei.value.exit_code == Exit.BACKEND_UNAVAILABLE

    def test_state_probe_never_connects(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")
        client.state("sid-1")
        assert FakeSandbox.script["connect_calls"] == []


# ---------------------------------------------------------------- start


class TestStart:
    def test_happy_one_step(self, client, monkeypatch):
        route = stub_route(monkeypatch, [make_response(200)])
        FakeSandbox.script["commands_run"] = env_commands_run()
        out = client.start("sid-1", ttl_s=1800)
        assert out["started"] is True
        assert out["relaunch_fired"] is False
        assert out["route_attempts"] == 1
        assert route.auth == ("opencode", "sekret")
        inst = FakeSandbox.last
        assert inst.set_timeout_calls == [1800]

    def test_absent_maps_to_4(self, client):
        def raise404(sid):
            raise SandboxNotFoundException("nope")

        FakeSandbox.script["connect"] = raise404
        with pytest.raises(CtlError) as ei:
            client.start("sid-1")
        assert ei.value.exit_code == Exit.SANDBOX_ABSENT

    def test_route_timeout_maps_to_5(self, client, monkeypatch):
        stub_route(monkeypatch, [make_response(404)])
        FakeSandbox.script["commands_run"] = env_commands_run()
        with pytest.raises(CtlError) as ei:
            client.start("sid-1", ttl_s=0, budget_s=0.2)
        assert ei.value.exit_code == Exit.OPERATION_FAILED
        assert "not ready" in str(ei.value)

    def test_relaunch_guard_fires_on_foreign_pid1(self, client, monkeypatch):
        stub_route(monkeypatch, [make_response(200)])
        FakeSandbox.script["commands_run"] = env_commands_run(pid1="/sbin/init\x00")
        out = client.start("sid-1", budget_s=5)
        assert out["relaunch_fired"] is True
        launch = [kw for cmd, kw in FakeSandbox.last.envd_calls if "opencode serve" in cmd]
        assert launch and launch[0].get("background") is True

    def test_envd_probe_blind_skips_guard_without_failing(self, client, monkeypatch):
        stub_route(monkeypatch, [make_response(200)])

        def handler(cmd, kw, owner):
            if "/proc/1/cmdline" in cmd:
                raise httpx.ConnectError("envd down")
            return FakeResult(stdout="")

        FakeSandbox.script["commands_run"] = handler
        monkeypatch.setattr(C, "ENVD_PROBE_BUDGET_S", 0.2)
        out = client.start("sid-1", budget_s=5)
        assert out["started"] is True and out["relaunch_fired"] is False

    def test_route_401_is_auth_not_retry(self, client, monkeypatch):
        stub_route(monkeypatch, [make_response(401)])
        FakeSandbox.script["commands_run"] = env_commands_run()
        with pytest.raises(CtlError) as ei:
            client.start("sid-1", budget_s=30)
        assert ei.value.exit_code == Exit.AUTH_FAILURE


# ---------------------------------------------------------------- stop


class TestStop:
    def test_pause_uses_filesystem_checkpoint(self, client):
        out = client.stop("sid-1")
        assert out["paused"] is True and out["already_paused"] is False
        assert FakeSandbox.script["pause_calls"] == [{"keep_memory": False}]

    def test_already_paused_is_idempotent_success(self, client):
        FakeSandbox.script["beta_pause"] = lambda sid: False
        out = client.stop("sid-1")
        assert out["paused"] is True
        assert out["already_paused"] is True

    def test_absent_maps_to_4(self, client):
        def raise404(sid, **kw):
            raise SandboxNotFoundException("gone")

        FakeSandbox.script["beta_pause"] = raise404
        with pytest.raises(CtlError) as ei:
            client.stop("sid-1")
        assert ei.value.exit_code == Exit.SANDBOX_ABSENT

    def test_sdk_timeout_maps_to_5(self, client):
        def raise_to(sid, **kw):
            raise TimeoutException("pause stalled")

        FakeSandbox.script["beta_pause"] = raise_to
        with pytest.raises(CtlError) as ei:
            client.stop("sid-1")
        assert ei.value.exit_code == Exit.OPERATION_FAILED


# ---------------------------------------------------------------- set-timeout


class TestSetTimeout:
    def test_renews_via_control_plane_without_connect(self, client):
        out = client.set_timeout("sid-1", 1800)
        assert out == {"timeout_set": True, "seconds": 1800}
        assert FakeSandbox.script["timeout_calls"] == [("sid-1", 1800)]
        assert FakeSandbox.script["connect_calls"] == []

    def test_absent_maps_to_4(self, client):
        def raise404(sid, timeout):
            raise SandboxNotFoundException("gone")

        FakeSandbox.script["set_timeout"] = raise404
        with pytest.raises(CtlError) as ei:
            client.set_timeout("sid-1", 60)
        assert ei.value.exit_code == Exit.SANDBOX_ABSENT

    def test_transport_death_maps_to_3(self, client):
        def dead(sid, timeout):
            raise httpx.ConnectError("gateway down")

        FakeSandbox.script["set_timeout"] = dead
        with pytest.raises(CtlError) as ei:
            client.set_timeout("sid-1", 60)
        assert ei.value.exit_code == Exit.BACKEND_UNAVAILABLE

    def test_sdk_timeout_maps_to_5(self, client):
        def raise_to(sid, timeout):
            raise TimeoutException("api stalled")

        FakeSandbox.script["set_timeout"] = raise_to
        with pytest.raises(CtlError) as ei:
            client.set_timeout("sid-1", 60)
        assert ei.value.exit_code == Exit.OPERATION_FAILED


# ---------------------------------------------------------------- engine-get


class TestEngineGet:
    def test_path_b_envs_param_when_env_invisible(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")

        def fetch(cmd, kw, owner):
            return FakeResult(stdout='200 [{"id":"ses_x"}]\n')

        FakeSandbox.script["commands_run"] = env_commands_run(fetch_body=fetch)
        out = client.engine_get("sid-1", "/session")
        assert out["status"] == 200
        assert out["body"] == '[{"id":"ses_x"}]'
        assert out["password_path"] == "envs-param"
        fetch_call = [kw for cmd, kw in FakeSandbox.last.envd_calls if "fetch(" in cmd]
        assert fetch_call[0]["envs"] == {"OPENCODE_SERVER_PASSWORD": "sekret"}

    def test_path_a_sandbox_env_when_visible(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")
        FakeSandbox.script["commands_run"] = env_commands_run(probe_visible=True)
        out = client.engine_get("sid-1", "/session")
        assert out["status"] == 200
        assert out["password_path"] == "sandbox-env"
        fetch_call = [kw for cmd, kw in FakeSandbox.last.envd_calls if "fetch(" in cmd]
        assert fetch_call[0]["envs"] is None

    def test_local_password_never_reaches_output(self, client):
        import json

        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")
        FakeSandbox.script["commands_run"] = env_commands_run()
        out = client.engine_get("sid-1", "/session")
        assert "sekret" not in json.dumps(out)

    def test_non_200_passes_status_through(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")

        def fetch(cmd, kw, owner):
            return FakeResult(stdout='404 {"name":"NotFoundError"}\n')

        FakeSandbox.script["commands_run"] = env_commands_run(fetch_body=fetch)
        out = client.engine_get("sid-1", "/session/zzz")
        assert out["status"] == 404

    def test_paused_returns_null_not_resume(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("paused")
        out = client.engine_get("sid-1", "/session")
        assert out == {"status": None, "body": None, "unavailable": "paused"}
        assert FakeSandbox.script["connect_calls"] == []

    def test_absent_maps_to_4(self, client):
        FakeSandbox.script["get_info"] = lambda sid: (_ for _ in ()).throw(
            SandboxNotFoundException("gone")
        )
        # state() swallows not-found into "absent"; engine_get must then raise 4
        with pytest.raises(CtlError) as ei:
            client.engine_get("sid-1", "/session")
        assert ei.value.exit_code == Exit.SANDBOX_ABSENT

    def test_envd_command_failure_maps_to_5(self, client):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")

        def fetch(cmd, kw, owner):
            return FakeResult(stdout="", stderr="node: bad option", exit_code=9)

        FakeSandbox.script["commands_run"] = env_commands_run(fetch_body=fetch)
        with pytest.raises(CtlError) as ei:
            client.engine_get("sid-1", "/session")
        assert ei.value.exit_code == Exit.OPERATION_FAILED

    def test_envs_invisible_file_missing_maps_to_auth(self, client, tmp_path):
        FakeSandbox.script["get_info"] = lambda sid: FakeInfo("running")
        FakeSandbox.script["commands_run"] = env_commands_run()
        client._password_file = tmp_path / "nope"
        with pytest.raises(CtlError) as ei:
            client.engine_get("sid-1", "/session")
        assert ei.value.exit_code == Exit.AUTH_FAILURE


# ---------------------------------------------------------------- create / kill / route-wait


class TestCreateKillRoute:
    def test_create_defaults(self, client):
        out = client.create("opencode-poc")
        call = FakeSandbox.script["create_calls"][0]
        assert call["template"] == "opencode-poc"
        assert call["timeout"] == 1800
        assert call["request_timeout"] == 320
        assert out["sandbox_id"] == "new-sandbox"

    def test_create_explicit_timeouts_win(self, client):
        client.create("opencode-poc", timeout_s=60, request_timeout_s=90.0)
        call = FakeSandbox.script["create_calls"][-1]
        assert (call["timeout"], call["request_timeout"]) == (60, 90.0)

    def test_kill_ok(self, client):
        assert client.kill("sid-1") == {"killed": True}
        assert FakeSandbox.script["kill_calls"] == ["sid-1"]

    def test_kill_absent_maps_to_4(self, client):
        def raise404(sid, **kw):
            raise SandboxNotFoundException("gone")

        FakeSandbox.script["kill"] = raise404
        with pytest.raises(CtlError) as ei:
            client.kill("sid-1")
        assert ei.value.exit_code == Exit.SANDBOX_ABSENT

    def test_route_wait_happy_and_transport_noise(self, client, monkeypatch):
        route = stub_route(monkeypatch, [httpx.ConnectError("no route"), make_response(200)])
        ready_s, attempts = client.poll_route("sid-1", budget_s=10)
        assert attempts == 2 and route.calls == 2
        assert ready_s >= 0

    def test_route_wait_exhausts_to_5(self, client, monkeypatch):
        stub_route(monkeypatch, [make_response(503)])
        with pytest.raises(CtlError) as ei:
            client.poll_route("sid-1", budget_s=0.2)
        assert ei.value.exit_code == Exit.OPERATION_FAILED


# ---------------------------------------------------------------- exit-code translation table


class TestTranslationTable:
    @pytest.mark.parametrize(
        "exc,expected",
        [
            (AuthenticationException("bad key"), Exit.AUTH_FAILURE),
            (SandboxNotFoundException("gone"), Exit.SANDBOX_ABSENT),
            (httpx.ConnectError("refused"), Exit.BACKEND_UNAVAILABLE),
            (ConnectionResetError("reset"), Exit.BACKEND_UNAVAILABLE),
            (TimeoutException("slow"), Exit.OPERATION_FAILED),
            (RuntimeError("???"), Exit.OPERATION_FAILED),
        ],
    )
    def test_map(self, exc, expected):
        assert C._translate(exc, "ctx").exit_code == expected
