"""CLI surface tests: dispatch, strict JSON on stdout, exit-code contract."""

from __future__ import annotations

import json

import pytest

from sandbox_ctl import cli
from sandbox_ctl.output import CtlError, Exit


class FakeClient:
    """Stands in for SandboxClient at the cli boundary."""

    last_kwargs = None

    def __init__(self, **kw):
        FakeClient.last_kwargs = kw

    results: dict = {}

    def _r(self, name, default):
        out = FakeClient.results.get(name, default)
        if isinstance(out, Exception):
            raise out
        return out

    def state(self, sid):
        return self._r("state", {"state": "running", "raw": {"sandbox_id": sid}})

    def start(self, sid, ttl_s=1800, budget_s=180.0):
        return self._r(
            "start",
            {"started": True, "connect_s": 0.5, "route_ready_s": 0.2,
             "route_attempts": 1, "relaunch_fired": False},
        )

    def stop(self, sid):
        return self._r("stop", {"paused": True, "already_paused": False, "pause_api_s": 1.4})

    def set_timeout(self, sid, seconds):
        return self._r("set_timeout", {"timeout_set": True, "seconds": seconds})

    def engine_get(self, sid, path):
        return self._r("engine_get", {"status": 200, "body": "[]", "password_path": "sandbox-env"})

    def create(self, template, timeout_s, request_timeout_s):
        return self._r("create", {"sandbox_id": "new-sandbox", "claim_s": 0.2})

    def kill(self, sid):
        return self._r("kill", {"killed": True})

    def poll_route(self, sid, budget_s=180.0):
        out = FakeClient.results.get("route", (0.3, 2))
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeClient.results = {}
    monkeypatch.setattr(cli, "SandboxClient", FakeClient)
    return FakeClient


def run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, out


class TestHappyPathsJSONStrictness:
    @pytest.mark.parametrize(
        "argv,payload_key",
        [
            (["state", "sid-1"], "state"),
            (["start", "sid-1"], "started"),
            (["stop", "sid-1"], "paused"),
            (["set-timeout", "sid-1", "1800"], "timeout_set"),
            (["engine-get", "sid-1", "/session"], "status"),
            (["create", "--template", "opencode-poc"], "sandbox_id"),
            (["kill", "sid-1"], "killed"),
            (["route-wait", "sid-1"], "ready"),
        ],
    )
    def test_stdout_is_one_strict_json_doc(self, capsys, argv, payload_key):
        code, out = run(capsys, argv)
        assert code == 0
        doc = json.loads(out)  # raises if not strict JSON
        assert payload_key in doc
        assert out.count("\n") == 1  # exactly one document

    def test_create_forwards_flags(self, capsys):
        code, out = run(capsys, ["create", "--template", "t", "--timeout", "60",
                                 "--request-timeout", "90"])
        assert code == 0 and json.loads(out)["sandbox_id"] == "new-sandbox"

    def test_set_timeout_forwards_seconds(self, capsys):
        code, out = run(capsys, ["set-timeout", "sid-1", "90"])
        assert code == 0
        assert json.loads(out) == {"timeout_set": True, "seconds": 90}

    def test_route_wait_output(self, capsys):
        code, out = run(capsys, ["route-wait", "sid-1", "--budget", "5"])
        doc = json.loads(out)
        assert code == 0 and doc == {"ready": True, "route_ready_s": 0.3, "attempts": 2}


class TestExitCodes:
    @pytest.mark.parametrize(
        "cmd,result_key,expected_exit",
        [
            (["start", "sid-1"], "start", Exit.BACKEND_UNAVAILABLE),
            (["stop", "sid-1"], "stop", Exit.SANDBOX_ABSENT),
            (["set-timeout", "sid-1", "60"], "set_timeout", Exit.SANDBOX_ABSENT),
            (["engine-get", "sid-1", "/session"], "engine_get", Exit.AUTH_FAILURE),
            (["kill", "sid-1"], "kill", Exit.OPERATION_FAILED),
        ],
    )
    def test_ctl_error_envelope_and_code(self, capsys, cmd, result_key, expected_exit):
        FakeClient.results[result_key] = CtlError(expected_exit, "boom")
        code, out = run(capsys, cmd)
        assert code == int(expected_exit)
        doc = json.loads(out)
        assert doc["ok"] is False
        assert doc["error"]["code"] == expected_exit.name.lower()
        assert doc["error"]["message"] == "boom"

    def test_route_timeout_start_is_5(self, capsys):
        FakeClient.results["start"] = CtlError(Exit.OPERATION_FAILED, "route not ready")
        code, out = run(capsys, ["start", "sid-1"])
        assert code == 5
        assert json.loads(out)["error"]["code"] == "operation_failed"

    def test_state_absent_exits_zero(self, capsys):
        FakeClient.results["state"] = {"state": "absent", "raw": None}
        code, out = run(capsys, ["state", "sid-1"])
        assert code == 0
        assert json.loads(out) == {"state": "absent", "raw": None}

    def test_engine_get_paused_exits_zero(self, capsys):
        FakeClient.results["engine_get"] = {"status": None, "body": None, "unavailable": "paused"}
        code, out = run(capsys, ["engine-get", "sid-1", "/session"])
        assert code == 0
        assert json.loads(out)["unavailable"] == "paused"


class TestUsage:
    def test_missing_subcommand_is_2(self, capsys):
        assert cli.main([]) == 2

    def test_bad_path_rejected_as_usage(self, capsys):
        with pytest.raises(SystemExit) as ei:
            cli.main(["engine-get", "sid-1", "session"])  # no leading slash
        assert ei.value.code == 2

    @pytest.mark.parametrize("bad", ["/a b", "/a'b", '/a"b', "/a$b", "/a`b", "/a\\nb"])
    def test_path_injection_chars_rejected(self, bad):
        with pytest.raises(SystemExit) as ei:
            cli.main(["engine-get", "sid-1", bad])
        assert ei.value.code == 2

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", ""])
    def test_set_timeout_seconds_must_be_positive_int(self, bad):
        with pytest.raises(SystemExit) as ei:
            cli.main(["set-timeout", "sid-1", bad])
        assert ei.value.code == 2

    def test_set_timeout_missing_seconds_is_2(self):
        with pytest.raises(SystemExit) as ei:
            cli.main(["set-timeout", "sid-1"])
        assert ei.value.code == 2

    def test_unknown_subcommand_is_2(self):
        with pytest.raises(SystemExit) as ei:
            cli.main(["destroy", "sid-1"])
        assert ei.value.code == 2


class TestConfigWiring:
    def test_client_is_env_only_no_construction_args(self, monkeypatch, capsys):
        seen = {}

        class Spy(FakeClient):
            def __init__(self, **kw):
                seen.update(kw)

        monkeypatch.setattr(cli, "SandboxClient", Spy)
        monkeypatch.setenv("E2B_API_KEY", "k-from-env")
        code, _ = run(capsys, ["state", "sid-1"])
        assert code == 0
        assert seen == {}

    def test_api_key_file_resolution(self, monkeypatch, tmp_path):
        from sandbox_ctl import bootstrap

        keyf = tmp_path / "admin-api-key"
        keyf.write_text("K123\n")
        monkeypatch.setenv("E2B_API_KEY_FILE", str(keyf))
        assert bootstrap.resolve_api_key() == "K123"

    def test_api_key_env_beats_file(self, monkeypatch, tmp_path):
        from sandbox_ctl import bootstrap

        keyf = tmp_path / "f"
        keyf.write_text("FILE")
        monkeypatch.setenv("E2B_API_KEY_FILE", str(keyf))
        monkeypatch.setenv("E2B_API_KEY", "ENV")
        assert bootstrap.resolve_api_key() == "ENV"

    def test_missing_key_file_raises_runtime(self, monkeypatch, tmp_path):
        from sandbox_ctl import bootstrap

        monkeypatch.setenv("E2B_API_KEY_FILE", str(tmp_path / "nope"))
        with pytest.raises(RuntimeError, match="unreadable"):
            bootstrap.resolve_api_key()

    def test_gateway_domain_default(self, monkeypatch):
        from sandbox_ctl import bootstrap

        monkeypatch.delenv("E2B_DOMAIN", raising=False)
        assert bootstrap.gateway_domain() == bootstrap.DEFAULT_DOMAIN

    def test_password_file_env_honored(self, monkeypatch, tmp_path):
        from sandbox_ctl.client import SandboxClient

        pw = tmp_path / "pw"
        pw.write_text("pwv\n")
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD_FILE", str(pw))
        c = SandboxClient(api_key="k")
        assert c._server_password() == "pwv"

    def test_password_env_var_beats_file(self, monkeypatch, tmp_path):
        from sandbox_ctl.client import SandboxClient

        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "fromenv")
        c = SandboxClient(api_key="k", password_file=str(tmp_path / "missing"))
        assert c._server_password() == "fromenv"
