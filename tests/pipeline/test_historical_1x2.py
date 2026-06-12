"""
tests/pipeline/test_historical_1x2.py
---------------------------------------
Testes para a extensão 1X2 de pipeline/historical.py e
backtesting/run_sharp1x2_signal.py.

Todos os dados usados são SINTÉTICOS — nunca dados reais.

Correr com:
    pytest tests/pipeline/test_historical_1x2.py -v --tb=short
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.historical import (
    _normalise,
    _synthetic_season,
    generate_synthetic,
    KEEP_COLS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_with_1x2(**extra_cols) -> pd.DataFrame:
    """Minimal raw CSV row with 1X2 columns present."""
    base = {
        "Div": "E0", "Date": "01/08/2526",
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "FTHG": 2, "FTAG": 1, "FTR": "H",
        "P>2.5": 1.85, "P<2.5": 2.00,
        "PC>2.5": 1.83, "PC<2.5": 2.02,
        "B365>2.5": 1.80, "B365<2.5": 2.05,
        "Avg>2.5": 1.82, "Max>2.5": 1.86,
        "PSH": 2.10, "PSD": 3.40, "PSA": 3.80,
        "PSCH": 1.95, "PSCD": 3.50, "PSCA": 4.10,
        "B365H": 2.05, "B365D": 3.30, "B365A": 3.75,
    }
    base.update(extra_cols)
    return pd.DataFrame([base])


def _make_raw_without_1x2() -> pd.DataFrame:
    """Minimal raw CSV row WITHOUT 1X2 columns (pre-extension dataset)."""
    return pd.DataFrame([{
        "Div": "E0", "Date": "01/08/2526",
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "FTHG": 2, "FTAG": 1,
        "P>2.5": 1.85, "P<2.5": 2.00,
        "PC>2.5": 1.83, "PC<2.5": 2.02,
        "B365>2.5": 1.80, "B365<2.5": 2.05,
        "Avg>2.5": 1.82, "Max>2.5": 1.86,
    }])


# ---------------------------------------------------------------------------
# TAREFA 2.1 — _normalise() extrai colunas 1X2
# ---------------------------------------------------------------------------

class TestNormalise1x2:
    def test_keeps_ftr_when_present(self) -> None:
        raw = _make_raw_with_1x2()
        out = _normalise(raw, epoch="2526", div="E0")
        assert "FTR" in out.columns
        assert out["FTR"].iloc[0] == "H"

    def test_keeps_pinnacle_opening_odds(self) -> None:
        raw = _make_raw_with_1x2()
        out = _normalise(raw, epoch="2526", div="E0")
        for col in ("PSH", "PSD", "PSA"):
            assert col in out.columns, f"Missing {col}"
            assert pd.to_numeric(out[col].iloc[0], errors="coerce") > 1.0

    def test_keeps_pinnacle_closing_odds(self) -> None:
        raw = _make_raw_with_1x2()
        out = _normalise(raw, epoch="2526", div="E0")
        for col in ("PSCH", "PSCD", "PSCA"):
            assert col in out.columns, f"Missing {col}"
            assert pd.to_numeric(out[col].iloc[0], errors="coerce") > 1.0

    def test_keeps_b365_1x2_odds(self) -> None:
        raw = _make_raw_with_1x2()
        out = _normalise(raw, epoch="2526", div="E0")
        for col in ("B365H", "B365D", "B365A"):
            assert col in out.columns, f"Missing {col}"

    def test_pin_drop_computed_correctly(self) -> None:
        """pin_drop_h = PSH/PSCH - 1"""
        raw = _make_raw_with_1x2(PSH=2.10, PSCH=1.95)
        out = _normalise(raw, epoch="2526", div="E0")
        expected = 2.10 / 1.95 - 1
        assert "pin_drop_h" in out.columns
        assert abs(out["pin_drop_h"].iloc[0] - expected) < 1e-6

    def test_pin_drop_positive_when_odds_shorten(self) -> None:
        """PSH > PSCH → odds fell → pin_drop_h > 0 (money came in on home)."""
        raw = _make_raw_with_1x2(PSH=3.00, PSCH=2.50)
        out = _normalise(raw, epoch="2526", div="E0")
        assert out["pin_drop_h"].iloc[0] > 0

    def test_pin_drop_negative_when_odds_lengthen(self) -> None:
        """PSH < PSCH → odds drifted → pin_drop_h < 0."""
        raw = _make_raw_with_1x2(PSH=2.50, PSCH=3.00)
        out = _normalise(raw, epoch="2526", div="E0")
        assert out["pin_drop_h"].iloc[0] < 0

    def test_no_crash_when_1x2_absent(self) -> None:
        """_normalise() must not raise when 1X2 columns are absent."""
        raw = _make_raw_without_1x2()
        out = _normalise(raw, epoch="2526", div="E0")
        # Over/Under columns still present
        assert "P>2.5" in out.columns
        # pin_drop columns should be NaN (column exists but all NaN)
        assert "pin_drop_h" in out.columns
        assert out["pin_drop_h"].isna().all()

    def test_pin_drop_nan_when_close_is_nan(self) -> None:
        """If PSCH is NaN, pin_drop_h must be NaN (not raise)."""
        raw = _make_raw_with_1x2()
        raw["PSCH"] = np.nan
        out = _normalise(raw, epoch="2526", div="E0")
        assert out["pin_drop_h"].isna().all()

    def test_keep_cols_includes_1x2(self) -> None:
        """KEEP_COLS must include all expected 1X2 columns."""
        for col in ("FTR", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA", "B365H", "B365D", "B365A"):
            assert col in KEEP_COLS, f"{col} missing from KEEP_COLS"


# ---------------------------------------------------------------------------
# TAREFA 2.1 — _synthetic_season() gera dados 1X2
# ---------------------------------------------------------------------------

class TestSyntheticSeason1x2:
    def _get_season(self) -> pd.DataFrame:
        rng = np.random.default_rng(0)
        return _synthetic_season("E0", "2526", rng)

    def test_ftr_present_and_valid(self) -> None:
        df = self._get_season()
        assert "FTR" in df.columns
        assert set(df["FTR"].unique()).issubset({"H", "D", "A"})

    def test_ftr_consistent_with_scoreline(self) -> None:
        df = self._get_season()
        expected = df.apply(
            lambda r: "H" if r["FTHG"] > r["FTAG"] else ("D" if r["FTHG"] == r["FTAG"] else "A"),
            axis=1,
        )
        assert (df["FTR"] == expected).all()

    def test_pinnacle_1x2_columns_present(self) -> None:
        df = self._get_season()
        for col in ("PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA"):
            assert col in df.columns, f"Missing {col}"
            assert df[col].notna().all(), f"{col} has NaN"

    def test_b365_1x2_columns_present(self) -> None:
        df = self._get_season()
        for col in ("B365H", "B365D", "B365A"):
            assert col in df.columns, f"Missing {col}"

    def test_pin_drop_present_and_plausible(self) -> None:
        df = self._get_season()
        for col in ("pin_drop_h", "pin_drop_d", "pin_drop_a"):
            assert col in df.columns, f"Missing {col}"
            # Should be between -0.5 and +0.5 for synthetic data
            assert (df[col].abs() < 0.5).all(), f"{col} has implausible values"

    def test_pinnacle_odds_reasonable_range(self) -> None:
        """Pinnacle 1X2 odds should be between 1.1 and 20."""
        df = self._get_season()
        for col in ("PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA"):
            assert (df[col] >= 1.05).all(), f"{col} has odds < 1.05"
            assert (df[col] <= 30.0).all(), f"{col} has odds > 30"

    def test_generate_synthetic_has_1x2(self) -> None:
        """generate_synthetic() (all epochs × divisions) must include 1X2 columns."""
        df = generate_synthetic(seed=0)
        assert "FTR" in df.columns
        assert "PSH" in df.columns
        assert "pin_drop_h" in df.columns
        assert df["PSH"].notna().any()


# ---------------------------------------------------------------------------
# TAREFA 2.3 — run_sharp1x2_signal handles missing 1X2 gracefully
# ---------------------------------------------------------------------------

class TestSharp1x2SignalMissingColumns:
    def test_exits_cleanly_when_no_1x2_cols(self, tmp_path: Path) -> None:
        """Script must exit code 0 when CSV lacks 1X2 columns."""
        df = generate_synthetic(seed=1)
        # Remove all 1X2 columns to simulate pre-extension dataset
        drop_cols = [c for c in ("FTR", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA",
                                  "pin_drop_h", "pin_drop_d", "pin_drop_a") if c in df.columns]
        df = df.drop(columns=drop_cols)
        csv_path = tmp_path / "matches.csv"
        df.to_csv(csv_path, index=False)

        from backtesting.run_sharp1x2_signal import main
        rc = main(["--csv", str(csv_path)])
        assert rc == 0

    def test_exits_cleanly_when_csv_missing(self, tmp_path: Path) -> None:
        """Script must exit code 0 when CSV doesn't exist yet."""
        from backtesting.run_sharp1x2_signal import main
        rc = main(["--csv", str(tmp_path / "nonexistent.csv")])
        assert rc == 0

    def test_produces_report_when_1x2_present(self, tmp_path: Path) -> None:
        """When 1X2 data is present, script writes a report."""
        df = generate_synthetic(seed=2)
        csv_path = tmp_path / "matches.csv"
        df.to_csv(csv_path, index=False)

        import backtesting.run_sharp1x2_signal as mod
        orig_path = mod._REPORT_PATH
        report_path = tmp_path / "sharp1x2_signal.md"
        mod._REPORT_PATH = report_path
        mod._REPORT_DIR = tmp_path
        try:
            rc = mod.main(["--csv", str(csv_path)])
        finally:
            mod._REPORT_PATH = orig_path
            mod._REPORT_DIR = orig_path.parent

        assert rc == 0
        assert report_path.exists(), "Report not written when 1X2 data is present"
        content = report_path.read_text()
        assert "Q1" in content
        assert "Q2" in content
        assert "Q3" in content
        assert "Q4" in content
        assert "Conclusão" in content


# ---------------------------------------------------------------------------
# TAREFA 2 (ext) — análise de divergência e pin_drop inverso
# ---------------------------------------------------------------------------

def _make_1x2_df(n: int = 500, seed: int = 99) -> "pd.DataFrame":
    """Minimal synthetic 1X2 DataFrame for unit tests — fast, no network."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        lh = rng.uniform(0.8, 2.0)
        la = rng.uniform(0.8, 1.8)
        psh = round(1.0 / (lh / (lh + la + 0.3) * 1.04), 3)
        psd = round(1.0 / (0.3 / (lh + la + 0.3) * 1.04), 3)
        psa = round(1.0 / (la / (lh + la + 0.3) * 1.04), 3)
        psch = round(psh * rng.uniform(0.95, 1.02), 3)
        pscd = round(psd * rng.uniform(0.97, 1.03), 3)
        psca = round(psa * rng.uniform(0.95, 1.02), 3)
        # B365: same probs but 8% margin → generally lower odds
        b365h = round(1.0 / (lh / (lh + la + 0.3) * 1.08), 3)
        b365d = round(1.0 / (0.3 / (lh + la + 0.3) * 1.08), 3)
        b365a = round(1.0 / (la / (lh + la + 0.3) * 1.08), 3)
        ftr = rng.choice(["H", "D", "A"])
        rows.append({
            "Div": "E0", "Season": "2526", "Date": "2026-01-01",
            "FTR": ftr,
            "PSH": psh, "PSD": psd, "PSA": psa,
            "PSCH": psch, "PSCD": pscd, "PSCA": psca,
            "B365H": b365h, "B365D": b365d, "B365A": b365a,
        })
    return pd.DataFrame(rows)


class TestSharp1x2AnalysisFunctions:
    """Unit tests for Q2 reverse and Q4 divergence analysis functions."""

    def test_q2_reverse_returns_quartile_table(self) -> None:
        """_q2_reverse returns a DataFrame with quartile rows and overall dict."""
        from backtesting.run_sharp1x2_signal import _q2_reverse, _prepare
        df = _prepare(_make_1x2_df())
        by_q, overall = _q2_reverse(df)
        assert not by_q.empty
        assert "roi_pct" in by_q.columns
        assert "n" in overall and "roi_pct" in overall

    def test_q2_reverse_picks_min_drop(self) -> None:
        """Reverse strategy picks MIN pin_drop outcome (most drift)."""
        from backtesting.run_sharp1x2_signal import _prepare
        row = {
            "Div": "E0", "Season": "2526", "Date": "2026-01-01",
            "HomeTeam": "A", "AwayTeam": "B", "FTR": "A",
            "PSH": 2.00, "PSD": 3.50, "PSA": 3.80,
            "PSCH": 1.50, "PSCD": 3.55, "PSCA": 3.82,  # H drops most
            "B365H": 1.98, "B365D": 3.45, "B365A": 3.75,
        }
        clean = _prepare(pd.DataFrame([row]))
        assert len(clean) == 1
        # pin_drop_h = 2.00/1.50 - 1 = 0.333 (biggest) → reverse must NOT pick H
        min_col = clean[["pin_drop_h", "pin_drop_d", "pin_drop_a"]].iloc[0].idxmin()
        assert min_col != "pin_drop_h"

    def test_q4_divergence_empty_when_b365_missing(self) -> None:
        """_q4_divergence returns empty DataFrames when B365 columns absent."""
        from backtesting.run_sharp1x2_signal import _q4_divergence, _prepare
        df = _prepare(_make_1x2_df())
        no_b365 = df.drop(columns=[c for c in ("B365H", "B365D", "B365A") if c in df.columns])
        by_thresh, by_league = _q4_divergence(no_b365)
        assert by_thresh.empty and by_league.empty

    def test_q4_divergence_fires_with_artificial_data(self) -> None:
        """_q4_divergence detects bets when B365 is explicitly 12.5% above PSH."""
        from backtesting.run_sharp1x2_signal import _q4_divergence, _MIN_DIV_GLOBAL
        n = _MIN_DIV_GLOBAL + 50
        rng = np.random.default_rng(99)
        rows = [{"Div": "E0", "FTR": rng.choice(["H", "D", "A"]),
                 "PSH": 2.00, "PSD": 3.50, "PSA": 3.80,
                 "PSCH": 1.95, "PSCD": 3.55, "PSCA": 3.85,
                 "B365H": 2.25, "B365D": 3.45, "B365A": 3.75}
                for _ in range(n)]
        by_thresh, _ = _q4_divergence(pd.DataFrame(rows))
        assert not by_thresh.empty, "Should find bets with B365H 12.5% above PSH"
        assert (by_thresh["threshold"] == ">10%").any()

    def test_report_has_conclusion_section(self, tmp_path: Path) -> None:
        """Report must include a Conclusão section with veredictos."""
        import backtesting.run_sharp1x2_signal as mod
        df = _make_1x2_df(n=1000, seed=7)
        csv_path = tmp_path / "m.csv"
        df.to_csv(csv_path, index=False)
        orig = mod._REPORT_PATH
        mod._REPORT_PATH = tmp_path / "r.md"
        mod._REPORT_DIR = tmp_path
        try:
            mod.main(["--csv", str(csv_path)])
        finally:
            mod._REPORT_PATH = orig
            mod._REPORT_DIR = orig.parent
        content = (tmp_path / "r.md").read_text()
        assert "Conclusão" in content
        assert "Veredicto" in content
