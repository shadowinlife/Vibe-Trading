"""SQLite-backed strategy/factor artifact store.

Satisfies ``StrategyStoreProtocol``.  Uses WAL mode for concurrent reads,
FK constraints for referential integrity, and ``PRAGMA user_version``
for schema migrations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from src.strategy_store.models import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    BenchCategory,
    BenchResult,
    DecaySignal,
    DecaySnapshot,
    ModelTier,
    ValidationStatus,
    validate_artifact_readiness,
    validate_validation_status_transition,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path.home() / ".vibe-trading" / "strategy_store.db"

# Schema version introduced by the model-governance migration (adds the
# developer/owner/validator/approver/model_version/artifact_version/
# model_tier/intended_use/limitations/validation_status/validation_date
# columns to `artifacts`). Bumping PRAGMA user_version documents intent;
# the migration itself is idempotent via a column-existence check so it is
# safe to run unconditionally on every open regardless of the stamped
# version (covers DBs that were hand-edited or partially migrated).
# Schema version 3 adds ``derived_from`` on ``artifacts``.
_SCHEMA_VERSION_ADAPTATION = 3
_ADAPTATION_COLUMNS: dict[str, str] = {
    "derived_from": "TEXT",
}

# column_name -> DDL type/default fragment used by ALTER TABLE ADD COLUMN.
_GOVERNANCE_COLUMNS: dict[str, str] = {
    "developer": "TEXT",
    "owner": "TEXT",
    "validator": "TEXT",
    "approver": "TEXT",
    "model_version": "TEXT",
    "artifact_version": "TEXT",
    "model_tier": "TEXT",
    "intended_use": "TEXT",
    "limitations": "TEXT",
    "validation_status": "TEXT DEFAULT 'unvalidated'",
    "validation_date": "TEXT",
}

# Strategy-library columns: execution kind, declarative spec, scheduling
# readiness and the verdict pair. Defaults make rows written before this
# migration read back as non-demo code strategies in the draft state.
_STRATEGY_LIBRARY_COLUMNS: dict[str, str] = {
    "kind": "TEXT DEFAULT 'code'",
    "spec": "TEXT",
    "spec_sha256": "TEXT",
    "readiness": "TEXT DEFAULT 'draft'",
    "evaluator_verdict": "TEXT",
    "previous_verdict": "TEXT",
    "demo": "INTEGER DEFAULT 0",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_artifact_id() -> str:
    """Generate a unique artifact identifier."""
    return f"art_{uuid.uuid4().hex[:12]}"


def _json_dumps(value: object) -> str:
    """Serialize *value* to a compact JSON string."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, default: object) -> object:
    """Deserialize a JSON string, returning *default* when empty or corrupt."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class _Keep:
    """Sentinel type meaning "leave this gate column unchanged"."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "_KEEP"


#: Default for every gate-writer column: omitted columns stay untouched.
_KEEP = _Keep()


def _default_db_path() -> Path:
    """Return the configured strategy-store database path."""
    from src.config.accessor import get_env_config

    raw_path = get_env_config().paths.vibe_trading_strategy_store_db_path.strip()
    if raw_path:
        return Path(raw_path).expanduser()
    return _DEFAULT_DB_PATH


F = TypeVar("F", bound=Callable)


def _synchronized(method: F) -> F:
    """Serialize access to the shared SQLite connection."""

    @wraps(method)
    def wrapper(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# SqliteStrategyStore
# ---------------------------------------------------------------------------


class SqliteStrategyStore:
    """SQLite-backed strategy/factor artifact store.

    Satisfies ``StrategyStoreProtocol``.  Uses WAL mode for concurrent reads,
    FK constraints for referential integrity, and ``PRAGMA user_version``
    for schema migrations.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """Initialize the strategy store.

        Args:
            db_path: SQLite database path.  When omitted,
                ``VIBE_TRADING_STRATEGY_STORE_DB_PATH`` can override the
                default ``~/.vibe-trading/strategy_store.db``.
        """
        self.db_path = (
            Path(db_path) if db_path is not None else _default_db_path()
        )
        self._lock = threading.RLock()
        self._closed = False
        self._conn = self._open_connection()
        self._init_db()

    # -- Connection lifecycle -----------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        """Open the SQLite connection with this store's standard PRAGMAs."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def close(self) -> None:
        """Close the underlying connection.  Idempotent.

        A closed store keeps its ``db_path``; call ``reopen()`` to use it
        again.  Until then every query raises ``sqlite3.ProgrammingError``.
        """
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    def reopen(self) -> SqliteStrategyStore:
        """Reconnect a closed store and re-run the schema bootstrap.

        Returns:
            ``self``.  An already-open store is returned unchanged; the
            database path, schema and PRAGMAs are never altered.
        """
        with self._lock:
            if not self._closed:
                return self
            conn = self._open_connection()
            try:
                self._conn = conn
                self._init_db()
            except BaseException:
                conn.close()
                raise
            self._closed = False
            return self

    # -- Schema bootstrap ---------------------------------------------------

    def _init_db(self) -> None:
        """Create schema if needed, set PRAGMAs."""
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    id              TEXT PRIMARY KEY,
                    type            TEXT NOT NULL CHECK(type IN ('factor', 'strategy')),
                    name            TEXT NOT NULL,
                    source_paper    TEXT,
                    source_url      TEXT,
                    formula_latex   TEXT,
                    theme           TEXT,
                    columns_required TEXT,
                    decay_horizon   INTEGER DEFAULT 20,
                    signal_definition TEXT,
                    entry_rules     TEXT,
                    exit_rules      TEXT,
                    position_sizing TEXT,
                    universe        TEXT NOT NULL,
                    signal_engine_path TEXT,
                    run_dir         TEXT,
                    hypothesis_id   TEXT,
                    derived_from    TEXT,
                    status          TEXT NOT NULL DEFAULT 'created'
                                    CHECK(status IN (
                                        'created','benching','active',
                                        'monitoring','decayed','disabled')),
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    disabled_at     TEXT,
                    disabled_reason TEXT,
                    UNIQUE(name, universe)
                );

                CREATE TABLE IF NOT EXISTS bench_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id     TEXT NOT NULL
                                    REFERENCES artifacts(id) ON DELETE CASCADE,
                    bench_type      TEXT NOT NULL
                                    CHECK(bench_type IN ('initial','periodic','manual')),
                    ic_mean         REAL,
                    ic_std          REAL,
                    ir              REAL,
                    ic_positive_ratio REAL,
                    t_stat          REAL,
                    sharpe          REAL,
                    annual_return   REAL,
                    max_drawdown    REAL,
                    calmar          REAL,
                    category        TEXT
                                    CHECK(category IN (
                                        'alive','reversed','dead',
                                        'confirmed_alive','noise')),
                    train_start     TEXT,
                    train_end       TEXT,
                    test_start      TEXT,
                    test_end        TEXT,
                    run_dir         TEXT,
                    created_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS adaptation_bindings (
                    artifact_id TEXT PRIMARY KEY REFERENCES artifacts(id) ON DELETE CASCADE,
                    contract TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decay_snapshots (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id     TEXT NOT NULL
                                    REFERENCES artifacts(id) ON DELETE CASCADE,
                    rolling_ic_mean     REAL,
                    rolling_ir          REAL,
                    baseline_ic_mean    REAL,
                    ic_ratio            REAL,
                    decay_signal    TEXT
                                    CHECK(decay_signal IN (
                                        'healthy','warning','decayed','critical')),
                    consecutive_warnings INTEGER DEFAULT 0,
                    detail          TEXT,
                    created_at      TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_status
                    ON artifacts(status);
                CREATE INDEX IF NOT EXISTS idx_artifacts_type
                    ON artifacts(type);
                CREATE INDEX IF NOT EXISTS idx_bench_artifact
                    ON bench_history(artifact_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_decay_artifact
                    ON decay_snapshots(artifact_id, created_at);
                """
            )
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                self._conn.execute("PRAGMA user_version=1")
            self._migrate_artifact_columns()
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < _SCHEMA_VERSION_ADAPTATION:
                self._conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION_ADAPTATION}")
            self._conn.commit()

    def _migrate_artifact_columns(self) -> None:
        """Add optional ``artifacts`` columns if missing.

        Idempotent: checks ``PRAGMA table_info`` before each
        ``ALTER TABLE ADD COLUMN``.
        """
        existing_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(artifacts)")
        }
        extra = {
            **_GOVERNANCE_COLUMNS,
            **_ADAPTATION_COLUMNS,
            **_STRATEGY_LIBRARY_COLUMNS,
        }
        for column, ddl in extra.items():
            if column not in existing_columns:
                self._conn.execute(f"ALTER TABLE artifacts ADD COLUMN {column} {ddl}")

    # -- Write transaction --------------------------------------------------

    @contextmanager
    def _write_transaction(self):
        """Open an immediate write transaction for cross-connection safety."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    # -- Row converters -----------------------------------------------------

    @staticmethod
    def _row_to_artifact(row: sqlite3.Row) -> Artifact:
        """Convert a database row to an ``Artifact`` dataclass."""
        return Artifact(
            id=row["id"],
            type=ArtifactType(row["type"]),
            name=row["name"],
            universe=row["universe"],
            source_paper=row["source_paper"],
            source_url=row["source_url"],
            formula_latex=row["formula_latex"],
            theme=tuple(_json_loads(row["theme"], [])),
            columns_required=tuple(_json_loads(row["columns_required"], [])),
            decay_horizon=row["decay_horizon"],
            signal_definition=row["signal_definition"],
            entry_rules=row["entry_rules"],
            exit_rules=row["exit_rules"],
            position_sizing=row["position_sizing"],
            signal_engine_path=row["signal_engine_path"],
            run_dir=row["run_dir"],
            hypothesis_id=row["hypothesis_id"],
            derived_from=row["derived_from"],
            status=ArtifactStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            disabled_at=row["disabled_at"],
            disabled_reason=row["disabled_reason"],
            developer=row["developer"],
            owner=row["owner"],
            validator=row["validator"],
            approver=row["approver"],
            model_version=row["model_version"],
            artifact_version=row["artifact_version"],
            model_tier=ModelTier(row["model_tier"]) if row["model_tier"] else None,
            intended_use=row["intended_use"],
            limitations=row["limitations"],
            validation_status=(
                ValidationStatus(row["validation_status"])
                if row["validation_status"]
                else ValidationStatus.UNVALIDATED
            ),
            validation_date=row["validation_date"],
            kind=row["kind"] or "code",
            spec=_json_loads(row["spec"], None),
            spec_sha256=row["spec_sha256"],
            readiness=row["readiness"] or "draft",
            evaluator_verdict=_json_loads(row["evaluator_verdict"], None),
            previous_verdict=_json_loads(row["previous_verdict"], None),
            demo=bool(row["demo"]),
        )

    @staticmethod
    def _row_to_bench_result(row: sqlite3.Row) -> BenchResult:
        """Convert a database row to a ``BenchResult`` dataclass."""
        category = row["category"]
        return BenchResult(
            id=row["id"],
            artifact_id=row["artifact_id"],
            bench_type=row["bench_type"],
            ic_mean=row["ic_mean"],
            ic_std=row["ic_std"],
            ir=row["ir"],
            ic_positive_ratio=row["ic_positive_ratio"],
            t_stat=row["t_stat"],
            sharpe=row["sharpe"],
            annual_return=row["annual_return"],
            max_drawdown=row["max_drawdown"],
            calmar=row["calmar"],
            category=BenchCategory(category) if category else None,
            train_start=row["train_start"],
            train_end=row["train_end"],
            test_start=row["test_start"],
            test_end=row["test_end"],
            run_dir=row["run_dir"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_decay_snapshot(row: sqlite3.Row) -> DecaySnapshot:
        """Convert a database row to a ``DecaySnapshot`` dataclass."""
        signal = row["decay_signal"]
        return DecaySnapshot(
            id=row["id"],
            artifact_id=row["artifact_id"],
            rolling_ic_mean=row["rolling_ic_mean"],
            rolling_ir=row["rolling_ir"],
            baseline_ic_mean=row["baseline_ic_mean"],
            ic_ratio=row["ic_ratio"],
            decay_signal=DecaySignal(signal) if signal else None,
            consecutive_warnings=row["consecutive_warnings"],
            detail=row["detail"],
            created_at=row["created_at"],
        )

    # -- Artifact CRUD ------------------------------------------------------

    @_synchronized
    def register_artifact(self, artifact: Artifact, *, adaptation_binding: dict | None = None) -> str:
        """Register a new artifact.  Returns the artifact_id."""
        # A brand-new record has no prior validation history, so its implicit
        # "current" state is UNVALIDATED — registering directly with
        # validation_status=APPROVED would skip the VALIDATED step.
        validate_validation_status_transition(
            ValidationStatus.UNVALIDATED, artifact.validation_status
        )

        now = _now_iso()
        artifact_id = artifact.id or _new_artifact_id()
        created_at = artifact.created_at or now

        try:
            with self._write_transaction():
                self._conn.execute(
                    """
                    INSERT INTO artifacts (
                        id, type, name, source_paper, source_url, formula_latex,
                        theme, columns_required, decay_horizon, signal_definition,
                        entry_rules, exit_rules, position_sizing, universe,
                        signal_engine_path, run_dir, hypothesis_id, derived_from, status,
                        created_at, updated_at, disabled_at, disabled_reason,
                        developer, owner, validator, approver, model_version,
                        artifact_version, model_tier, intended_use, limitations,
                        validation_status, validation_date,
                        kind, spec, spec_sha256, readiness,
                        evaluator_verdict, previous_verdict, demo
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        artifact.type.value,
                        artifact.name,
                        artifact.source_paper,
                        artifact.source_url,
                        artifact.formula_latex,
                        _json_dumps(list(artifact.theme)),
                        _json_dumps(list(artifact.columns_required)),
                        artifact.decay_horizon,
                        artifact.signal_definition,
                        artifact.entry_rules,
                        artifact.exit_rules,
                        artifact.position_sizing,
                        artifact.universe,
                        artifact.signal_engine_path,
                        artifact.run_dir,
                        artifact.hypothesis_id,
                        artifact.derived_from,
                        artifact.status.value,
                        created_at,
                        now,
                        artifact.disabled_at,
                        artifact.disabled_reason,
                        artifact.developer,
                        artifact.owner,
                        artifact.validator,
                        artifact.approver,
                        artifact.model_version,
                        artifact.artifact_version,
                        artifact.model_tier.value if artifact.model_tier else None,
                        artifact.intended_use,
                        artifact.limitations,
                        artifact.validation_status.value,
                        artifact.validation_date,
                        artifact.kind,
                        _json_dumps(artifact.spec)
                        if artifact.spec is not None
                        else None,
                        artifact.spec_sha256,
                        artifact.readiness,
                        _json_dumps(artifact.evaluator_verdict)
                        if artifact.evaluator_verdict is not None
                        else None,
                        _json_dumps(artifact.previous_verdict)
                        if artifact.previous_verdict is not None
                        else None,
                        int(artifact.demo),
                    ),
                )
                if adaptation_binding is not None:
                    self._conn.execute(
                        "INSERT INTO adaptation_bindings VALUES (?, ?)",
                        (artifact_id, _json_dumps(adaptation_binding)),
                    )
        except sqlite3.IntegrityError as exc:
            if "artifacts.name" in str(exc):
                raise ValueError(
                    f"artifact '{artifact.name}' already exists in "
                    f"universe '{artifact.universe}'"
                ) from exc
            raise
        return artifact_id

    @_synchronized
    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """Get a single artifact by ID.  Returns ``None`` if not found."""
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        return self._row_to_artifact(row) if row else None

    @_synchronized
    def get_adaptation_binding(self, artifact_id: str) -> dict | None:
        """Return the immutable input contract of a prepared adaptation."""
        row = self._conn.execute(
            "SELECT contract FROM adaptation_bindings WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return json.loads(row["contract"]) if row else None

    @_synchronized
    def list_artifacts(
        self,
        *,
        type: ArtifactType | None = None,
        status: ArtifactStatus | None = None,
        universe: str | None = None,
        kind: str | None = None,
        readiness: str | None = None,
        limit: int = 100,
    ) -> Sequence[Artifact]:
        """List artifacts with optional filters, newest first."""
        clauses: list[str] = []
        params: list[object] = []

        if type is not None:
            clauses.append("type = ?")
            params.append(type.value)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if universe is not None:
            clauses.append("universe = ?")
            params.append(universe)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if readiness is not None:
            clauses.append("readiness = ?")
            params.append(readiness)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        rows = self._conn.execute(
            f"SELECT * FROM artifacts {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    @_synchronized
    def update_status(
        self,
        artifact_id: str,
        status: ArtifactStatus,
        *,
        reason: str | None = None,
    ) -> Artifact | None:
        """Transition an artifact to a new status.

        Sets ``disabled_at`` when transitioning to ``DISABLED``.
        Clears it when transitioning away.
        Returns the updated artifact, or ``None`` if not found.
        """
        existing = self.get_artifact(artifact_id)
        if existing is None:
            return None

        now = _now_iso()
        disabled_at: str | None = None
        disabled_reason: str | None = None

        if status is ArtifactStatus.DISABLED:
            disabled_at = now
            disabled_reason = reason if reason is not None else existing.disabled_reason
        elif existing.status is ArtifactStatus.DISABLED:
            # Clear disabled fields when moving out of DISABLED.
            disabled_at = None
            disabled_reason = None
        else:
            # Preserve existing disabled fields.
            disabled_at = existing.disabled_at
            disabled_reason = existing.disabled_reason

        with self._write_transaction():
            self._conn.execute(
                """
                UPDATE artifacts
                SET status = ?, updated_at = ?, disabled_at = ?, disabled_reason = ?
                WHERE id = ?
                """,
                (status.value, now, disabled_at, disabled_reason, artifact_id),
            )
        return self.get_artifact(artifact_id)

    @_synchronized
    def update_artifact(self, artifact: Artifact) -> Artifact | None:
        """Replace an artifact record.  Returns updated artifact or ``None``.

        Gate columns (``readiness``, ``evaluator_verdict``,
        ``previous_verdict``) are intentionally not written: they belong to
        ``_update_gate_columns`` so a plain row update cannot silently clear
        or forge a verdict. ``validation_status`` stays writable here for SDM
        model governance.
        """
        existing = self.get_artifact(artifact.id)
        if existing is None:
            return None

        # Structural enforcement of the validation state machine: an
        # unvalidated model can never be updated straight into APPROVED.
        validate_validation_status_transition(
            existing.validation_status, artifact.validation_status
        )

        now = _now_iso()
        with self._write_transaction():
            self._conn.execute(
                """
                UPDATE artifacts
                SET type = ?, name = ?, source_paper = ?, source_url = ?,
                    formula_latex = ?, theme = ?, columns_required = ?,
                    decay_horizon = ?, signal_definition = ?,
                    entry_rules = ?, exit_rules = ?, position_sizing = ?,
                    universe = ?, signal_engine_path = ?, run_dir = ?,
                    hypothesis_id = ?, derived_from = ?, status = ?, updated_at = ?,
                    disabled_at = ?, disabled_reason = ?,
                    developer = ?, owner = ?, validator = ?, approver = ?,
                    model_version = ?, artifact_version = ?, model_tier = ?,
                    intended_use = ?, limitations = ?, validation_status = ?,
                    validation_date = ?, kind = ?, spec = ?, spec_sha256 = ?,
                    demo = ?
                WHERE id = ?
                """,
                (
                    artifact.type.value,
                    artifact.name,
                    artifact.source_paper,
                    artifact.source_url,
                    artifact.formula_latex,
                    _json_dumps(list(artifact.theme)),
                    _json_dumps(list(artifact.columns_required)),
                    artifact.decay_horizon,
                    artifact.signal_definition,
                    artifact.entry_rules,
                    artifact.exit_rules,
                    artifact.position_sizing,
                    artifact.universe,
                    artifact.signal_engine_path,
                    artifact.run_dir,
                    artifact.hypothesis_id,
                    artifact.derived_from,
                    artifact.status.value,
                    now,
                    artifact.disabled_at,
                    artifact.disabled_reason,
                    artifact.developer,
                    artifact.owner,
                    artifact.validator,
                    artifact.approver,
                    artifact.model_version,
                    artifact.artifact_version,
                    artifact.model_tier.value if artifact.model_tier else None,
                    artifact.intended_use,
                    artifact.limitations,
                    artifact.validation_status.value,
                    artifact.validation_date,
                    artifact.kind,
                    _json_dumps(artifact.spec) if artifact.spec is not None else None,
                    artifact.spec_sha256,
                    int(artifact.demo),
                    artifact.id,
                ),
            )
        return self.get_artifact(artifact.id)

    # -- Strategy library gate state ----------------------------------------

    @_synchronized
    def _update_gate_columns(
        self,
        strategy_id: str,
        *,
        readiness: str | _Keep = _KEEP,
        evaluator_verdict: dict | None | _Keep = _KEEP,
        previous_verdict: dict | None | _Keep = _KEEP,
        validation_status: ValidationStatus | str | _Keep = _KEEP,
        expected_version: str | None = None,
        expected_spec_sha256: str | None = None,
    ) -> int:
        """Write only the gate columns the caller explicitly passes.

        Two independent state machines share the row. ``validation_status`` is
        SDM model-risk governance; ``readiness`` is the scheduling gate that
        decides whether a strategy may be attached to a schedule. A passing
        gate synchronises ``VALIDATED`` (never ``APPROVED`` — approval stays
        with SDM) and a failing gate sets ``REJECTED``; scheduling reads only
        ``readiness``.

        Every column argument defaults to ``_KEEP``, so a caller that flips
        one column leaves the others untouched instead of nulling them. The
        whole write is one conditional UPDATE, so a verdict and the readiness
        it justifies cannot land in separate statements.

        Args:
            strategy_id: Artifact id to update.
            readiness: New scheduling readiness, or ``_KEEP``.
            evaluator_verdict: Verdict dict stored as JSON, ``None`` to clear
                it, or ``_KEEP``.
            previous_verdict: Displaced verdict dict, ``None`` to clear, or
                ``_KEEP``.
            validation_status: SDM status to synchronise, or ``_KEEP``.
            expected_version: When given, require ``artifact_version`` to
                equal this value.
            expected_spec_sha256: When given, require ``spec_sha256`` to equal
                this value.

        Returns:
            The UPDATE rowcount: 1 when the row matched and was written, 0
            when the id does not exist or a CAS guard failed. A stale 0 is
            the facade's ``StaleVerdictError``; this layer only counts rows.

        Raises:
            ValueError: if *readiness* is outside the closed set, or if a
                gate write attempts to set ``APPROVED``.
        """
        assignments: list[str] = []
        params: list[object] = []

        if readiness is not _KEEP:
            validate_artifact_readiness(readiness)
            assignments.append("readiness = ?")
            params.append(readiness)
        if evaluator_verdict is not _KEEP:
            assignments.append("evaluator_verdict = ?")
            params.append(
                _json_dumps(evaluator_verdict) if evaluator_verdict is not None else None
            )
        if previous_verdict is not _KEEP:
            assignments.append("previous_verdict = ?")
            params.append(
                _json_dumps(previous_verdict) if previous_verdict is not None else None
            )
        if validation_status is not _KEEP:
            status = ValidationStatus(validation_status)
            if status is ValidationStatus.APPROVED:
                raise ValueError(
                    "gate writes may set VALIDATED or REJECTED but never APPROVED; "
                    "approval belongs to the SDM governance path"
                )
            assignments.append("validation_status = ?")
            params.append(status.value)

        assignments.append("updated_at = ?")
        params.append(_now_iso())

        where = ["id = ?"]
        params.append(strategy_id)
        # A `= NULL` predicate never matches in SQLite, so an absent expected
        # value omits its guard rather than making the UPDATE impossible.
        if expected_version is not None:
            where.append("artifact_version = ?")
            params.append(expected_version)
        if expected_spec_sha256 is not None:
            where.append("spec_sha256 = ?")
            params.append(expected_spec_sha256)

        with self._write_transaction():
            cursor = self._conn.execute(
                f"UPDATE artifacts SET {', '.join(assignments)} "
                f"WHERE {' AND '.join(where)}",
                params,
            )
            return cursor.rowcount

    @_synchronized
    def _update_spec_and_version(
        self,
        strategy_id: str,
        *,
        spec_json: str,
        artifact_version: str,
        spec_sha256: str,
        readiness: str,
        evaluator_verdict: dict | None,
        previous_verdict: dict | None,
        expected_version: str,
        expected_spec_sha256: str,
    ) -> int:
        """Install a new spec version in one compare-and-swap UPDATE.

        Editing a strategy must invalidate the verdict issued against the old
        bytes in the same statement that installs the new ones: a separate
        clearing write would leave a window where the new version is schedulable
        on the old verdict. ``spec_sha256 IS ?`` is NULL-safe; both guards are
        required (not optional) because this method exists only for the
        versioned edit path, and a legacy row without comparable state is
        handled by the facade instead.

        Args:
            strategy_id: Artifact id to update.
            spec_json: Canonical JSON text of the new spec.
            artifact_version: New version string to store.
            spec_sha256: sha256 of exactly ``spec_json``.
            readiness: New scheduling readiness.
            evaluator_verdict: Verdict to store; an edit always clears it.
            previous_verdict: The current verdict this edit displaces.
            expected_version: Require this ``artifact_version``.
            expected_spec_sha256: Require this ``spec_sha256``.

        Returns:
            The UPDATE rowcount: 1 when written, 0 when the row is missing or
            either guard failed.

        Raises:
            ValueError: if *readiness* is outside the closed set.
        """
        validate_artifact_readiness(readiness)
        with self._write_transaction():
            cursor = self._conn.execute(
                """
                UPDATE artifacts
                SET spec = ?, artifact_version = ?, spec_sha256 = ?,
                    readiness = ?, evaluator_verdict = ?, previous_verdict = ?,
                    validation_status = ?, updated_at = ?
                WHERE id = ? AND artifact_version = ? AND spec_sha256 IS ?
                """,
                (
                    spec_json,
                    artifact_version,
                    spec_sha256,
                    readiness,
                    _json_dumps(evaluator_verdict)
                    if evaluator_verdict is not None
                    else None,
                    _json_dumps(previous_verdict)
                    if previous_verdict is not None
                    else None,
                    # A new version has never been validated; the gate records
                    # VALIDATED/REJECTED for it later.
                    ValidationStatus.UNVALIDATED.value,
                    _now_iso(),
                    strategy_id,
                    expected_version,
                    expected_spec_sha256,
                ),
            )
            return cursor.rowcount

    # -- Bench history ------------------------------------------------------

    @staticmethod
    def _bench_values(result: BenchResult) -> dict[str, object]:
        """Column values of a bench row other than ``id`` and ``created_at``.

        One list serves both the insert and the duplicate check, so a column
        added to one cannot be missing from the other.
        """
        return {
            "artifact_id": result.artifact_id,
            "bench_type": result.bench_type,
            "ic_mean": result.ic_mean,
            "ic_std": result.ic_std,
            "ir": result.ir,
            "ic_positive_ratio": result.ic_positive_ratio,
            "t_stat": result.t_stat,
            "sharpe": result.sharpe,
            "annual_return": result.annual_return,
            "max_drawdown": result.max_drawdown,
            "calmar": result.calmar,
            "category": result.category.value if result.category else None,
            "train_start": result.train_start,
            "train_end": result.train_end,
            "test_start": result.test_start,
            "test_end": result.test_end,
            "run_dir": result.run_dir,
        }

    @_synchronized
    def record_bench(self, result: BenchResult) -> int:
        """Record a bench result.  Returns the result ID."""
        with self._write_transaction():
            return self._insert_bench(result)

    def _insert_bench(self, result: BenchResult) -> int:
        """Insert within the caller's transaction."""
        values = self._bench_values(result)
        values["created_at"] = result.created_at or _now_iso()
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)

        cursor = self._conn.execute(
            f"INSERT INTO bench_history ({columns}) VALUES ({placeholders})",
            list(values.values()),
        )
        return cursor.lastrowid  # type: ignore[return-value]

    @_synchronized
    def record_bench_once(self, result: BenchResult) -> int | None:
        """Atomically record a measurement, returning None for a duplicate.

        BEGIN IMMEDIATE serializes this check and insert across connections.
        Directory, timestamp, bench label and grading do not change evidence.
        """
        with self._write_transaction():
            if self.has_bench_result(result):
                return None
            return self._insert_bench(result)

    @_synchronized
    def get_bench_history(
        self,
        artifact_id: str,
        *,
        limit: int = 50,
        bench_types: Sequence[str] | None = None,
    ) -> Sequence[BenchResult]:
        """Get bench history for an artifact, newest first.

        Args:
            artifact_id: Artifact whose bench rows are returned.
            limit: Maximum number of rows.
            bench_types: When given, only rows of these bench types are
                returned, and ``limit`` applies after the filter.

        Returns:
            Matching bench results, newest first.
        """
        type_clause = ""
        params: list[object] = [artifact_id]
        if bench_types is not None:
            types = list(bench_types)
            if not types:
                return []
            type_clause = f"AND bench_type IN ({', '.join('?' for _ in types)})"
            params.extend(types)
        params.append(limit)
        rows = self._conn.execute(
            f"""
            SELECT * FROM bench_history
            WHERE artifact_id = ? {type_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_bench_result(row) for row in rows]

    @_synchronized
    def has_bench_result(self, result: BenchResult) -> bool:
        """Return whether an identical bench row is already stored.

        ``IS`` rather than ``=`` makes two missing values compare equal, so a
        metric absent from both rows still counts as a match.

        Args:
            result: Candidate measurement; location, time and grading metadata
                are ignored.

        Returns:
            ``True`` when a stored measurement has the same artifact, windows and metrics.
        """
        values = self._bench_values(result)
        for metadata in ("run_dir", "bench_type", "category"):
            values.pop(metadata)
        where = " AND ".join(f"{column} IS ?" for column in values)
        row = self._conn.execute(
            f"SELECT 1 FROM bench_history WHERE {where} LIMIT 1",
            list(values.values()),
        ).fetchone()
        return row is not None

    # -- Decay snapshots ----------------------------------------------------

    @_synchronized
    def record_decay_snapshot(self, snapshot: DecaySnapshot) -> int:
        """Record a decay monitoring snapshot.  Returns the snapshot ID."""
        now = _now_iso()
        created_at = snapshot.created_at or now
        signal_value = snapshot.decay_signal.value if snapshot.decay_signal else None

        with self._write_transaction():
            cursor = self._conn.execute(
                """
                INSERT INTO decay_snapshots (
                    artifact_id, rolling_ic_mean, rolling_ir,
                    baseline_ic_mean, ic_ratio, decay_signal,
                    consecutive_warnings, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.artifact_id,
                    snapshot.rolling_ic_mean,
                    snapshot.rolling_ir,
                    snapshot.baseline_ic_mean,
                    snapshot.ic_ratio,
                    signal_value,
                    snapshot.consecutive_warnings,
                    snapshot.detail,
                    created_at,
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    @_synchronized
    def get_decay_history(
        self, artifact_id: str, *, limit: int = 20
    ) -> Sequence[DecaySnapshot]:
        """Get decay snapshots for an artifact, newest first."""
        rows = self._conn.execute(
            """
            SELECT * FROM decay_snapshots
            WHERE artifact_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (artifact_id, limit),
        ).fetchall()
        return [self._row_to_decay_snapshot(row) for row in rows]
