"""
tests/pipeline/test_etl_filters.py
-----------------------------------
Tests for pipeline.etl.filter_by_league and filter_alert_candidates.

Run with:
    pytest tests/pipeline/test_etl_filters.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from pipeline.etl import filter_alert_candidates, filter_by_league

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WHITELIST = ["Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1"]


def _pick(**kwargs: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "id": "1",
        "casa": "Team A",
        "fora": "Team B",
        "liga": "",
        "movimento": "SHORTENING",
        "result_over25": "",
    }
    return {**base, **kwargs}


# ---------------------------------------------------------------------------
# filter_by_league — league whitelist enforcement
# ---------------------------------------------------------------------------


class TestFilterByLeagueEmptyLiga:
    def test_empty_liga_rejected(self, tmp_path: Path) -> None:
        """Pick with empty 'liga' must be rejected and written to rejected_picks.json."""
        pick = _pick(id="1", liga="")
        accepted, n_rej = filter_by_league([pick], _WHITELIST, tmp_path / "rejected.json")

        assert len(accepted) == 0
        assert n_rej == 1

    def test_empty_liga_reject_reason(self, tmp_path: Path) -> None:
        """Rejected pick must carry reject_reason='liga_vazia'."""
        reject_path = tmp_path / "rejected.json"
        filter_by_league([_pick(id="1", liga="")], _WHITELIST, reject_path)

        rejected = json.loads(reject_path.read_text())
        assert len(rejected) == 1
        assert rejected[0]["reject_reason"] == "liga_vazia"


class TestFilterByLeagueNonWhitelisted:
    def test_non_whitelisted_liga_rejected(self, tmp_path: Path) -> None:
        """Pick with a liga not in the whitelist must be rejected."""
        pick = _pick(id="2", liga="Ekstraklasa")
        accepted, n_rej = filter_by_league([pick], _WHITELIST, tmp_path / "rejected.json")

        assert len(accepted) == 0
        assert n_rej == 1

    def test_non_whitelisted_reject_reason_contains_liga(self, tmp_path: Path) -> None:
        """Reject reason must name the offending league."""
        reject_path = tmp_path / "rejected.json"
        filter_by_league([_pick(id="2", liga="Copa Sudamericana")], _WHITELIST, reject_path)

        rejected = json.loads(reject_path.read_text())
        assert "liga_fora_da_whitelist" in rejected[0]["reject_reason"]
        assert "Copa Sudamericana" in rejected[0]["reject_reason"]

    @pytest.mark.parametrize("liga", ["Angola", "USL League One", "Brasileirao", "Veikkausliiga"])
    def test_known_bad_leagues_rejected(self, liga: str, tmp_path: Path) -> None:
        """Leagues observed in real picks.json that must not reach production alerts."""
        pick = _pick(id="x", liga=liga)
        accepted, n_rej = filter_by_league([pick], _WHITELIST, tmp_path / "rejected.json")
        assert len(accepted) == 0
        assert n_rej == 1


class TestFilterByLeagueAccepted:
    @pytest.mark.parametrize("liga", ["Premier League", "La Liga", "Bundesliga"])
    def test_whitelisted_liga_accepted(self, liga: str, tmp_path: Path) -> None:
        """Picks with a whitelisted liga must pass through unchanged."""
        pick = _pick(id="3", liga=liga)
        accepted, n_rej = filter_by_league([pick], _WHITELIST, tmp_path / "rejected.json")

        assert len(accepted) == 1
        assert n_rej == 0
        assert not (tmp_path / "rejected.json").exists()

    def test_accepted_pick_unchanged(self, tmp_path: Path) -> None:
        """The accepted pick dict must be the same object (no mutations)."""
        pick = _pick(id="5", liga="Serie A", movimento="SHORTENING")
        accepted, _ = filter_by_league([pick], _WHITELIST, tmp_path / "rejected.json")
        assert accepted[0] is pick


class TestFilterByLeagueMixed:
    def test_mixed_picks_split_correctly(self, tmp_path: Path) -> None:
        picks = [
            _pick(id="a", liga="Premier League"),
            _pick(id="b", liga=""),
            _pick(id="c", liga="Ekstraklasa"),
            _pick(id="d", liga="Bundesliga"),
        ]
        accepted, n_rej = filter_by_league(picks, _WHITELIST, tmp_path / "rejected.json")

        assert len(accepted) == 2
        assert n_rej == 2
        assert {p["id"] for p in accepted} == {"a", "d"}

    def test_rejected_file_written(self, tmp_path: Path) -> None:
        reject_path = tmp_path / "rejected.json"
        picks = [_pick(id="99", liga="Copa Sudamericana")]
        filter_by_league(picks, _WHITELIST, reject_path)

        assert reject_path.exists()
        data = json.loads(reject_path.read_text())
        assert data[0]["id"] == "99"
        assert "reject_reason" in data[0]


class TestFilterByLeagueDeduplication:
    def test_repeated_call_does_not_duplicate(self, tmp_path: Path) -> None:
        """Calling filter_by_league twice with the same pick must not write duplicates."""
        reject_path = tmp_path / "rejected.json"
        pick = _pick(id="42", liga="")

        filter_by_league([pick], _WHITELIST, reject_path)
        filter_by_league([pick], _WHITELIST, reject_path)

        rejected = json.loads(reject_path.read_text())
        assert len(rejected) == 1

    def test_new_pick_appended_to_existing_file(self, tmp_path: Path) -> None:
        """Second call with a different id appends to the rejected file."""
        reject_path = tmp_path / "rejected.json"

        filter_by_league([_pick(id="1", liga="")], _WHITELIST, reject_path)
        filter_by_league([_pick(id="2", liga="Ekstraklasa")], _WHITELIST, reject_path)

        rejected = json.loads(reject_path.read_text())
        assert len(rejected) == 2
        assert {r["id"] for r in rejected} == {"1", "2"}


# ---------------------------------------------------------------------------
# filter_alert_candidates — DRIFTING exclusion
# ---------------------------------------------------------------------------


class TestFilterAlertCandidatesDrifting:
    def test_drifting_excluded_from_alerts(self) -> None:
        """DRIFTING picks must not appear in alert candidates."""
        picks = [
            _pick(id="1", movimento="DRIFTING"),
            _pick(id="2", movimento="SHORTENING"),
        ]
        alerts = filter_alert_candidates(picks)
        ids = {p["id"] for p in alerts}

        assert "1" not in ids
        assert "2" in ids

    def test_drifting_pick_stays_in_original_list(self) -> None:
        """filter_alert_candidates must not mutate the input — DRIFTING stays in full list."""
        drifting = _pick(id="d1", movimento="DRIFTING")
        shortening = _pick(id="s1", movimento="SHORTENING")
        all_picks = [drifting, shortening]

        alert_picks = filter_alert_candidates(all_picks)

        assert len(all_picks) == 2, "Input list must be unchanged"
        assert len(alert_picks) == 1
        assert alert_picks[0]["id"] == "s1"

    def test_all_drifting_returns_empty(self) -> None:
        picks = [_pick(id=str(i), movimento="DRIFTING") for i in range(5)]
        alerts = filter_alert_candidates(picks)
        assert alerts == []

    def test_case_insensitive_drifting(self) -> None:
        """movimento values should be compared case-insensitively."""
        picks = [
            _pick(id="1", movimento="drifting"),
            _pick(id="2", movimento="Drifting"),
            _pick(id="3", movimento="SHORTENING"),
        ]
        alerts = filter_alert_candidates(picks)
        ids = {p["id"] for p in alerts}
        assert ids == {"3"}


class TestFilterAlertCandidatesNonDrifting:
    @pytest.mark.parametrize("movimento", ["SHORTENING", "STEAM", "STABLE", ""])
    def test_non_drifting_movements_pass(self, movimento: str) -> None:
        """All movement values except DRIFTING must pass through."""
        pick = _pick(id="1", movimento=movimento)
        alerts = filter_alert_candidates([pick])
        assert len(alerts) == 1

    def test_empty_list_returns_empty(self) -> None:
        assert filter_alert_candidates([]) == []
