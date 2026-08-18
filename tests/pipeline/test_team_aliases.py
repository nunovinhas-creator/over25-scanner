"""
tests/pipeline/test_team_aliases.py
--------------------------------------
Bloco P (parte 1) — DC_TEAM_ALIASES / resolve_dc_team_key() em
pipeline/transform.py. Tabela de aliases explícita BSD -> football-data.co.uk
para as 10 ligas da whitelist, revista e aprovada pelo autor antes de ser
aplicada (ver conversa do Bloco P) — 55 pares confirmados manualmente,
excluindo mudanças de divisão, equipas B/reserva, equipas sem histórico e
contaminação legacy.

Cobre em particular a verificação pedida antes de aplicar a tabela:
correspondência por (liga, nome exacto da BSD), NUNCA por prefixo ou
substring — "Vitória SC" (Guimarães) não pode nunca herdar o rating de, nem
ser confundido com, um eventual "Vitória FC" (Setúbal) só porque partilham
a palavra "Vitória".

Todos os dados aqui são sintéticos (# synthetic), excepto os testes que
validam a tabela contra o data/dc_ratings.json real do repositório.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.transform import DC_TEAM_ALIASES, normalize_team_names, resolve_dc_team_key

_ROOT = Path(__file__).resolve().parents[2]
_DC_RATINGS_PATH = _ROOT / "data" / "dc_ratings.json"
_CALIBRATOR_PATH = _ROOT / "data" / "calibrator.json"


# ── resolve_dc_team_key — comportamento geral ─────────────────────────────


def test_aliased_name_resolves_to_normalized_target():
    key = resolve_dc_team_key("Championship", "Blackburn Rovers")
    assert key == normalize_team_names("Blackburn")


def test_non_aliased_name_falls_back_to_generic_normalization():
    """Nome que não está na tabela: comportamento inalterado (normalize_team_names puro)."""
    key = resolve_dc_team_key("Championship", "Some Random Team FC")
    assert key == normalize_team_names("Some Random Team FC")


def test_league_absent_from_aliases_falls_back_to_generic_normalization():
    key = resolve_dc_team_key("MLS", "LA Galaxy")
    assert key == normalize_team_names("LA Galaxy")


def test_alias_is_scoped_by_league_not_global():
    """O mesmo nome cru não é aliasado numa liga onde não foi aprovado —
    a tabela nunca se aplica fora da liga em que foi verificada."""
    # "Blackburn Rovers" só tem alias aprovado em Championship.
    key_wrong_league = resolve_dc_team_key("La Liga", "Blackburn Rovers")
    key_right_league = resolve_dc_team_key("Championship", "Blackburn Rovers")
    assert key_wrong_league != key_right_league
    assert key_wrong_league == normalize_team_names("Blackburn Rovers")


# ── Vitória SC vs Vitória FC — verificação pedida antes de aplicar ────────


def test_vitoria_sc_alias_never_matches_vitoria_fc_by_prefix():
    """Confirma que a correspondência é exacta, não por prefixo/substring:
    'Vitória SC' (Guimarães, tem alias) e 'Vitória FC' (Setúbal, sem
    alias — nem está em dc_ratings.json) têm de resolver para chaves
    DIFERENTES. Se algum dia 'Vitória FC' aparecer na BSD, cai no
    normalize_team_names() genérico, nunca herda o rating de Guimarães."""
    key_guimaraes = resolve_dc_team_key("Primeira Liga", "Vitória SC")
    key_setubal = resolve_dc_team_key("Primeira Liga", "Vitória FC")

    assert key_guimaraes == normalize_team_names("Guimaraes")
    assert key_setubal == normalize_team_names("Vitória FC")  # sem alias — nunca "guimaraes"
    assert key_guimaraes != key_setubal


def test_vitoria_fc_would_not_match_dc_ratings_even_unaliased():
    """Setúbal não está em nenhuma liga de dc_ratings.json (não jogou
    Primeira Liga na janela de 2 épocas do histórico) — sem alias, 'Vitória
    FC' cai correctamente em market_only, nunca num rating errado."""
    dc = json.loads(_DC_RATINGS_PATH.read_text(encoding="utf-8"))
    key_setubal = resolve_dc_team_key("Primeira Liga", "Vitória FC")
    primeira_liga_teams = set(dc.get("Primeira Liga", {}).get("teams", {}).keys())
    assert key_setubal not in primeira_liga_teams


# ── Validação contra o dc_ratings.json real — apanha erros de digitação ──


def test_every_alias_target_exists_in_real_dc_ratings():
    """Cada par (liga, alvo) da tabela tem de bater com uma chave REAL em
    data/dc_ratings.json — protege contra um erro de digitação na tabela
    (o alvo nunca seria encontrado, o par ficaria morto em silêncio).

    Compara chaves normalizadas dos dois lados, tal como resolve_dc_team_key()
    faz em produção (normalize_team_names(alias_target)) — data/dc_ratings.json
    grava as suas chaves já normalizadas desde o Bloco O (train_all()), por
    isso comparar o alvo cru da tabela (Title Case) directamente contra o
    dict falha assim que um retrain corre (visto em produção: PR #172 media
    contra um ficheiro ainda por re-treinar; o retrain semanal seguinte
    normalizou as chaves e partiu esta comparação literal)."""
    dc = json.loads(_DC_RATINGS_PATH.read_text(encoding="utf-8"))
    erros = []
    for liga, pairs in DC_TEAM_ALIASES.items():
        dc_teams_normalizadas = {normalize_team_names(t) for t in dc.get(liga, {}).get("teams", {})}
        for bsd_name, dc_name in pairs.items():
            if normalize_team_names(dc_name) not in dc_teams_normalizadas:
                erros.append((liga, bsd_name, dc_name))
    assert erros == [], f"alvos de alias sem correspondência em dc_ratings.json: {erros}"


def test_all_ten_leagues_are_valid_whitelist_leagues():
    """DC_TEAM_ALIASES nunca introduz uma liga fora da whitelist de produção."""
    from pipeline.scan_common import WHITELIST
    assert set(DC_TEAM_ALIASES.keys()) <= WHITELIST


def test_alias_count_matches_reviewed_table():
    """55 pares confirmados pelo autor (ver Bloco P) — se este número mudar,
    a tabela mudou e precisa de nova revisão, não só de um teste actualizado
    às cegas."""
    total = sum(len(pairs) for pairs in DC_TEAM_ALIASES.values())
    assert total == 55


# ── amostra de pares, um por liga, sanity check ───────────────────────────


def test_sample_aliases_resolve_as_expected():
    cases = [
        ("Belgian Pro League", "Royale Union Saint-Gilloise", "St. Gilloise"),
        ("Championship", "Queens Park Rangers", "QPR"),
        ("Eredivisie", "Fortuna Sittard", "For Sittard"),
        ("La Liga", "Espanyol", "Espanol"),
        ("La Liga 2", "Málaga CF", "Malaga"),
        ("Primeira Liga", "Sporting CP", "Sp Lisbon"),
    ]
    for liga, bsd_name, dc_name in cases:
        assert resolve_dc_team_key(liga, bsd_name) == normalize_team_names(dc_name)


# ── fim-a-fim com dados reais — compute_prob() encontra o modelo agora ────


def _real_calibrator_fn():
    import numpy as np

    cal = json.loads(_CALIBRATOR_PATH.read_text(encoding="utf-8"))
    x = np.array(cal["x_thresholds"], dtype=np.float64)
    y = np.array(cal["y_thresholds"], dtype=np.float64)
    return lambda arr: np.clip(np.interp(np.asarray(arr, dtype=np.float64), x, y), 1e-6, 1 - 1e-6)


def _post_retrain_dc_ratings() -> dict:
    """data/dc_ratings.json real, com as chaves normalizadas.

    Nota histórica: quando este teste foi escrito (Bloco P), o Bloco O
    tinha corrigido models/train_dc.py para gravar chaves normalizadas,
    mas o retrain semanal (retrain_dc.yml, segundas) ainda não tinha
    corrido — o ficheiro em disco ainda tinha as chaves cruas do CSV
    (Title Case) e este helper simulava o estado pós-retrain para o
    teste poder correr sem esperar pelo cron. O retrain já correu desde
    então (dc_ratings.json em produção já tem chaves normalizadas) — a
    normalização aqui é agora um no-op idempotente, mantida para o
    teste continuar correcto mesmo que o ficheiro volte a ter chaves
    cruas nalgum cenário futuro (ex.: rollback)."""
    dc = json.loads(_DC_RATINGS_PATH.read_text(encoding="utf-8"))
    return {
        liga: {**info, "teams": {normalize_team_names(t): v for t, v in info["teams"].items()}}
        for liga, info in dc.items()
    }


def test_compute_prob_finds_dc_model_for_previously_unmatched_game():
    """Fim-a-fim: um candidato Championship com os dois nomes completos da
    BSD (Blackburn Rovers vs Cardiff City) — antes do Bloco P caía sempre
    em market_only, porque nem "Blackburn Rovers" nem "Cardiff City"
    batiam com as chaves cruas do CSV ("Blackburn"/"Cardiff"). Com
    DC_TEAM_ALIASES, as duas resolvem e o modelo passa a correr — testado
    contra o dc_ratings.json real (normalizado, ver _post_retrain_dc_ratings)."""
    import pipeline.scan_over25 as mod

    dc_ratings = _post_retrain_dc_ratings()
    calibrator_fn = _real_calibrator_fn()

    ev = {
        "casa": "Blackburn Rovers", "fora": "Cardiff City",
        "liga": "Championship", "odds_over": 1.85, "odds_under": 1.91,
    }

    result = mod.compute_prob(ev, dc_ratings, calibrator_fn)

    assert result is not None
    assert result["p_model_source"] == "dc"
    assert result["p_dc_raw"] is not None
