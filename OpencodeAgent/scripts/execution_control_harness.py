"""Docker and real-HTTP evidence harness for execution-control verification."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from execution_control_common import (
    PASSWORD,
    TOKEN,
    CheckFailure,
    JsonValue,
    Reply,
    canonical,
    command,
    mapping,
    require,
    sha,
)


class Harness:

    def __init__(self, image: str, evidence: Path, state: Path) -> None:
        self.image, self.evidence, self.state = image, evidence, state
        self.name = f"vt-execution-control-{os.getpid()}"
        self.cid = ""
        self.sequence = 0
        self.lock = threading.Lock()
        self.passes: list[str] = []
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "requests.jsonl").write_text("", encoding="utf-8")

    def save(self, name: str, value: JsonValue) -> None:
        (self.evidence / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def mark(self, group: str) -> None:
        require(group not in self.passes, f"duplicate group {group}")
        self.passes.append(group)
        print(f"[PASS] {group}", flush=True)

    def docker(self, *args: str, data: bytes | None = None) -> str:
        result = command(["docker", *args], data=data)
        if result.returncode:
            raise CheckFailure(result.stderr.decode(errors="replace").strip())
        return result.stdout.decode(errors="replace")

    def container_python(self, source: str, *args: str) -> str:
        return self.docker(
            "exec",
            "-i",
            self.cid,
            "/opt/venv/bin/python",
            "-",
            *args,
            data=source.encode(),
        )

    def request(
        self,
        group: str,
        label: str,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None = None,
        *,
        auth: str = "good",
    ) -> Reply:
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
        headers = ["Content-Type: application/json"]
        if auth == "good":
            headers.append(f"Authorization: Bearer {TOKEN}")
        elif auth == "wrong":
            headers.append("Authorization: Bearer definitely-wrong")
        elif auth == "basic":
            headers.append(f"Authorization: Basic {TOKEN}")
        args = [
            "exec",
            "-i",
            self.cid,
            "curl",
            "-sS",
            "--max-time",
            "180",
            "-X",
            method,
        ]
        for header in headers:
            args.extend(("-H", header))
        payload = b"" if body is None else canonical(body)
        if body is not None:
            args.extend(("--data-binary", "@-"))
        args.extend((f"http://127.0.0.1:8000{path}", "-w", "\n__STATUS__:%{http_code}"))
        started = time.perf_counter()
        result = command(["docker", *args], data=payload, timeout=200)
        elapsed = time.perf_counter() - started
        require(
            result.returncode == 0,
            f"curl {label}: {result.stderr.decode(errors='replace')}",
        )
        raw, marker = result.stdout.decode(errors="replace").rsplit("\n__STATUS__:", 1)
        status = int(marker.strip())
        parsed: JsonValue = json.loads(raw) if raw else None
        record = {
            "sequence": sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            "group": group,
            "label": label,
            "method": method,
            "path": path,
            "request_body": body,
            "status": status,
            "response_body": parsed,
            "response_sha256": sha(raw.encode()),
            "elapsed_s": round(elapsed, 6),
        }
        with self.lock:
            with (self.evidence / "requests.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
            print(
                f"[{group}] {label}: {method} {path} -> {status} sha256={record['response_sha256']}",
                flush=True,
            )
        return Reply(sequence, status, parsed, raw)

    def expect(
        self, reply: Reply, status: int, body: JsonValue | None = None
    ) -> dict[str, JsonValue]:
        require(
            reply.status == status,
            f"HTTP {reply.status}, expected {status}: {reply.raw}",
        )
        if body is not None:
            require(reply.body == body, f"body {reply.body!r}, expected {body!r}")
        return mapping(reply.body)

    def file_state(self, run_id: str) -> dict[str, list[JsonValue]]:
        root = self.state / "runtime" / "runs" / run_id
        require(root.is_dir(), f"missing run directory {run_id}")
        return {
            path.relative_to(root).as_posix(): [
                sha(path.read_bytes()),
                path.stat().st_mtime_ns,
            ]
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def dispatch(self, key: str) -> dict[str, JsonValue] | None:
        path = self.state / "runtime" / "strategy_runs.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT remote_id,status,error_kind,worker_pid,worker_pgid,start_digest FROM execution_dispatches WHERE dispatch_key=?",
                (key,),
            ).fetchone()
        return None if row is None else dict(row)

    def wait_terminal(self, group: str, remote_id: str) -> dict[str, JsonValue]:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            body = self.expect(
                self.request(
                    group,
                    f"status-{remote_id}",
                    "GET",
                    f"/execution-instances/{remote_id}",
                ),
                200,
            )
            if body["status"] in {"completed", "failed", "cancelled"}:
                return body
            time.sleep(0.2)
        raise CheckFailure(f"terminal timeout for {remote_id}")

    def wait_ready(self) -> None:
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            logs = self.docker("logs", self.cid)
            if all(
                marker in logs
                for marker in (
                    "opencode server listening",
                    "warmup complete",
                    "VT MCP connected",
                    "strategy-run entry listening",
                )
            ):
                try:
                    health = self.request("startup", "health", "GET", "/health")
                except CheckFailure:
                    # A restart retains old log markers while the new socket is still closed.
                    health = None
                if health is not None and health.status == 200:
                    self.save("startup.json", {"health": health.body, "markers": True})
                    return
            require(
                self.docker(
                    "inspect", "--format", "{{.State.Running}}", self.cid
                ).strip()
                == "true",
                "container exited",
            )
            time.sleep(2)
        raise CheckFailure("startup markers/health timeout")

    def start_container(self) -> None:
        fixtures = Path(__file__).resolve().parents[2] / "agent/tests/fixtures"
        args = [
            "run",
            "-d",
            "--name",
            self.name,
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "-e",
            f"OPENCODE_SERVER_PASSWORD={PASSWORD}",
            "-e",
            "DASHSCOPE_API_KEY=dummy-not-a-real-key",
            "-e",
            "CLICKHOUSE_HOST=",
            "-e",
            "RM_VT_STRATEGY_RUN_TOKEN_FILE=/vt-state/auth/service-token",
            "-e",
            "VIBE_TRADING_HOME=/vt-state/runtime",
            "-e",
            "VIBE_TRADING_ALLOWED_RUN_ROOTS=/vt-state/runtime/runs",
            "-e",
            "VT_STRATEGY_RUN_RECOVERY_GRACE_S=3600",
            "-e",
            "VT_STARTUP_BUDGET_S=300",
            "-v",
            f"{self.state}:/vt-state",
            "-v",
            f"{fixtures / 'execution_control'}:/fixtures/execution_control:ro",
            "-v",
            f"{fixtures / 'factor_prototype'}:/fixtures/factor_prototype:ro",
            "-v",
            f"{self.state / 'loader.yaml'}:/home/opencode/.vibe-trading/data-bridge/config.yaml:ro",
            self.image,
        ]
        self.cid = self.docker(*args).strip()
        self.wait_ready()

    def stop_container(self) -> None:
        if self.cid:
            logs = self.docker("logs", "--timestamps", self.cid)
            (self.evidence / "container.log").write_text(logs, encoding="utf-8")
            self.docker("rm", "-f", self.cid)
