"""Materialize one frozen backtest directory and its replay-safe provenance.

Local ``verified`` data digests use schema ``backtest-local-data-v1``: canonical
JSON ``{"schema": ..., "files": [[relative_path, sha256], ...]}`` with pairs
sorted by relative path. Paths are resolved like the local loader, then rendered
relative to its ``data-bridge`` directory. Only files required by every declared
symbol participate; missing/unreadable sources are explicitly ``unavailable``.

# noqa: SIZE_OK - one atomic filesystem contract and its typed local-data boundary.
"""

from __future__ import annotations

import errno
import hmac
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Final, Literal, TypeAlias

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backtest.loaders import local_loader
from src.strategy_library.paths import (
    file_sha256,
    strategy_dir,
    version_code_path,
    version_config_path,
)
from src.strategy_runs.backtest_config import (
    BacktestConfigValidationError,
    NeutralBacktestConfig,
    validate_duplicate_provenance,
)
from src.strategy_runs.control_contract import (
    CONTRACT_VERSION,
    ContractValidationError,
    canonical_json_bytes,
    backtest_input_digest,
    sha256_hex,
)
from src.strategy_runs.control_store import (
    ControlStore,
    ControlStoreError,
    DispatchConflict,
)
from src.tools.path_utils import safe_run_dir

__all__ = [
    "BacktestMaterializer",
    "BacktestPrepareError",
    "BacktestPrepareRequest",
    "BacktestPrepareResponse",
    "recheck_backtest_inputs",
]

_SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
_DISPATCH_KEY_PATTERN: Final = r"^[A-Za-z0-9._~:-]+$"
_RUN_ID_RE: Final = re.compile(r"mcp-long-[A-Za-z0-9._-]+-[1-9][0-9]*")
_UNAVAILABLE: Final = "unavailable"
_DATA_SCHEMA: Final = "backtest-local-data-v1"
ConfigInputValue: TypeAlias = str | int | float | bool | None | list[str]
PrepareErrorKind = Literal[
    "invalid_input",
    "strategy_not_found",
    "version_not_found",
    "input_digest_mismatch",
    "idempotency_conflict",
    "storage_unavailable",
]
ReplayState = Literal["missing", "complete", "tampered"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _FrozenSource(_FrozenModel):
    text: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class BacktestPrepareRequest(_FrozenModel):
    """The exact prepare wire body; no execution or output fields are accepted."""

    dispatch_key: str = Field(
        min_length=1, max_length=200, pattern=_DISPATCH_KEY_PATTERN
    )
    run_dir: str = Field(min_length=1)
    config: dict[str, ConfigInputValue]
    source: _FrozenSource
    data_source: str = Field(min_length=1)
    data_as_of: str = Field(min_length=1)
    snapshot_basis: str = Field(min_length=1)
    contract_version: Literal[CONTRACT_VERSION]


class BacktestPrepareResponse(_FrozenModel):
    """The exact stored and returned D-3/data provenance projection."""

    run_dir: str
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    input_digest: str = Field(pattern=_SHA256_PATTERN)
    data_source: str
    data_as_of: str
    data_digest: str | None = Field(pattern=_SHA256_PATTERN)
    data_digest_status: Literal["verified", "unavailable"]
    contract_version: Literal[CONTRACT_VERSION]


_PREPARE_DECLARATION_KEYS: Final = frozenset(BacktestPrepareResponse.model_fields)


class _LocalSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    type: str = "csv"
    path: str | None = None
    db_path: str | None = None
    query: str | None = None


class _LocalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    sources: tuple[_LocalSource, ...] = ()


class BacktestPrepareError(Exception):
    """A fixed failure kind safe for the HTTP boundary to project."""

    __slots__ = ("kind", "remote_id")

    def __init__(self, kind: PrepareErrorKind, remote_id: str | None = None) -> None:
        self.kind = kind
        self.remote_id = remote_id
        super().__init__(kind)


class BacktestMaterializer:
    """Claim declarations briefly, then atomically freeze only new run bytes."""

    def __init__(
        self, store: ControlStore, *, runtime_root: Path | None = None
    ) -> None:
        self._store = store
        self._runtime_root = runtime_root

    def prepare(self, request: BacktestPrepareRequest) -> BacktestPrepareResponse:
        """Validate, claim and materialize one declaration without starting work."""
        try:
            config = validate_duplicate_provenance(
                request.config,
                data_source=request.data_source,
                data_as_of=request.data_as_of,
                snapshot_basis=request.snapshot_basis,
            )
            config_bytes = canonical_json_bytes(request.config)
            source_bytes = request.source.text.encode("utf-8")
        except (
            ValidationError,
            BacktestConfigValidationError,
            ContractValidationError,
            UnicodeError,
        ) as exc:
            raise BacktestPrepareError("invalid_input") from exc

        run_dir = _prepare_run_dir(request.run_dir)
        snapshot_bytes = _snapshot_bytes(config, self._runtime_root)
        source_sha256 = sha256_hex(source_bytes)
        if not hmac.compare_digest(request.source.sha256, source_sha256):
            raise BacktestPrepareError("invalid_input")
        if not hmac.compare_digest(source_bytes, snapshot_bytes):
            raise BacktestPrepareError("input_digest_mismatch")

        config_sha256 = sha256_hex(config_bytes)
        input_digest = backtest_input_digest(
            config_sha256,
            source_sha256,
            request.data_source,
            request.data_as_of,
            request.snapshot_basis,
        )
        data_digest, data_status = _local_data_digest(config)
        response = BacktestPrepareResponse(
            run_dir=str(run_dir),
            config_sha256=config_sha256,
            source_sha256=source_sha256,
            input_digest=input_digest,
            data_source=request.data_source,
            data_as_of=request.data_as_of,
            data_digest=data_digest,
            data_digest_status=data_status,
            contract_version=CONTRACT_VERSION,
        )
        try:
            claim = self._store.claim_dispatch(
                request.dispatch_key,
                "backtest",
                response.model_dump(mode="json"),
                run_dir.name,
                CONTRACT_VERSION,
            )
        except DispatchConflict as exc:
            raise BacktestPrepareError(
                "idempotency_conflict", exc.original_remote_id
            ) from exc
        except ControlStoreError as exc:
            raise BacktestPrepareError("storage_unavailable") from exc

        expected = (
            _stored_response(claim.row.declaration_json) if claim.replayed else response
        )
        _publish_or_verify(run_dir, expected, config_bytes, source_bytes)
        return expected


def _prepare_run_dir(value: str) -> Path:
    """Fence an absolute run path and derive its server-owned remote identity."""
    candidate = Path(value)
    if not candidate.is_absolute():
        raise BacktestPrepareError("invalid_input")
    try:
        run_dir = safe_run_dir(value)
    except ValueError as exc:
        raise BacktestPrepareError("invalid_input") from exc
    if (
        candidate.name != run_dir.name
        or len(run_dir.name) > 128
        or _RUN_ID_RE.fullmatch(run_dir.name) is None
    ):
        raise BacktestPrepareError("invalid_input")
    return run_dir


def _snapshot_bytes(config: NeutralBacktestConfig, runtime_root: Path | None) -> bytes:
    """Resolve one version and read its code exactly once without modifying it."""
    try:
        directory = strategy_dir(config.strategy_id, runtime_root=runtime_root)
        config_path = version_config_path(
            config.strategy_id, config.strategy_version, runtime_root=runtime_root
        )
        code_path = version_code_path(
            config.strategy_id, config.strategy_version, runtime_root=runtime_root
        )
    except ValueError as exc:
        raise BacktestPrepareError("invalid_input") from exc
    if not directory.is_dir():
        raise BacktestPrepareError("strategy_not_found")
    if not config_path.is_file() or not code_path.is_file():
        raise BacktestPrepareError("version_not_found")
    try:
        return code_path.read_bytes()
    except FileNotFoundError as exc:
        raise BacktestPrepareError("version_not_found") from exc
    except OSError as exc:
        raise BacktestPrepareError("storage_unavailable") from exc


def _stored_response(declaration_json: str) -> BacktestPrepareResponse:
    try:
        return BacktestPrepareResponse.model_validate_json(declaration_json)
    except ValidationError as exc:
        raise BacktestPrepareError("storage_unavailable") from exc


def recheck_backtest_inputs(declaration_json: str) -> BacktestPrepareResponse | None:
    """Revalidate every available frozen-input proof without writing any bytes.

    The prepare wire response deliberately omits ``snapshot_basis``. Its frozen,
    hash-verified config remains the stored authority for that one D-3 field, so
    existing response bytes and declaration identities stay compatible.

    Args:
        declaration_json: Stored prepare declaration to revalidate.

    Returns:
        The parsed declaration, or ``None`` for a pre-execute abbreviated row.

    Raises:
        BacktestPrepareError: ``input_digest_mismatch`` on any available proof
            drift, or ``storage_unavailable`` when stored input cannot be read.
    """
    try:
        declaration = json.loads(declaration_json)
    except json.JSONDecodeError as exc:
        raise BacktestPrepareError("storage_unavailable") from exc
    if (
        not isinstance(declaration, dict)
        or not _PREPARE_DECLARATION_KEYS <= declaration.keys()
    ):
        return None
    stored = _stored_response(declaration_json)
    run_dir = _prepare_run_dir(stored.run_dir)
    try:
        config_bytes = (run_dir / "config.json").read_bytes()
        source_bytes = (run_dir / "code" / "signal_engine.py").read_bytes()
    except FileNotFoundError as exc:
        raise BacktestPrepareError("input_digest_mismatch") from exc
    except OSError as exc:
        raise BacktestPrepareError("storage_unavailable") from exc

    config_sha256 = sha256_hex(config_bytes)
    source_sha256 = sha256_hex(source_bytes)
    if not (
        hmac.compare_digest(config_sha256, stored.config_sha256)
        and hmac.compare_digest(source_sha256, stored.source_sha256)
    ):
        raise BacktestPrepareError("input_digest_mismatch")
    try:
        config = NeutralBacktestConfig.model_validate_json(config_bytes)
    except (ValidationError, UnicodeError) as exc:
        raise BacktestPrepareError("input_digest_mismatch") from exc
    if (
        config.data_source != stored.data_source
        or config.data_as_of.isoformat() != stored.data_as_of
    ):
        raise BacktestPrepareError("input_digest_mismatch")
    input_digest = backtest_input_digest(
        config_sha256,
        source_sha256,
        stored.data_source,
        stored.data_as_of,
        config.snapshot_basis,
    )
    if not hmac.compare_digest(input_digest, stored.input_digest):
        raise BacktestPrepareError("input_digest_mismatch")

    if stored.data_digest_status == "verified":
        data_digest, data_status = _local_data_digest(config)
        if (
            data_status != "verified"
            or stored.data_digest is None
            or data_digest is None
            or not hmac.compare_digest(data_digest, stored.data_digest)
        ):
            raise BacktestPrepareError("input_digest_mismatch")
    return stored


def _publish_or_verify(
    run_dir: Path,
    expected: BacktestPrepareResponse,
    config_bytes: bytes,
    source_bytes: bytes,
) -> None:
    """Replay a complete tree or atomically publish the one a prior attempt lost.

    A dispatch claim becomes visible before its run directory does, and a failed
    attempt removes its staging tree. An absent directory therefore means either
    an in-flight first attempt or a retry after a transient failure: publish the
    same frozen bytes instead of treating absence as tampering. If another
    attempt publishes first, its tree is verified and never rewritten. A rename
    that loses the same race reports ENOTEMPTY (or EEXIST) because the winning
    run directory is non-empty, which is the same published-by-another signal.
    """
    for _ in range(2):
        state = _frozen_state(run_dir, expected)
        if state == "complete":
            return
        if state == "tampered":
            raise BacktestPrepareError("input_digest_mismatch")
        try:
            _materialize(run_dir, config_bytes, source_bytes)
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise BacktestPrepareError("storage_unavailable") from exc
            continue
        return
    if _frozen_state(run_dir, expected) != "complete":
        raise BacktestPrepareError("input_digest_mismatch")


def _frozen_state(run_dir: Path, expected: BacktestPrepareResponse) -> ReplayState:
    """Classify frozen bytes without writes: absent, complete, or tampered."""
    if not run_dir.exists():
        return "missing"
    missing = False
    for relative, digest in (
        ("config.json", expected.config_sha256),
        ("code/signal_engine.py", expected.source_sha256),
    ):
        try:
            data = (run_dir / relative).read_bytes()
        except FileNotFoundError:
            missing = True
            continue
        except OSError as exc:
            raise BacktestPrepareError("storage_unavailable") from exc
        if not hmac.compare_digest(sha256_hex(data), digest):
            raise BacktestPrepareError("input_digest_mismatch")
    return "tampered" if missing else "complete"


def _materialize(run_dir: Path, config_bytes: bytes, source_bytes: bytes) -> None:
    """Publish a complete read-only staging tree, never a partial run directory."""
    parent = run_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{run_dir.name}.{uuid.uuid4().hex}.staging"
    renamed = False
    completed = False
    try:
        staging.mkdir()
        (staging / "code").mkdir()
        _write_readonly(staging / "config.json", config_bytes)
        _write_readonly(staging / "code" / "signal_engine.py", source_bytes)
        _fsync_dir(staging / "code")
        _fsync_dir(staging)
        if os.path.lexists(run_dir):
            raise FileExistsError(f"run directory already exists: {run_dir}")
        os.rename(staging, run_dir)
        renamed = True
        _fsync_dir(parent)
        completed = True
    finally:
        if not completed:
            shutil.rmtree(run_dir if renamed else staging, ignore_errors=True)


def _write_readonly(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("zero-length filesystem write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    path.chmod(0o444)


def _fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _local_data_digest(
    config: NeutralBacktestConfig,
) -> tuple[str | None, Literal["verified", "unavailable"]]:
    """Hash a completely enumerable local file set, never invoking a loader."""
    if config.data_source != "local":
        return None, _UNAVAILABLE
    try:
        local = _LocalConfig.model_validate(local_loader._load_config())
    except (ValidationError, OSError, UnicodeError, yaml.YAMLError):
        return None, _UNAVAILABLE
    sources = {source.symbol.strip(): source for source in local.sources}
    files: set[Path] = set()
    for requested in config.symbols:
        clean = (
            requested.split(":", 1)[1] if requested.startswith("local:") else requested
        )
        source = sources.get(clean)
        if source is None:
            return None, _UNAVAILABLE
        resolved = _local_source_files(source)
        if resolved is None:
            return None, _UNAVAILABLE
        files.update(resolved)

    pairs: list[list[str]] = []
    for path in files:
        try:
            if not path.is_file():
                return None, _UNAVAILABLE
            relative = Path(
                os.path.relpath(path, local_loader._CONFIG_PATH.parent.resolve())
            ).as_posix()
            pairs.append([relative, file_sha256(path)])
        except OSError:
            return None, _UNAVAILABLE
    pairs.sort(key=lambda pair: pair[0])
    document = {"schema": _DATA_SCHEMA, "files": pairs}
    return sha256_hex(canonical_json_bytes(document)), "verified"


def _local_source_files(source: _LocalSource) -> tuple[Path, ...] | None:
    """Resolve exactly the files the configured local reader would consume."""
    source_type = source.type.strip().lower()
    if source_type in {"csv", "parquet"}:
        if source.path is None or not source.path.strip():
            return None
        return (Path(source.path.strip()).expanduser().resolve(),)
    if source_type == "duckdb":
        if (
            source.db_path is None
            or not source.db_path.strip()
            or source.query is None
            or not source.query.strip()
        ):
            return None
        database = Path(source.db_path).expanduser().resolve()
        wal = Path(f"{database}.wal")
        return (database, wal) if wal.exists() else (database,)
    return None
