"""
tests/pipeline/test_train_dc.py
---------------------------------
Bloco O — causa raiz confirmada: models/train_dc.py gravava as chaves de
equipa de dc_ratings.json cruas do CSV (football-data.co.uk), enquanto
pipeline/transform.py::compute_final_probability_dc() procura sempre por
normalize_team_names(casa/fora) — a mesma normalização usada do lado da
BSD (scan_over25.py::compute_prob()). Convenções diferentes nos dois lados
do mesmo lookup faziam o lookup falhar 100% das vezes, sem excepção nem
aviso, colapsando p_model em p_market (ev_final vira o simétrico do vig —
nenhum jogo passa o gate de EV por construção).

Estes testes cobrem só a correcção do lookup (train_all() normaliza as
chaves na escrita) — não tocam em MODEL_WEIGHT, no threshold de EV, nem em
nenhum gate.

Todos os dados aqui são sintéticos (# synthetic).
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from models.train_dc import train_all
from pipeline.transform import normalize_team_names


def _synthetic_matches(rows: list[tuple]) -> pd.DataFrame:  # synthetic
    """rows: (div, date, home, away, goals_home, goals_away)."""
    return pd.DataFrame(rows, columns=["Div", "date", "home", "away", "goals_home", "goals_away"])


def _round_robin(div: str, teams: list[str], n_rounds: int = 3) -> list[tuple]:  # synthetic
    """Gera jogos suficientes (>= MIN_GAMES_TO_FIT por omissão, ou o
    min_games passado ao teste) para o optimizer correr sem erro — cada
    par de equipas joga em casa e fora, repetido n_rounds vezes com datas
    a espaçar."""
    rows = []
    day = 1
    for _ in range(n_rounds):
        for i, home in enumerate(teams):
            for away in teams:
                if home == away:
                    continue
                rows.append((div, pd.Timestamp("2025-08-01") + pd.Timedelta(days=day),
                             home, away, (day % 3), ((day + 1) % 3)))
                day += 1
    return rows


# ---------------------------------------------------------------------------
# Bloco O — chaves normalizadas na escrita
# ---------------------------------------------------------------------------


def test_team_keys_are_normalized_lowercase():
    """A causa raiz directa: chaves de dc_ratings.json têm de sair na mesma
    convenção que compute_final_probability_dc() usa para procurar (lowercase,
    sem acentos, via normalize_team_names())."""
    rows = _round_robin("B1", ["Cercle Brugge", "Club Brugge", "Anderlecht"])
    df = _synthetic_matches(rows)

    ratings = train_all(df, min_games=1)

    teams = ratings["Belgian Pro League"]["teams"]
    assert "cercle brugge" in teams
    assert "club brugge" in teams
    assert "anderlecht" in teams
    # nunca as chaves cruas do CSV
    assert "Cercle Brugge" not in teams
    assert "Club Brugge" not in teams


def test_normalized_key_matches_what_the_consumer_looks_up():
    """O teste que prova a correcção fim-a-fim: normalize_team_names() do
    lado do consumo (pipeline/transform.py::compute_final_probability_dc,
    a mesma função que scan_over25.py::compute_prob() aplica aos nomes da
    BSD) tem de bater certo com a chave que train_all() escreveu — mesmo
    quando o CSV usa a forma abreviada ('Norwich', convenção comum no
    football-data.co.uk) e a BSD devolve o nome oficial completo
    ('Norwich City'). É exactamente o _TEAM_ABBREVS que faz a ponte."""
    rows = _round_robin("E1", ["Norwich", "Southampton"])
    df = _synthetic_matches(rows)

    ratings = train_all(df, min_games=1)
    teams = ratings["Championship"]["teams"]

    bsd_home_name = "Norwich City"  # nome oficial completo, como a BSD devolve
    assert normalize_team_names(bsd_home_name) in teams


def test_attack_defence_values_preserved_after_key_normalization():
    """A normalização só muda a CHAVE — os valores attack/defence continuam
    a vir do mesmo fit, sem alteração."""
    rows = _round_robin("P1", ["Sporting CP", "Benfica"])
    df = _synthetic_matches(rows)

    ratings = train_all(df, min_games=1)
    teams = ratings["Primeira Liga"]["teams"]

    assert set(teams.keys()) == {"sporting cp", "benfica"}
    for t in teams.values():
        assert isinstance(t["attack"], float)
        assert isinstance(t["defence"], float)


def test_collision_between_two_raw_names_logs_warning_and_keeps_one(caplog):
    """Duas grafias diferentes no CSV que colapsam na mesma chave normalizada
    (ex.: 'Wolves' e 'Wolverhampton Wanderers', via _TEAM_ABBREVS) não podem
    desaparecer em silêncio — fica um aviso nos logs, e uma das duas
    sobrevive (não crasha, não perde dados de forma inexplicável)."""
    rows = _round_robin("E0", ["Wolves", "Wolverhampton Wanderers", "Arsenal"])
    df = _synthetic_matches(rows)

    with caplog.at_level(logging.WARNING):
        ratings = train_all(df, min_games=1)

    teams = ratings["Premier League"]["teams"]
    assert "wolverhampton wanderers" in teams
    assert len([k for k in teams if "wolv" in k]) == 1  # as duas colapsaram numa só
    assert any("colisão" in rec.message for rec in caplog.records)


def test_no_collision_when_all_names_normalize_uniquely():
    rows = _round_robin("N1", ["Ajax", "Feyenoord", "PSV"])
    df = _synthetic_matches(rows)

    ratings = train_all(df, min_games=1)
    teams = ratings["Eredivisie"]["teams"]
    assert len(teams) == 3  # nenhuma colisão


def test_league_still_maps_correctly_alongside_key_normalization():
    """A normalização das chaves de equipa não interfere com o mapeamento
    Div -> liga canónica (_DIV_TO_LEAGUE), que é uma preocupação separada."""
    rows = _round_robin("SP1", ["Barcelona", "Real Madrid"])
    df = _synthetic_matches(rows)

    ratings = train_all(df, min_games=1)
    assert "La Liga" in ratings
    assert set(ratings["La Liga"]["teams"].keys()) == {"barcelona", "real madrid"}
