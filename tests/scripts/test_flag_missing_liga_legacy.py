"""
tests/scripts/test_flag_missing_liga_legacy.py
------------------------------------------------
Testes para scripts.flag_missing_liga_legacy.flag_missing_liga (Ponto 1,
sessão data-quality-fixes): garante que picks legacy com liga vazia ficam
sempre marcados com data_quality_flag, sem inventar o nome da liga.
"""

from __future__ import annotations

from scripts.flag_missing_liga_legacy import FLAG_REASON, flag_missing_liga


def test_empty_liga_without_flag_gets_flagged():
    picks = [{"id": "1", "liga": "", "data_quality_flag": None}]
    out, n_marked = flag_missing_liga(picks)
    assert n_marked == 1
    assert out[0]["data_quality_flag"] == FLAG_REASON
    # nunca inventa o nome da liga
    assert out[0]["liga"] == ""


def test_missing_liga_key_gets_flagged():
    picks = [{"id": "2"}]
    out, n_marked = flag_missing_liga(picks)
    assert n_marked == 1
    assert out[0]["data_quality_flag"] == FLAG_REASON


def test_already_flagged_pick_not_double_counted():
    picks = [{"id": "3", "liga": "", "data_quality_flag": "pre_bugfix_liga_vazia"}]
    out, n_marked = flag_missing_liga(picks)
    assert n_marked == 0
    assert out[0]["data_quality_flag"] == "pre_bugfix_liga_vazia"


def test_pick_with_liga_untouched():
    picks = [{"id": "4", "liga": "Premier League"}]
    out, n_marked = flag_missing_liga(picks)
    assert n_marked == 0
    assert "data_quality_flag" not in out[0]


def test_mixed_batch_only_flags_empty_liga():
    picks = [
        {"id": "a", "liga": "La Liga"},
        {"id": "b", "liga": ""},
        {"id": "c", "liga": "DESCONHECIDA"},
    ]
    out, n_marked = flag_missing_liga(picks)
    # "DESCONHECIDA" is a non-empty string (already an explicit sentinel from
    # the scanners, not the pre-fix contamination this script targets)
    assert n_marked == 1
    flagged_ids = {p["id"] for p in out if p.get("data_quality_flag")}
    assert flagged_ids == {"b"}


def test_does_not_mutate_input_list_in_place():
    picks = [{"id": "1", "liga": ""}]
    out, _ = flag_missing_liga(picks)
    assert "data_quality_flag" not in picks[0]
    assert out[0]["data_quality_flag"] == FLAG_REASON
