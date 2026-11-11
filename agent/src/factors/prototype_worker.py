"""CLI entrypoint for one supervisor-owned factor prototype run directory."""

from __future__ import annotations

import json
import signal
import sys
import threading
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.factors.prototype import (
    FactorPrototypeRunner,
    PrototypeRunRequest,
    PrototypeValidationError,
    write_prototype_json,
)
from src.factors.prototype_artifacts import quality_bounds
from src.factors.prototype_contract import factor_remote_id
from src.factors.registry_freeze import TOOL_SCHEMA_VERSION
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    FactorPrototypeSpec,
    JsonValue,
)
from src.tools.path_utils import safe_run_dir


class _SpecDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    remote_id: str = Field(min_length=1, max_length=128)
    dispatch_key: str = Field(min_length=1, max_length=200)
    registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_version: Literal[TOOL_SCHEMA_VERSION]
    contract_version: Literal[CONTRACT_VERSION]
    spec: FactorPrototypeSpec


def _run_dir(raw: str) -> Path:
    try:
        return safe_run_dir(raw)
    except ValueError as exc:
        raise PrototypeValidationError("invalid_request") from exc


def _write_failure(
    run_dir: Path,
    document: _SpecDocument,
    kind: Literal["input_digest_mismatch", "invalid_request", "runner_error"],
) -> None:
    result: dict[str, JsonValue] = {
        "remote_id": document.remote_id,
        "dispatch_key": document.dispatch_key,
        "registry_digest": document.registry_digest,
        "tool_schema_version": document.tool_schema_version,
        "contract_version": CONTRACT_VERSION,
        "status": "failed",
        "completed": 0,
        "total": len(document.spec.candidate_ids),
        "candidates": [],
        "quality_bounds": quality_bounds(document.spec, [], [], complete=False),
        "artifacts": {
            "ic_series": None,
            "ic_summary": None,
            "group_equity": None,
        },
        "artifact_paths": [],
        "error_kind": kind,
    }
    write_prototype_json(run_dir / "result.json", result)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``run_dir/spec.json`` and publish honest terminal evidence.

    Args:
        argv: Optional argument sequence containing exactly one fenced run directory.

    Returns:
        Zero for completed, partial, or cancelled outcomes; one for every failure.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        return 1
    document: _SpecDocument | None = None
    previous_handler = None
    handler_installed = False
    try:
        run_dir = _run_dir(arguments[0])
        spec_path = (run_dir / "spec.json").resolve()
        if spec_path.parent != run_dir:
            raise PrototypeValidationError("invalid_request")
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
        document = _SpecDocument.model_validate(raw)
        expected_remote_id = factor_remote_id(document.dispatch_key, document.spec)
        if (
            document.registry_digest != document.spec.registry_digest
            or document.remote_id != expected_remote_id
        ):
            raise PrototypeValidationError("input_digest_mismatch")
        stopped = threading.Event()
        previous_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        handler_installed = True
        result = FactorPrototypeRunner().run(
            PrototypeRunRequest(
                document.spec,
                run_dir,
                document.remote_id,
                document.dispatch_key,
            ),
            should_stop=stopped.is_set,
            on_progress=lambda progress: write_prototype_json(
                run_dir / "progress.json", progress.document()
            ),
        )
        write_prototype_json(run_dir / "result.json", result.document)
        return 0 if result.document["status"] != "failed" else 1
    except PrototypeValidationError as exc:
        if document is not None:
            _write_failure(run_dir, document, exc.kind)
        return 1
    except (OSError, UnicodeError):
        if document is not None:
            _write_failure(run_dir, document, "runner_error")
        return 1
    except (json.JSONDecodeError, ValidationError):
        if document is not None:
            _write_failure(run_dir, document, "invalid_request")
        return 1
    except Exception:  # noqa: BLE001 - worker failure-record boundary.
        if document is not None:
            _write_failure(run_dir, document, "runner_error")
        return 1
    finally:
        if handler_installed:
            signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
