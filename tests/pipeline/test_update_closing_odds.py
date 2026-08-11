"""
tests/pipeline/test_update_closing_odds.py
---------------------------------------------
Testes para pipeline/update_closing_odds.py (Ponto 2, sessão data-quality-fixes).

Cobre o caminho de falha: uma falha de fetch (ou janela fechada sem nunca ter
conseguido odds_fecho) tem de ficar explícita no próprio pick
(fetch_error + fetch_error_at) — nunca só um print em stderr que se perde.

Cobre também a correcção da janela desalinhada (settle até 48h após KO,
close-fetch antigo só até 24h após KO): a elegibilidade para
fetch_closing_odds() usa settled_at (quando settle_sharp1x2.py definiu
resultado_outcome), nunca data/KO. Picks sem settled_at (backlog histórico
anterior a esta correcção) ficam não-elegíveis de forma silenciosa e
correcta — ausência de settlement registado não é um erro de fetch.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


def _iso(hours_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(**kwargs) -> dict:
    base = {
        "id": "999_home_sh",
        "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": _iso(3.0),
        "outcome": "HOME",
        "odds_entrada": "1.85",
        "resultado_outcome": "WIN",
        "odds_fecho": "",
        "clv": "",
        # settled_at é o campo que ancora a janela de closing odds (não a
        # data/KO — ver settle_sharp1x2.py). Default alinhado com "data"
        # só por convenção dos testes antigos; os dois já não têm de coincidir.
        "settled_at": _iso(3.0),
    }
    return {**base, **kwargs}


def _run(picks: list[dict], fetch_closing_odds_side_effect):
    import pipeline.update_closing_odds as mod

    with tempfile.TemporaryDirectory() as tmp:
        picks_file = Path(tmp) / "picks_1x2.json"
        picks_file.write_text(json.dumps(picks), encoding="utf-8")

        with (
            patch.object(mod, "PICKS_FILE", picks_file),
            patch.object(mod, "BSD_API_KEY", "fake_key"),
            patch.object(mod, "fetch_closing_odds", side_effect=fetch_closing_odds_side_effect),
            patch.object(mod, "git_commit_push"),
        ):
            mod.update()

        return json.loads(picks_file.read_text())


class TestUpdateClosingOdds:
    def test_unsettled_pick_untouched(self):
        """Pick sem resultado_outcome WIN/LOSS não é tocado (settlement é que
        decide isso — ver settle_sharp1x2.py)."""
        picks = [_pick(id="1_home_sh", resultado_outcome="", data=_iso(3.0))]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("não devia tentar fetch para pick não settled")))
        assert out[0]["odds_fecho"] == ""
        assert "fetch_error" not in out[0]

    def test_too_early_no_error_yet(self):
        """Settled há < 15min — ainda não é a altura, sem erro registado."""
        picks = [_pick(id="2_home_sh", settled_at=_iso(0.1))]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: 2.0)
        assert out[0]["odds_fecho"] == ""
        assert "fetch_error" not in out[0]

    def test_successful_fetch_sets_odds_and_clv(self):
        picks = [_pick(id="3_home_sh", odds_entrada="1.85", data=_iso(3.0))]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: 2.00)
        assert out[0]["odds_fecho"] == "2.0"
        assert float(out[0]["clv"]) < 0  # 1.85/2.00-1 = -7.5%
        assert "fetch_error" not in out[0]

    def test_fetch_failure_sets_explicit_error(self):
        """BSD sem odds pós-KO dentro da janela → fetch_error explícito no
        próprio pick, não só stderr."""
        picks = [_pick(id="4_home_sh", data=_iso(3.0))]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: None)
        assert out[0]["odds_fecho"] == ""
        assert out[0]["fetch_error"] == "bsd_sem_odds_pinnacle_pos_ko"
        assert out[0]["fetch_error_at"]

    def test_window_closed_marks_terminal_error_once(self):
        """Além de CLOSE_MAX_H desde settled_at, sem odds_fecho → erro terminal
        explícito, marcado uma única vez (não escreve de novo em corridas
        seguintes). KO (data) é irrelevante aqui — só settled_at conta."""
        picks = [_pick(id="5_home_sh", data=_iso(3.0), settled_at=_iso(15.0))]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("janela já fechada, não devia tentar fetch")))
        assert out[0]["fetch_error"] == "janela_fechada_sem_odds_fecho"
        assert out[0]["fetch_error_at"]

        # segunda corrida: erro já registado, não deve mudar nem tentar de novo
        first_error_at = out[0]["fetch_error_at"]
        out2 = _run(out, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("já marcado, não devia tentar de novo")))
        assert out2[0]["fetch_error_at"] == first_error_at

    def test_success_clears_previous_fetch_error(self):
        """Um fetch bem-sucedido limpa qualquer fetch_error anterior (transiente)."""
        picks = [_pick(id="6_home_sh", data=_iso(3.0), fetch_error="bsd_sem_odds_pinnacle_pos_ko",
                        fetch_error_at="2026-01-01T00:00:00Z")]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: 2.10)
        assert out[0]["odds_fecho"] == "2.1"
        assert "fetch_error" not in out[0]
        assert "fetch_error_at" not in out[0]

    def test_already_filled_odds_fecho_skipped(self):
        picks = [_pick(id="7_home_sh", odds_fecho="1.95", data=_iso(3.0))]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("não devia tentar — já preenchido")))
        assert out[0]["odds_fecho"] == "1.95"

    def test_eligibility_uses_settled_at_not_ko(self):
        """A elegibilidade tem de usar settled_at, NUNCA data/KO — correcção da
        janela desalinhada (settle até 48h pós-KO, close-fetch antigo só até
        24h pós-KO). Um KO muito antigo com settlement recente é elegível;
        um KO recente com settlement antigo (fora da janela) não é."""
        # KO há 10 dias (240h), mas settled_at há 3h → dentro da janela, elegível.
        picks_recent_settle = [_pick(id="8_home_sh", data=_iso(240.0), settled_at=_iso(3.0))]
        out = _run(picks_recent_settle, fetch_closing_odds_side_effect=lambda eid, out_: 2.00)
        assert out[0]["odds_fecho"] == "2.0"
        assert "fetch_error" not in out[0]

        # KO há 3h (recente), mas settled_at há 15h → fora da janela, mesmo
        # com KO recente. Nunca deve tentar fetch.
        picks_old_settle = [_pick(id="9_home_sh", data=_iso(3.0), settled_at=_iso(15.0))]
        out2 = _run(picks_old_settle, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("janela fechada por settled_at — não devia tentar fetch")))
        assert out2[0]["fetch_error"] == "janela_fechada_sem_odds_fecho"
        assert out2[0]["fetch_error_at"]

    def test_pick_without_settled_at_is_silently_not_eligible(self):
        """Backlog histórico (settled antes desta correcção, sem settled_at)
        fica não-elegível de forma silenciosa e correcta — NUNCA gera
        fetch_error. Ausência de settled_at não é uma falha de fetch, é
        ausência de settlement registado (345 picks reais neste estado)."""
        picks = [_pick(id="10_home_sh", data=_iso(3.0))]
        del picks[0]["settled_at"]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("sem settled_at — não devia tentar fetch")))
        assert out[0]["odds_fecho"] == ""
        assert "fetch_error" not in out[0]

    def test_pick_with_malformed_settled_at_is_silently_not_eligible(self):
        """settled_at ilegível (formato inválido) trata-se como ausente —
        nunca inventa uma elegibilidade a partir de dados corrompidos, e
        nunca gera fetch_error por isso (não é uma falha de fetch)."""
        picks = [_pick(id="11_home_sh", data=_iso(3.0), settled_at="não-é-uma-data")]
        out = _run(picks, fetch_closing_odds_side_effect=lambda eid, out_: (_ for _ in ()).throw(
            AssertionError("settled_at inválido — não devia tentar fetch")))
        assert out[0]["odds_fecho"] == ""
        assert "fetch_error" not in out[0]
