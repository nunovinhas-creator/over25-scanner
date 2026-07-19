"""
tests/data_quality/test_scoreline_fields.py
-----------------------------------------------
Testes para os 3 novos campos de scoreline no momento do alerta
(score_no_alerta, minuto_no_alerta, origem_alerta) — sessão
data-quality-fixes, Ponto 3.

Cobre: schema Over 2.5 (já existia, estendido), schemas novos e mínimos para
1X2 e BTTS, e retrocompatibilidade com picks antigos (sem estes campos).
"""

from __future__ import annotations

from data.schema.picks_schema import validate_picks
from data.schema.picks_1x2_schema import validate_picks_1x2
from data.schema.picks_btts_schema import validate_picks_btts


def _over25_pick(**over) -> dict:
    base = {
        "id": "1", "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": "2026-07-20T18:00:00+00:00", "score_sistema": "58",
        "prob_over25": "60", "odds_over": "1.90", "movimento": "SHORTENING",
        "sharp_label": "", "result_over25": "",
    }
    return {**base, **over}


def _1x2_pick(**over) -> dict:
    base = {
        "id": "2_home_sh", "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": "2026-07-20T18:00:00+00:00", "outcome": "HOME",
        "resultado_outcome": "",
    }
    return {**base, **over}


def _btts_pick(**over) -> dict:
    base = {
        "id": "3_btts", "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": "2026-07-20T18:00:00+00:00", "resultado_btts_over25": "",
    }
    return {**base, **over}


class TestOver25ScorelineFields:
    def test_legacy_pick_without_fields_does_not_crash(self):
        """Picks antigos sem score_no_alerta/minuto_no_alerta/origem_alerta
        continuam a validar — colunas ficam NaN, dashboard não parte."""
        df = validate_picks([_over25_pick()])
        assert len(df) == 1
        assert "score_no_alerta" in df.columns
        assert "origem_alerta" in df.columns
        assert df["score_no_alerta"].isna().all()

    def test_pre_ko_pick_validates_with_values(self):
        pick = _over25_pick(score_no_alerta="0-0", minuto_no_alerta="", origem_alerta="pre-ko")
        df = validate_picks([pick])
        assert len(df) == 1
        assert df.iloc[0]["score_no_alerta"] == "0-0"
        assert df.iloc[0]["origem_alerta"] == "pre-ko"

    def test_invalid_origem_alerta_row_dropped(self):
        """origem_alerta fora de {pre-ko, live, ''} falha a validação — a
        linha é descartada (lazy validation), nunca aceite silenciosamente."""
        pick = _over25_pick(origem_alerta="qualquer_coisa")
        df = validate_picks([pick])
        assert len(df) == 0


class TestSharp1x2ScorelineFields:
    def test_legacy_pick_without_fields_does_not_crash(self):
        df = validate_picks_1x2([_1x2_pick()])
        assert len(df) == 1
        assert "score_no_alerta" in df.columns
        assert df["minuto_no_alerta"].isna().all()

    def test_pre_ko_pick_validates_with_values(self):
        pick = _1x2_pick(score_no_alerta="0-0", origem_alerta="pre-ko")
        df = validate_picks_1x2([pick])
        assert len(df) == 1
        assert df.iloc[0]["score_no_alerta"] == "0-0"
        assert df.iloc[0]["origem_alerta"] == "pre-ko"

    def test_invalid_origem_alerta_row_dropped(self):
        pick = _1x2_pick(origem_alerta="invalido")
        df = validate_picks_1x2([pick])
        assert len(df) == 0

    def test_invalid_resultado_outcome_row_dropped(self):
        pick = _1x2_pick(resultado_outcome="DRAW")  # não é WIN/LOSS/VOID/''
        df = validate_picks_1x2([pick])
        assert len(df) == 0


class TestBttsScorelineFields:
    def test_legacy_pick_without_fields_does_not_crash(self):
        df = validate_picks_btts([_btts_pick()])
        assert len(df) == 1
        assert "origem_alerta" in df.columns
        assert df["origem_alerta"].isna().all()

    def test_pre_ko_pick_validates_with_values(self):
        pick = _btts_pick(score_no_alerta="0-0", origem_alerta="pre-ko")
        df = validate_picks_btts([pick])
        assert len(df) == 1
        assert df.iloc[0]["origem_alerta"] == "pre-ko"

    def test_invalid_origem_alerta_row_dropped(self):
        pick = _btts_pick(origem_alerta="???")
        df = validate_picks_btts([pick])
        assert len(df) == 0
