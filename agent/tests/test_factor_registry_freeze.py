from __future__ import annotations

import hashlib
import json

import pytest

from src.factors.registry_freeze import CandidateRow, freeze_registry

_RM_FIXTURE_DIGEST = "afab1fe35f384532ea0c9c7b9f5427166013537a080164eede8106b45ed9f6e0"
_RM_FIXTURE_ROWS = (
    CandidateRow(
        candidate_id="academic_bab",
        zoo="academic",
        universe=("equity_us", "equity_cn", "equity_hk"),
        columns_required=("close",),
        min_warmup_bars=253,
    ),
    CandidateRow(
        candidate_id="academic_carhart_mom",
        zoo="academic",
        universe=("equity_us", "equity_cn", "equity_hk"),
        columns_required=("close",),
        min_warmup_bars=252,
    ),
    CandidateRow(
        candidate_id="academic_high52w",
        zoo="academic",
        universe=("equity_us", "equity_cn", "equity_hk"),
        columns_required=("close",),
        min_warmup_bars=252,
    ),
)


def test_rm_fixture_rows_produce_the_known_digest() -> None:
    # Given the exact RM fixture rows, including its intentionally stale 252.
    rows = _RM_FIXTURE_ROWS

    # When the selected registry is frozen.
    frozen = freeze_registry(rows)

    # Then the canonical bytes match RM's independently published known answer.
    assert frozen.registry_digest == _RM_FIXTURE_DIGEST


def test_candidate_id_order_does_not_change_the_digest() -> None:
    # Given the same selected candidates in opposite request orders.
    forward = _RM_FIXTURE_ROWS
    reverse = tuple(reversed(_RM_FIXTURE_ROWS))

    # When each request order is frozen.
    forward_digest = freeze_registry(forward).registry_digest
    reverse_digest = freeze_registry(reverse).registry_digest

    # Then candidate-id canonicalization makes them identical.
    assert reverse_digest == forward_digest


def test_current_registry_digest_uses_actual_vt_metadata() -> None:
    # Given the real local registry and an independent stdlib digest calculation.
    from src.factors.registry import Registry
    from src.factors.registry_freeze import TOOL_SCHEMA_VERSION, rows_from_registry

    registry = Registry()
    candidate_ids = (
        "academic_bab",
        "academic_carhart_mom",
        "academic_high52w",
    )
    expected_rows = []
    for candidate_id in candidate_ids:
        alpha = registry.get(candidate_id)
        expected_rows.append(
            {
                "candidate_id": alpha.id,
                "zoo": alpha.zoo,
                "universe": alpha.meta["universe"],
                "columns_required": alpha.meta["columns_required"],
                "min_warmup_bars": alpha.meta["min_warmup_bars"],
            }
        )
    expected_document = {
        "schema": "factor-registry-v1",
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "candidates": sorted(expected_rows, key=lambda row: row["candidate_id"]),
    }
    expected_encoded = json.dumps(
        expected_document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    expected_digest = hashlib.sha256(expected_encoded).hexdigest()

    # When the three current candidates are read and frozen.
    frozen = freeze_registry(rows_from_registry(registry, candidate_ids))

    # Then VT publishes its actual 253-bar Carhart metadata, never stale RM 252.
    assert registry.get("academic_carhart_mom").meta["min_warmup_bars"] == 253
    assert frozen.registry_digest == expected_digest
    assert frozen.registry_digest != _RM_FIXTURE_DIGEST


def test_canonical_rows_have_only_the_five_frozen_keys() -> None:
    # Given a frozen candidate whose registry contains unrelated metadata.
    frozen = freeze_registry(_RM_FIXTURE_ROWS[:1])

    # When its canonical row is published.
    document = frozen.candidates[0].document()

    # Then nickname, timestamps, source files, and module paths are excluded.
    assert set(document) == {
        "candidate_id",
        "zoo",
        "universe",
        "columns_required",
        "min_warmup_bars",
    }


@pytest.mark.parametrize(
    ("candidate_ids", "expected_kind"),
    [
        ([], "empty"),
        (["academic_bab", "academic_bab"], "duplicate"),
        (["academic_does_not_exist"], "unknown"),
        (["not a candidate"], "malformed"),
        (
            [f"academic_unknown_{index:03d}" for index in range(201)],
            "too_many",
        ),
    ],
)
def test_invalid_candidate_id_selections_raise_typed_errors(
    candidate_ids: list[str],
    expected_kind: str,
) -> None:
    # Given a real registry and one invalid explicit selection.
    from src.factors.registry import Registry
    from src.factors.registry_freeze import RegistryFreezeError, rows_from_registry

    registry = Registry()

    # When the selection is resolved.
    with pytest.raises(RegistryFreezeError) as caught:
        rows_from_registry(registry, candidate_ids)

    # Then the typed failure names the validation class; no fallback is returned.
    assert caught.value.kind == expected_kind


@pytest.mark.parametrize(
    "rows",
    [
        (),
        (_RM_FIXTURE_ROWS[0], _RM_FIXTURE_ROWS[0]),
        tuple(
            CandidateRow(
                candidate_id=f"academic_generated_{index:03d}",
                zoo="academic",
                universe=("equity_us",),
                columns_required=("close",),
                min_warmup_bars=1,
            )
            for index in range(201)
        ),
    ],
)
def test_invalid_row_sets_raise_typed_errors(rows: tuple[CandidateRow, ...]) -> None:
    # Given an empty, duplicate, or over-limit selected row set.
    from src.factors.registry_freeze import RegistryFreezeError

    # When the set is frozen, then validation fails closed.
    with pytest.raises(RegistryFreezeError):
        freeze_registry(rows)


def test_malformed_candidate_metadata_raises_a_typed_error() -> None:
    # Given metadata with a negative warmup bound.
    from src.factors.registry_freeze import RegistryFreezeError

    # When the canonical row is constructed, then it is rejected as malformed.
    with pytest.raises(RegistryFreezeError) as caught:
        CandidateRow(
            candidate_id="academic_bab",
            zoo="academic",
            universe=("equity_us",),
            columns_required=("close",),
            min_warmup_bars=-1,
        )

    assert caught.value.kind == "malformed"


def test_registry_response_cannot_substitute_another_candidate(monkeypatch) -> None:
    # Given a registry that returns a different candidate with stale metadata.
    from src.factors.base import Alpha
    from src.factors.registry import Registry
    from src.factors.registry_freeze import RegistryFreezeError, rows_from_registry

    registry = Registry()
    monkeypatch.setattr(
        registry,
        "get",
        lambda candidate_id: Alpha(
            id="academic_high52w",
            zoo="academic",
            module_path="rm.stale.fixture",
            meta={
                "universe": ["equity_us", "equity_cn", "equity_hk"],
                "columns_required": ["close"],
                "min_warmup_bars": 252,
            },
        ),
    )

    # When Carhart is requested but another candidate is returned.
    with pytest.raises(RegistryFreezeError) as caught:
        rows_from_registry(registry, ["academic_carhart_mom"])

    # Then the identity mismatch is rejected instead of freezing substituted data.
    assert caught.value.kind == "malformed"
