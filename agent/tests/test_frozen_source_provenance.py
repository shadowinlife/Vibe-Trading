"""Per-symbol provenance through frozen manifests and sealed replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest import runner


def _frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [10.0, 10.5, 11.0],
            "high": [10.5, 11.0, 11.5],
            "low": [9.5, 10.0, 10.5],
            "close": [10.2, 10.7, 11.2],
            "volume": [100.0, 110.0, 120.0],
        },
        index=pd.bdate_range("2024-01-02", periods=3),
    )
    frame.attrs = {"adjustment": "split_dividend"}
    return frame


_NO_PROVENANCE = object()


def _write_frozen_snapshot(
    tmp_path: Path,
    *,
    provenance: object = _NO_PROVENANCE,
    attrs: dict[str, object] | None = None,
    sources: list[str] | None = None,
    caliber_warning: str | None = None,
) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    raw = _frame().rename_axis("date").to_csv().encode("utf-8")
    (snapshot / "AAPL.US.csv").write_bytes(raw)
    manifest = {
        "schema_version": 1,
        "interval": "1D",
        "sources": sources if sources is not None else ["yahoo"],
        "frames": {
            "AAPL.US": {
                "path": "AAPL.US.csv",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "attrs": attrs or {},
            }
        },
    }
    if provenance is not _NO_PROVENANCE:
        manifest["data_provenance"] = provenance
    if caliber_warning is not None:
        manifest["caliber_warning"] = caliber_warning
    path = snapshot / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _frozen_config(manifest: Path) -> dict[str, object]:
    return {
        "source": "local",
        "codes": ["AAPL.US"],
        "start_date": "2024-01-01",
        "end_date": "2024-01-05",
        "interval": "1D",
        "frozen_data_manifest": str(manifest),
    }


def test_frozen_loader_restores_only_whitelisted_attrs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backtest.frozen import load_frozen_frames

    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    manifest = _write_frozen_snapshot(
        tmp_path,
        attrs={
            "quote_currency": "USD",
            "currency_conversion": "none",
            "adjustment": "raw",
            "api_key": "secret-token",
            "arbitrary": "must-not-survive",
        },
    )

    frames, loaded = load_frozen_frames(str(manifest), _frozen_config(manifest))

    assert loaded["sources"] == ["yahoo"]
    assert frames["AAPL.US"].attrs == {
        "quote_currency": "USD",
        "currency_conversion": "none",
        "adjustment": "raw",
    }


def test_legacy_frozen_manifest_returns_explicit_unknown_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    manifest = _write_frozen_snapshot(
        tmp_path, attrs={"quote_currency": "USD", "adjustment": "raw"}
    )

    result = runner.fetch_data_map(_frozen_config(manifest))

    assert result.data_provenance == {
        "AAPL.US": {
            "source": "unknown",
            "adjustment": "raw",
            "attrs": {"quote_currency": "USD", "adjustment": "raw"},
        }
    }
    assert "fallback_attempts" not in result.data_provenance["AAPL.US"]


def test_new_frozen_manifest_returns_exact_validated_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    original = {
        "source": "original-vendor",
        "adjustment": "raw",
        "fallback_attempts": [{"source": "requested-vendor", "reason": "no_data"}],
        "attrs": {"quote_currency": "USD", "adjustment": "raw"},
    }
    manifest = _write_frozen_snapshot(
        tmp_path,
        provenance={"AAPL.US": original},
        attrs=original["attrs"],
        sources=["original-vendor"],
    )

    result = runner.fetch_data_map(_frozen_config(manifest))

    assert result.data_provenance == {"AAPL.US": original}


def test_new_frozen_manifest_rejects_source_set_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    entry = {
        "source": "yahoo",
        "adjustment": "raw",
        "fallback_attempts": [],
        "attrs": {"adjustment": "raw"},
    }
    manifest = _write_frozen_snapshot(
        tmp_path,
        provenance={"AAPL.US": entry},
        attrs=entry["attrs"],
        sources=["local"],
    )

    with pytest.raises(ValueError, match="sources"):
        runner.fetch_data_map(_frozen_config(manifest))


def test_new_frozen_manifest_rejects_provenance_adjustment_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    entry = {
        "source": "yahoo",
        "adjustment": "raw",
        "fallback_attempts": [],
        "attrs": {"adjustment": "split_dividend"},
    }
    manifest = _write_frozen_snapshot(
        tmp_path,
        provenance={"AAPL.US": entry},
        attrs=entry["attrs"],
    )

    with pytest.raises(ValueError, match="adjustment"):
        runner.fetch_data_map(_frozen_config(manifest))


def test_new_frozen_manifest_rejects_frame_attrs_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    entry = {
        "source": "yahoo",
        "adjustment": "raw",
        "fallback_attempts": [],
        "attrs": {"adjustment": "raw"},
    }
    manifest = _write_frozen_snapshot(
        tmp_path,
        provenance={"AAPL.US": entry},
        attrs={"adjustment": "split_dividend"},
    )

    with pytest.raises(ValueError, match="attrs"):
        runner.fetch_data_map(_frozen_config(manifest))


def test_new_frozen_manifest_recomputes_caliber_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backtest.loaders.registry import additive_caliber_warning

    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    entry = {
        "source": "tencent",
        "adjustment": "split_dividend_additive",
        "fallback_attempts": [],
        "attrs": {"adjustment": "split_dividend_additive"},
    }
    manifest = _write_frozen_snapshot(
        tmp_path,
        provenance={"AAPL.US": entry},
        attrs=entry["attrs"],
        sources=["tencent"],
        caliber_warning="stale warning",
    )

    result = runner.fetch_data_map(_frozen_config(manifest))

    assert result.caliber_warning == additive_caliber_warning(
        {"AAPL.US": ("tencent", "split_dividend_additive")}
    )


@pytest.mark.parametrize(
    "provenance",
    [
        None,
        {},
        {"AAPL.US": None},
        {"AAPL.US": {"source": "yahoo", "adjustment": "raw", "attrs": {}}},
        {
            "AAPL.US": {
                "source": None,
                "adjustment": "raw",
                "fallback_attempts": [],
                "attrs": {},
            }
        },
    ],
    ids=[
        "not_a_mapping",
        "missing_symbol",
        "malformed_entry",
        "partial_entry",
        "bad_source",
    ],
)
def test_new_frozen_manifest_rejects_missing_or_malformed_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: object
) -> None:
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    manifest = _write_frozen_snapshot(tmp_path, provenance=provenance)

    with pytest.raises(ValueError, match="AAPL.US") as caught:
        runner.fetch_data_map(_frozen_config(manifest))

    assert "provenance" in str(caught.value)
