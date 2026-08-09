"""
tests/pipeline/test_settle_sharp1x2.py
-----------------------------------------
Testes para pipeline/settle_sharp1x2.py (Ponto 2, sessão data-quality-fixes).

Cobre a causa raiz corrigida: nada definia resultado_outcome para os picks
Sharp 1X2 do scanner de produção, por isso update_closing_odds.py nunca
tinha picks elegíveis para o fetch de odds_fecho. Usa mocks do BSD Sports
API — sem rede, sem secrets.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.settle_sharp1x2 import resolve_outcome, settle

_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _iso(hours_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(**kwargs) -> dict:
    base = {
        "id": "12345_home_sh",
        "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": _iso(3.0),
        "outcome": "HOME",
        "resultado_outcome": "",
        "odds_fecho": "",
    }
    return {**base, **kwargs}


class TestResolveOutcome:
    def test_home_win(self):
        assert resolve_outcome(2, 1) == "HOME"

    def test_away_win(self):
        assert resolve_outcome(0, 3) == "AWAY"

    def test_draw(self):
        assert resolve_outcome(1, 1) == "DRAW"

    def test_invalid_scores_return_none(self):
        assert resolve_outcome(None, None) is None
        assert resolve_outcome("?", "?") is None


class TestSettle:
    def _run(self, picks: list[dict], fetch_result_side_effect=None,
             git_commit_push_return=None):
        import pipeline.settle_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            picks_file = Path(tmp) / "picks_1x2.json"
            picks_file.write_text(json.dumps(picks), encoding="utf-8")

            commit_kwargs = {} if git_commit_push_return is None else {"return_value": git_commit_push_return}
            with (
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "fetch_event_result", side_effect=fetch_result_side_effect),
                patch.object(mod, "git_commit_push", **commit_kwargs),
            ):
                mod.settle()

            return json.loads(picks_file.read_text())

    def test_too_early_left_untouched(self):
        """Pick com KO há < 2.5h não é tocado — ainda a decorrer/cedo demais."""
        picks = [_pick(id="1_home_sh", data=_iso(1.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: pytest.fail("não devia chamar fetch"))
        assert out[0]["resultado_outcome"] == ""
        assert "settlement_error" not in out[0]

    def test_already_settled_pick_skipped(self):
        """Pick já settled (WIN/LOSS/VOID) nunca gera novo fetch."""
        picks = [_pick(id="2_home_sh", resultado_outcome="WIN", data=_iso(10.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: pytest.fail("não devia chamar fetch"))
        assert out[0]["resultado_outcome"] == "WIN"

    def test_home_win_resolves_to_win(self):
        """Pick outcome=HOME, resultado real HOME → WIN."""
        picks = [_pick(id="3_home_sh", outcome="HOME", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 2, "away_score": 0},
        )
        assert out[0]["resultado_outcome"] == "WIN"
        assert out[0]["resultado_jogo"] == "2-0"
        assert "settlement_error" not in out[0]

    def test_home_pick_loses_when_away_wins(self):
        """Pick outcome=HOME, resultado real AWAY → LOSS."""
        picks = [_pick(id="4_home_sh", outcome="HOME", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 0, "away_score": 1},
        )
        assert out[0]["resultado_outcome"] == "LOSS"

    def test_cancelled_event_marks_void(self):
        """Evento cancelado/adiado → VOID, nunca WIN/LOSS forçado."""
        picks = [_pick(id="5_home_sh", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "postponed", "home_score": None, "away_score": None},
        )
        assert out[0]["resultado_outcome"] == "VOID"

    def test_fetch_failure_within_window_stays_pending_no_error(self):
        """Falha de fetch dentro da janela (< 48h) — ainda tenta na próxima corrida,
        sem marcar erro definitivo."""
        picks = [_pick(id="6_home_sh", data=_iso(3.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: None)
        assert out[0]["resultado_outcome"] == ""
        assert "settlement_error" not in out[0]

    def test_fetch_failure_past_deadline_gets_explicit_error(self):
        """Falha de fetch além das 48h → settlement_error explícito com timestamp
        (nunca pendente silenciosamente para sempre)."""
        picks = [_pick(id="7_home_sh", data=_iso(50.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: None)
        assert out[0]["resultado_outcome"] == ""
        assert out[0]["settlement_error"] == "bsd_fetch_falhou_apos_48h"
        assert out[0]["settlement_error_at"]

    def test_not_finished_past_deadline_gets_explicit_error(self):
        """Evento sem estado finished mesmo 48h depois → erro explícito, não WIN/LOSS inventado."""
        picks = [_pick(id="8_home_sh", data=_iso(50.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "notstarted", "home_score": None, "away_score": None},
        )
        assert out[0]["resultado_outcome"] == ""
        assert "nao_finalizado_apos_48h" in out[0]["settlement_error"]

    def test_second_settlement_error_is_noop_no_new_commit(self):
        """Guarda (issue #127 / PR #128): um settlement_error já registado não
        é reescrito numa segunda corrida — evita commits [skip ci] repetidos
        quando um jogo fica preso sem resolução da BSD além das 48h."""
        import pipeline.settle_sharp1x2 as mod

        picks = [_pick(id="9_home_sh", data=_iso(50.0))]

        with tempfile.TemporaryDirectory() as tmp:
            picks_file = Path(tmp) / "picks_1x2.json"
            picks_file.write_text(json.dumps(picks), encoding="utf-8")

            commit_calls = []
            with (
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "fetch_event_result", side_effect=lambda eid: None),
                patch.object(mod, "git_commit_push", side_effect=lambda *a, **k: commit_calls.append(a)),
            ):
                mod.settle()
                first = json.loads(picks_file.read_text())[0]
                assert first["settlement_error"] == "bsd_fetch_falhou_apos_48h"
                assert len(commit_calls) == 1

                mod.settle()  # segunda corrida — mesmo pick, ainda sem resolução da BSD
                second = json.loads(picks_file.read_text())[0]
                assert second["settlement_error_at"] == first["settlement_error_at"]  # não reescrito
                assert len(commit_calls) == 1  # sem novo commit

    def test_settled_at_written_on_success(self):
        """Ao settlar um pick (WIN/LOSS), grava settled_at no mesmo formato de
        settlement_error_at — update_closing_odds.py vai ancorar a janela de
        closing odds a este campo, não ao KO (data)."""
        picks = [_pick(id="10_home_sh", outcome="HOME", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 2, "away_score": 0},
        )
        assert out[0]["resultado_outcome"] == "WIN"
        assert _ISO_TS_RE.match(out[0]["settled_at"])

    def test_settled_at_idempotent_not_overwritten(self):
        """settled_at nunca é reescrito se já existir — protege a janela de
        closing odds de reiniciar o relógio numa eventual segunda passagem
        pelo ramo de sucesso."""
        sentinel = "2026-01-01T00:00:00Z"
        picks = [_pick(id="11_home_sh", outcome="HOME", data=_iso(3.0), settled_at=sentinel)]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 2, "away_score": 0},
        )
        assert out[0]["resultado_outcome"] == "WIN"
        assert out[0]["settled_at"] == sentinel

    # ── Settlement Bug 1 (auditoria de continuidade, 9 ago 2026) ──────────
    # O servidor (este módulo) é a ÚNICA autoridade de settlement de Sharp
    # 1X2 — index.html deixou de escrever resultado_outcome/resultado_jogo
    # (ver tests/js/test_sharp1x2_settlement_authority.mjs para a prova do
    # lado browser). Estes testes cobrem os invariantes do lado servidor:
    # um settlement já decidido nunca é revertido/reprocessado, e uma falha
    # de push nunca deixa o ficheiro local com estado parcial.

    @pytest.mark.parametrize("outcome_val,resultado_jogo,has_settled_at", [
        ("WIN", "2-0", True),
        ("LOSS", "0-1", True),
        ("VOID", "", False),  # VOID nunca teve settled_at — comportamento
                               # pré-existente, não alterado por esta correcção
                               # (ver relatório: "outros riscos, não corrigidos").
    ])
    def test_already_settled_pick_never_reprocessed_across_multiple_runs(
        self, outcome_val, resultado_jogo, has_settled_at,
    ):
        """TESTE 1/2/3 (Settlement Bug 1): um pick já WIN/LOSS/VOID mantém-se
        exactamente assim ao longo de várias corridas de settle() — nunca
        volta para "" nem é reprocessado, mesmo repetindo a corrida."""
        base = _pick(
            id=f"30_{outcome_val.lower()}_sh", outcome="HOME",
            resultado_outcome=outcome_val, resultado_jogo=resultado_jogo,
            data=_iso(10.0),
        )
        if has_settled_at:
            base["settled_at"] = "2026-01-01T00:00:00Z"
        picks = [base]

        def _fail_fetch(eid):
            pytest.fail(f"pick já {outcome_val} não devia chamar fetch_event_result")

        out = self._run(picks, fetch_result_side_effect=_fail_fetch)
        assert out[0]["resultado_outcome"] == outcome_val
        if has_settled_at:
            assert out[0]["settled_at"] == "2026-01-01T00:00:00Z"

        # Segunda corrida (simula reprocessamento pelo servidor) — mesmo resultado.
        out2 = self._run(out, fetch_result_side_effect=_fail_fetch)
        assert out2[0]["resultado_outcome"] == outcome_val
        if has_settled_at:
            assert out2[0]["settled_at"] == "2026-01-01T00:00:00Z"

    def test_git_push_failure_still_persists_full_local_state(self):
        """TESTE 7 (Settlement Bug 1): falha de persistência (push) nunca
        deixa o ficheiro local com estado parcial — save_json_list() escreve
        sempre o batch completo desta corrida antes de git_commit_push() ser
        sequer chamado, por isso uma falha de push não corrompe nem trunca o
        resultado do settlement; só impede que chegue a origin/main (a
        corrida seguinte retoma de forma idempotente, ver teste anterior)."""
        picks = [
            _pick(id="31_home_sh", outcome="HOME", data=_iso(3.0)),
            _pick(id="32_away_sh", outcome="AWAY", data=_iso(3.0)),
        ]

        def fake_fetch(eid):
            return {"status": "finished", "home_score": 2, "away_score": 0}

        out = self._run(picks, fetch_result_side_effect=fake_fetch, git_commit_push_return=False)
        assert out[0]["resultado_outcome"] == "WIN"   # HOME, 2-0 → WIN
        assert out[1]["resultado_outcome"] == "LOSS"  # AWAY, 2-0 → LOSS
        assert out[0]["settled_at"]
        assert out[1]["settled_at"]

    # ── Settlement Bug 2 (auditoria de continuidade, 9 ago 2026) ───────────
    # "data" (KO) ausente/malformado escondia o pick para sempre: elapsed_h
    # nunca era calculado, por isso o ramo "past_deadline" (que gera
    # settlement_error nos outros casos) nunca era alcançado — o pick
    # desaparecia silenciosamente de todas as corridas futuras. Corrigido
    # para erro imediato e explícito ("data_ausente"/"data_invalida"), sem
    # depender de SETTLE_MAX_H (não há elapsed_h para comparar).

    def test_valid_data_settles_normally(self):
        """Controlo: "data" válido continua a settlar normalmente — a
        correcção não regride o caminho feliz."""
        picks = [_pick(id="40_home_sh", outcome="HOME", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 1, "away_score": 0},
        )
        assert out[0]["resultado_outcome"] == "WIN"
        assert "settlement_error" not in out[0]

    def test_missing_data_field_gets_explicit_error(self):
        """"data" ausente (chave nunca definida no pick) → settlement_error
        imediato, nunca fica pendente silencioso."""
        pick = _pick(id="41_home_sh", outcome="HOME")
        del pick["data"]

        def _fail_fetch(eid):
            pytest.fail("data ausente não devia chegar a fetch_event_result")

        out = self._run([pick], fetch_result_side_effect=_fail_fetch)
        assert out[0]["resultado_outcome"] == ""
        assert out[0]["settlement_error"] == "data_ausente"
        assert out[0]["settlement_error_at"]

    def test_empty_string_data_gets_explicit_error(self):
        """"data"="" → mesmo tratamento explícito que ausente."""
        picks = [_pick(id="42_home_sh", outcome="HOME", data="")]

        def _fail_fetch(eid):
            pytest.fail("data vazio não devia chegar a fetch_event_result")

        out = self._run(picks, fetch_result_side_effect=_fail_fetch)
        assert out[0]["resultado_outcome"] == ""
        assert out[0]["settlement_error"] == "data_ausente"

    def test_malformed_data_gets_explicit_error(self):
        """"data" ilegível (não-ISO) → settlement_error explícito — bug
        original: nunca atingia nenhum ramo de erro, ficava preso para sempre
        em silêncio."""
        picks = [_pick(id="43_home_sh", outcome="HOME", data="not-a-date")]

        def _fail_fetch(eid):
            pytest.fail("data malformado não devia chegar a fetch_event_result")

        out = self._run(picks, fetch_result_side_effect=_fail_fetch)
        assert out[0]["resultado_outcome"] == ""
        assert out[0]["settlement_error"] == "data_invalida"

    def test_impossible_calendar_date_treated_as_invalid(self):
        """Data com forma de ISO mas calendricamente impossível (30 de
        Fevereiro) — datetime.fromisoformat rejeita-a (ValueError), mesmo
        tratamento que "malformado". Comportamento explicitamente definido e
        testado, não deixado como caso não coberto."""
        picks = [_pick(id="44_home_sh", outcome="HOME", data="2026-02-30T00:00:00Z")]

        def _fail_fetch(eid):
            pytest.fail("data impossível não devia chegar a fetch_event_result")

        out = self._run(picks, fetch_result_side_effect=_fail_fetch)
        assert out[0]["resultado_outcome"] == ""
        assert out[0]["settlement_error"] == "data_invalida"

    def test_invalid_data_pick_does_not_block_other_picks(self):
        """Um pick com "data" inválido não impede os restantes de serem
        settled na mesma corrida — o erro de um pick nunca interrompe o
        processamento do lote."""
        picks = [
            _pick(id="45_home_sh", outcome="HOME", data="lixo"),
            _pick(id="46_home_sh", outcome="HOME", data=_iso(3.0)),
        ]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 2, "away_score": 0},
        )
        bad = next(p for p in out if p["id"] == "45_home_sh")
        good = next(p for p in out if p["id"] == "46_home_sh")
        assert bad["settlement_error"] == "data_invalida"
        assert good["resultado_outcome"] == "WIN"

    def test_data_error_persists_detectable_across_runs_without_new_commit(self):
        """O erro mantém-se detectável no ciclo seguinte — e não é
        reescrito/re-timestampado enquanto "data" continuar inválido (mesmo
        padrão anti-ruído já usado nos outros ramos de erro; evita commits
        [skip ci] repetidos para um pick preso)."""
        import pipeline.settle_sharp1x2 as mod

        picks = [_pick(id="47_home_sh", outcome="HOME", data="ainda-lixo")]

        def _fail_fetch(eid):
            pytest.fail("data inválido não devia chegar a fetch_event_result")

        with tempfile.TemporaryDirectory() as tmp:
            picks_file = Path(tmp) / "picks_1x2.json"
            picks_file.write_text(json.dumps(picks), encoding="utf-8")

            commit_calls = []
            with (
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "fetch_event_result", side_effect=_fail_fetch),
                patch.object(mod, "git_commit_push", side_effect=lambda *a, **k: commit_calls.append(a)),
            ):
                mod.settle()
                first = json.loads(picks_file.read_text())[0]
                assert first["settlement_error"] == "data_invalida"
                assert len(commit_calls) == 1

                mod.settle()  # segunda corrida — mesma "data" ainda inválida
                second = json.loads(picks_file.read_text())[0]
                assert second["settlement_error"] == "data_invalida"
                assert second["settlement_error_at"] == first["settlement_error_at"]  # não reescrito
                assert len(commit_calls) == 1  # sem novo commit

    def test_retry_after_fixing_data_settles_normally(self):
        """Corrigir "data" (ex.: edição manual) permite settlement normal na
        corrida seguinte — o settlement_error antigo não bloqueia o retry."""
        stale = _pick(
            id="48_home_sh", outcome="HOME", data="lixo-original",
            settlement_error="data_invalida", settlement_error_at="2026-01-01T00:00:00Z",
        )

        def _fail_fetch(eid):
            pytest.fail("data ainda inválido não devia chegar a fetch_event_result")

        out1 = self._run([stale], fetch_result_side_effect=_fail_fetch)
        assert out1[0]["settlement_error"] == "data_invalida"

        # "Edição manual" corrige a data para um KO real, 3h no passado.
        fixed = dict(out1[0])
        fixed["data"] = _iso(3.0)

        out2 = self._run(
            [fixed],
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 1, "away_score": 0},
        )
        assert out2[0]["resultado_outcome"] == "WIN"
        assert "settlement_error" not in out2[0]  # limpo pelo caminho de sucesso

    def test_settlement_error_not_erased_by_repeated_invalid_processing(self):
        """settlement_error não é silenciosamente apagado por reprocessamento
        inválido repetido — só o caminho de sucesso o limpa."""
        picks = [_pick(id="49_home_sh", outcome="HOME", data="lixo")]

        def _fail_fetch(eid):
            pytest.fail("data inválido não devia chegar a fetch_event_result")

        out = self._run(picks, fetch_result_side_effect=_fail_fetch)
        for _ in range(3):
            out = self._run(out, fetch_result_side_effect=_fail_fetch)
        assert out[0]["settlement_error"] == "data_invalida"
        assert out[0]["resultado_outcome"] == ""

    def test_already_settled_pick_with_garbage_data_stays_untouched(self):
        """Um pick já WIN/LOSS/VOID nunca chega sequer a tentar parsear
        "data" — idempotente mesmo com "data" corrompido (guarda de topo
        corre antes de qualquer parsing)."""
        picks = [_pick(id="50_home_sh", outcome="HOME", resultado_outcome="WIN",
                        data="isto-nao-e-uma-data")]

        def _fail_fetch(eid):
            pytest.fail("pick já settled não devia chamar fetch_event_result")

        out = self._run(picks, fetch_result_side_effect=_fail_fetch)
        assert out[0]["resultado_outcome"] == "WIN"
        assert "settlement_error" not in out[0]

    def test_no_bsd_api_key_aborts_cleanly(self):
        import pipeline.settle_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            picks_file = Path(tmp) / "picks_1x2.json"
            picks_file.write_text("[]", encoding="utf-8")
            with (
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "BSD_API_KEY", ""),
                patch.object(mod, "git_commit_push"),
            ):
                with pytest.raises(SystemExit) as ctx:
                    mod.settle()
                assert ctx.value.code == 0
