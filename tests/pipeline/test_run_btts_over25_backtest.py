"""
tests/pipeline/test_run_btts_over25_backtest.py
--------------------------------------------------
Bloco O — backtesting/run_btts_over25_backtest.py::run_fast() lê
dc_ratings.json directamente do disco e procurava as equipas com o nome
cru do CSV (comentário antigo no código: "dc_ratings stores raw CSV team
names (no normalisation)"). Depois de models/train_dc.py passar a gravar
chaves normalizadas (normalize_team_names(), a mesma convenção usada em
pipeline/transform.py::compute_final_probability_dc para o consumo ao
vivo), este lookup tinha de ser actualizado também — senão passava a
falhar 100% das vezes aqui, exactamente o mesmo bug movido de sítio.

Todos os dados aqui são sintéticos (# synthetic).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import backtesting.run_btts_over25_backtest as mod
from backtesting.run_btts_over25_backtest import run_fast


def _synthetic_dc_ratings(tmp_path):  # synthetic
    """dc_ratings.json no formato PÓS Bloco O — chaves já normalizadas
    (train_all() grava normalize_team_names(team), não o nome cru do CSV)."""
    ratings = {
        "Championship": {
            "teams": {
                "blackburn": {"attack": 0.15, "defence": -0.05},
                "wolverhampton wanderers": {"attack": 0.08, "defence": 0.02},
            },
            "home_adv": 0.25, "rho": -0.08,
        },
    }
    dc_path = tmp_path / "dc_ratings.json"
    dc_path.write_text(json.dumps(ratings), encoding="utf-8")
    return dc_path


def _match_row(**over):  # synthetic
    base = {
        "Date": pd.Timestamp("2026-01-10"), "Div": "E1",
        # 'Blackburn' (maiúscula, como o CSV) e 'Wolves' (abreviatura do CSV,
        # ver _TEAM_ABBREVS em pipeline/transform.py: 'wolves' -> 'wolverhampton wanderers')
        "HomeTeam": "Blackburn", "AwayTeam": "Wolves",
        "FTHG": 2, "FTAG": 1,
        "btts": 1, "over25": 1, "btts_over25": 1,
    }
    base.update(over)
    return base


def test_run_fast_matches_teams_via_normalized_lookup(tmp_path, monkeypatch):
    """O nome do CSV ('Blackburn', 'Wolves') tem de encontrar a chave
    normalizada em dc_ratings.json ('blackburn', 'wolverhampton wanderers')
    — mirror exacto do que compute_final_probability_dc() já faz do lado
    vivo, incluindo a passagem por _TEAM_ABBREVS para 'Wolves'."""
    _synthetic_dc_ratings(tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)

    df = pd.DataFrame([_match_row()])
    out = run_fast(df)

    assert len(out) == 1  # encontrou as duas equipas e produziu uma linha
    assert out.iloc[0]["home"] == "Blackburn"
    assert out.iloc[0]["p_dc_conjunta"] is not None


def test_run_fast_skips_row_when_team_truly_absent(tmp_path, monkeypatch):
    dc_path = _synthetic_dc_ratings(tmp_path)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)

    df = pd.DataFrame([_match_row(HomeTeam="Equipa Inexistente FC")])
    out = run_fast(df)

    assert len(out) == 0


def test_run_fast_would_have_failed_with_raw_unnormalized_lookup(tmp_path, monkeypatch):
    """Prova negativa: um dc_ratings.json com chaves CRUAS (formato
    pré-Bloco O) e o mesmo nome curto do CSV não bate certo — confirma que
    a normalização introduzida é o que faz a diferença, não um acaso."""
    ratings = {
        "Championship": {
            "teams": {
                "Blackburn Rovers": {"attack": 0.15, "defence": -0.05},  # cru, não normalizado
                "Wolverhampton": {"attack": 0.08, "defence": 0.02},
            },
            "home_adv": 0.25, "rho": -0.08,
        },
    }
    dc_path = tmp_path / "dc_ratings.json"
    dc_path.write_text(json.dumps(ratings), encoding="utf-8")
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)

    df = pd.DataFrame([_match_row()])  # HomeTeam="Blackburn" (curto, como no CSV)
    out = run_fast(df)

    assert len(out) == 0  # "blackburn" normalizado != "Blackburn Rovers" cru
