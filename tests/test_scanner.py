"""
tests/test_scanner.py
---------------------
Smoke tests para pipeline/scan_over25.py e pipeline/scan_sharp1x2.py.

Usa mocks do BSD Sports API e Telegram — sem rede, sem secrets.
Eventos no formato BSD (event_id, home, away, date, movement, ...).
Verifica: gates, deduplicação, TG alerts, escrita em picks / rejected.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
import tempfile


# ── helpers ────────────────────────────────────────────────────────────────────

def _future_iso(hours: float = 3.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bsd_event(
    event_id: str,
    home: str,
    away: str,
    league: str = "Premier League",
    hours_to_ko: float = 3.0,
    odds_over: float = 1.90,
    odds_under: float = 2.00,
    movement: str = "SHORTENING",
    pinnacle_home: float | None = None,
    pinnacle_draw: float | None = None,
    pinnacle_away: float | None = None,
    b365_home: float | None = None,
    b365_draw: float | None = None,
    b365_away: float | None = None,
) -> dict:
    """Evento no formato BSD Sports API."""
    ev: dict = {
        "event_id": event_id,
        "home": home,
        "away": away,
        "league": league,
        "date": _future_iso(hours_to_ko),
        "odds_over": odds_over,
        "odds_under": odds_under,
        "movement": movement,
    }
    if pinnacle_home is not None:
        ev.update({"pinnacle_home": pinnacle_home, "pinnacle_draw": pinnacle_draw, "pinnacle_away": pinnacle_away})
    if b365_home is not None:
        ev.update({"b365_home": b365_home, "b365_draw": b365_draw, "b365_away": b365_away})
    return ev


# ── Over 2.5 tests ─────────────────────────────────────────────────────────────

class TestScanOver25(unittest.TestCase):

    def _run_scan(self, bsd_events: list[dict], existing_picks: list | None = None,
                  existing_rejected: list | None = None, git_push_ok: bool = True):
        """
        Corre scan() com data dir temporária e mocks.
        bsd_events: lista de eventos BSD simulados.
        Devolve (picks_list, rejected_list, tg_calls).
        """
        import pipeline.scan_over25 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            picks_file = tmp_path / "picks.json"
            rejected_file = tmp_path / "rejected_picks.json"
            state_file = tmp_path / "scan_state_over25.json"
            if existing_picks:
                picks_file.write_text(json.dumps(existing_picks))
            if existing_rejected:
                rejected_file.write_text(json.dumps(existing_rejected))

            def fake_fetch():
                return bsd_events

            def fake_compute_prob(ev, dc_ratings, calibrator_fn):
                return {
                    "p_model_source": "dc", "p_dc_raw": 0.60,
                    "p_model": 0.62, "p_market": 0.55,
                    "p_market_source": "devig",
                    "p_final": 0.575, "ev_final": 0.10,
                    "odds_band": "1.70–2.00",
                }

            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "SCAN_STATE_FILE", state_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "_fetch_all_events", side_effect=fake_fetch),
                patch.object(mod, "_fetch_lineup_info", return_value={}),
                patch.object(mod, "compute_prob", side_effect=fake_compute_prob),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push", return_value=git_push_ok),
            ):
                mod.scan()

            picks = json.loads(picks_file.read_text()) if picks_file.exists() else []
            rejected = json.loads(rejected_file.read_text()) if rejected_file.exists() else []
            return picks, rejected, tg_calls

    def test_one_pass_two_rejected(self):
        """1 jogo passa todos os gates, 2 são rejeitados (timing e odds)."""
        events = [
            # Válido — Premier League, 3h, boas odds, SHORTENING
            _bsd_event("ev1", "Arsenal", "Chelsea", hours_to_ko=3.0, odds_over=1.90),
            # Timing > 6h → rejeitado
            _bsd_event("ev2", "Bayern", "Dortmund", league="Bundesliga", hours_to_ko=10.0),
            # Odds fora da banda (> 3.50)
            _bsd_event("ev3", "Real Madrid", "Barca", league="La Liga", hours_to_ko=2.0, odds_over=4.50),
        ]
        picks, rejected, tg = self._run_scan(events)

        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["id"], "ev1")
        self.assertEqual(len(tg), 1)

        reject_reasons = {r["id"]: r["reject_reason"] for r in rejected}
        self.assertEqual(reject_reasons.get("ev2"), "timing_apos_6h")
        self.assertEqual(reject_reasons.get("ev3"), "odds_fora_banda")

    def test_drifting_rejected(self):
        """DRIFTING (fornecido pelo BSD) → rejeitado sem alerta TG."""
        events = [
            _bsd_event("ev_drift", "Man City", "Liverpool", hours_to_ko=3.0, movement="DRIFTING"),
        ]
        picks, rejected, tg = self._run_scan(events)

        self.assertEqual(len(tg), 0)
        drift_rej = [r for r in rejected if r.get("reject_reason") == "odds_drifting"]
        self.assertEqual(len(drift_rej), 1)

    def test_liga_fora_whitelist(self):
        """Liga fora da whitelist → rejeitada."""
        events = [
            _bsd_event("ev_mls", "LA Galaxy", "Seattle", league="MLS", hours_to_ko=3.0),
        ]
        picks, rejected, tg = self._run_scan(events)

        self.assertEqual(len(tg), 0)
        self.assertEqual(len(picks), 0)
        self.assertTrue(any(r.get("reject_reason") == "liga_fora_whitelist" for r in rejected))

    def test_liga_irresolvel_nunca_string_vazia(self):
        """Liga irresolúvel (sem league_id mapeável nem league_name) grava
        'DESCONHECIDA' + reject_reason='liga_desconhecida' — nunca liga=''
        (ver .claude/rules/data.md)."""
        events = [
            _bsd_event("ev_unknown", "Time X", "Time Y", league="", hours_to_ko=3.0),
        ]
        picks, rejected, tg = self._run_scan(events)

        self.assertEqual(len(tg), 0)
        self.assertEqual(len(picks), 0)
        unknown_rej = [r for r in rejected if r.get("id") == "ev_unknown"]
        self.assertEqual(len(unknown_rej), 1)
        self.assertEqual(unknown_rej[0]["liga"], "DESCONHECIDA")
        self.assertNotEqual(unknown_rej[0]["liga"], "")
        self.assertEqual(unknown_rej[0]["reject_reason"], "liga_desconhecida")

    # ── Regressão: deduplicação de rejected_picks.json (auditoria 9 ago 2026) ──
    # Bug: rej_index era chaveado por id+scanned_at, que muda a cada ciclo de
    # 30 min — o mesmo evento ainda-rejeitado nunca substituía o registo
    # anterior. Fix: chave por id puro, igual a existing_picks/existing_btts.

    def test_rejected_same_event_repeated_scan_no_duplicate(self):
        """MESMO EVENTO + MESMA REJEIÇÃO em scans sucessivos → 1 registo, não 2."""
        ev = _bsd_event("ev_rej_dup", "Sevilla", "Betis", league="La Liga", hours_to_ko=10.0)

        _, rejected1, _ = self._run_scan([ev])
        self.assertEqual(len(rejected1), 1)
        self.assertEqual(rejected1[0]["reject_reason"], "timing_apos_6h")

        _, rejected2, _ = self._run_scan([ev], existing_rejected=rejected1)
        self.assertEqual(len(rejected2), 1, "não deve duplicar o mesmo evento rejeitado")
        self.assertEqual(rejected2[0]["id"], "ev_rej_dup")

    def test_rejected_legitimate_update_replaces_old_reason(self):
        """MESMO EVENTO + ACTUALIZAÇÃO LEGÍTIMA → o motivo mais recente
        substitui o anterior; não acumula os dois registos."""
        ev1 = _bsd_event("ev_rej_upd", "Milan", "Inter", league="Serie A", hours_to_ko=10.0)
        _, rejected1, _ = self._run_scan([ev1])
        self.assertEqual(rejected1[0]["reject_reason"], "timing_apos_6h")

        # Mesmo evento, agora dentro da janela de timing mas com odds fora da banda
        ev2 = _bsd_event("ev_rej_upd", "Milan", "Inter", league="Serie A",
                          hours_to_ko=3.0, odds_over=4.50)
        _, rejected2, _ = self._run_scan([ev2], existing_rejected=rejected1)
        self.assertEqual(len(rejected2), 1, "deve substituir, não duplicar")
        self.assertEqual(rejected2[0]["reject_reason"], "odds_fora_banda")

    def test_rejected_new_event_adds_separate_record(self):
        """NOVO EVENTO → gera registo adicional, sem tocar no anterior."""
        ev_a = _bsd_event("ev_rej_a", "Ajax", "PSV", league="Eredivisie", hours_to_ko=10.0)
        _, rejected1, _ = self._run_scan([ev_a])

        ev_b = _bsd_event("ev_rej_b", "Feyenoord", "AZ", league="Eredivisie", hours_to_ko=10.0)
        _, rejected2, _ = self._run_scan([ev_b], existing_rejected=rejected1)

        self.assertEqual(len(rejected2), 2)
        self.assertEqual({r["id"] for r in rejected2}, {"ev_rej_a", "ev_rej_b"})

    def test_rejected_dedup_survives_multiple_restarts(self):
        """RESTART DO WORKFLOW (simulado por chamadas sucessivas que só veem o
        output persistido da corrida anterior) → deduplicação mantém-se."""
        ev = _bsd_event("ev_rej_restart", "Villarreal", "Getafe", league="La Liga", hours_to_ko=10.0)
        rejected = None
        for _ in range(4):
            _, rejected, _ = self._run_scan([ev], existing_rejected=rejected)
        self.assertEqual(len(rejected), 1)

    def test_rejected_dedup_unaffected_by_persistence_failure(self):
        """FALHA DE PERSISTÊNCIA → não avança/corrompe o estado de dedup.

        Em produção (scanner.yml) cada execução faz `actions/checkout` de
        origin/main sem estado local entre corridas — por isso uma falha de
        git_commit_push() já não pode, por construção, fazer o próximo scan
        herdar duplicados (o próximo scan volta a partir do último estado
        realmente publicado). Este teste cobre o cenário pior dentro de um
        único processo: mesmo com o push a falhar, o ficheiro local nunca é
        escrito com registos duplicados.
        """
        ev = _bsd_event("ev_rej_pushfail", "Napoli", "Roma", league="Serie A", hours_to_ko=10.0)
        _, rejected1, _ = self._run_scan([ev], git_push_ok=False)
        self.assertEqual(len(rejected1), 1)

        _, rejected2, _ = self._run_scan([ev], existing_rejected=rejected1, git_push_ok=False)
        self.assertEqual(len(rejected2), 1, "falha de push não deve introduzir duplicados")

    def test_dedup_no_double_alert(self):
        """Mesmo evento em duas corridas → só 1 alerta TG."""
        events = [_bsd_event("ev_dedup", "Man City", "Liverpool", hours_to_ko=4.0)]

        picks1, _, tg1 = self._run_scan(events)
        self.assertEqual(len(tg1), 1)

        picks2, _, tg2 = self._run_scan(events, existing_picks=picks1)
        self.assertEqual(len(tg2), 0, "Não deve haver segundo alerta")
        self.assertEqual(len(picks2), 1, "Não deve duplicar o pick")

    def test_update_alert_on_significant_odds_change(self):
        """Odds mudam > 3% → cria pick _update e envia alerta de atualização."""
        existing = [{
            "id": "ev_upd",
            "casa": "PSG", "fora": "Lyon", "liga": "Ligue 1",
            "data": _future_iso(3.0), "odds_over": 1.90,
            "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": "2026-01-01T00:00:00Z",
        }]
        # Odds agora 1.80 — queda de 5.3% > 3%
        events = [_bsd_event("ev_upd", "PSG", "Lyon", league="Ligue 1", hours_to_ko=3.0, odds_over=1.80)]

        picks, _, tg = self._run_scan(events, existing_picks=existing)

        update_ids = [p["id"] for p in picks if "_update" in p["id"]]
        self.assertEqual(len(update_ids), 1)
        self.assertTrue(any("ATUALIZAÇÃO" in t for t in tg))

    def test_pre_ko_scoreline_fields_set(self):
        """Todo pick novo (pré-KO, timing_h>=0 garantido pelo Gate 1) grava
        score_no_alerta='0-0', minuto_no_alerta=None, origem_alerta='pre-ko'
        (data-quality-fixes, Ponto 3)."""
        events = [_bsd_event("ev_scoreline", "Arsenal", "Chelsea", hours_to_ko=3.0)]
        picks, _, _ = self._run_scan(events)

        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["score_no_alerta"], "0-0")
        self.assertIsNone(picks[0]["minuto_no_alerta"])
        self.assertEqual(picks[0]["origem_alerta"], "pre-ko")

    def test_no_alert_without_bsd_key(self):
        """Sem BSD_API_KEY o scan termina com sys.exit(0)."""
        import pipeline.scan_over25 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", tmp_path / "picks.json"),
                patch.object(mod, "REJECTED_FILE", tmp_path / "rejected.json"),
                patch.object(mod, "SCAN_STATE_FILE", tmp_path / "state.json"),
                patch.object(mod, "BSD_API_KEY", ""),
                patch.object(mod, "git_commit_push"),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    mod.scan()
                self.assertEqual(ctx.exception.code, 0)


# ── Sharp 1X2 tests ────────────────────────────────────────────────────────────

class TestScanSharp1x2(unittest.TestCase):

    def _run_scan(self, bsd_events: list[dict], existing_picks: list | None = None,
                  existing_rejected: list | None = None, git_push_ok: bool = True):
        import pipeline.scan_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            picks_file = tmp_path / "picks_1x2.json"
            rejected_file = tmp_path / "rejected_picks_1x2.json"
            if existing_picks:
                picks_file.write_text(json.dumps(existing_picks))
            if existing_rejected:
                rejected_file.write_text(json.dumps(existing_rejected))

            def fake_fetch():
                return bsd_events

            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "_fetch_all_events", side_effect=fake_fetch),
                patch.object(mod, "_fetch_lineup_info", return_value={}),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push", return_value=git_push_ok),
            ):
                mod.scan()

            picks = json.loads(picks_file.read_text()) if picks_file.exists() else []
            rejected = json.loads(rejected_file.read_text()) if rejected_file.exists() else []
            return picks, rejected, tg_calls

    def _ev_with_1x2(self, event_id, league="Premier League", hours_to_ko=3.0,
                     pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41)):
        return _bsd_event(
            event_id, "Arsenal", "Chelsea", league=league, hours_to_ko=hours_to_ko,
            pinnacle_home=pinn[0], pinnacle_draw=pinn[1], pinnacle_away=pinn[2],
            b365_home=b365[0], b365_draw=b365[1], b365_away=b365[2],
        )

    def test_away_passes_gates(self):
        """AWAY com div>3% e timing<6h → pick no 1º scan; TG na confirmação com shortening."""
        # B365 AWAY=4.41, Pin AWAY=4.20 → div=5%
        ev = self._ev_with_1x2("g1", pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        picks, rejected, tg = self._run_scan([ev])

        away_picks = [p for p in picks if p["outcome"] == "AWAY"]
        self.assertEqual(len(away_picks), 1)
        self.assertEqual(away_picks[0]["id"], "g1_away_sh")
        # 1º scan guarda o sinal e aguarda confirmação — sem TG ainda
        self.assertFalse(any("SHARP 1X2" in t for t in tg))

        # 2º scan: Pinnacle encurta 4.20 → 4.10 (confirmação) → alerta TG
        ev2 = self._ev_with_1x2("g1", pinn=(1.85, 3.50, 4.10), b365=(1.85, 3.50, 4.41))
        picks2, _, tg2 = self._run_scan([ev2], existing_picks=picks)
        self.assertTrue(any("SHARP 1X2" in t for t in tg2))
        away2 = [p for p in picks2 if p["id"] == "g1_away_sh"][0]
        self.assertTrue(away2.get("alerted_at"))

        # 3º scan: shortening de novo mas já alertado → sem TG duplicado
        ev3 = self._ev_with_1x2("g1", pinn=(1.85, 3.50, 4.05), b365=(1.85, 3.50, 4.41))
        _, _, tg3 = self._run_scan([ev3], existing_picks=picks2)
        self.assertFalse(any("SHARP 1X2" in t for t in tg3))

    def test_draw_blocked_non_n1(self):
        """DRAW numa liga não-N1 → draw_suspenso."""
        ev = self._ev_with_1x2("g2", league="La Liga",
                                pinn=(1.50, 4.00, 6.00), b365=(1.50, 4.30, 6.00))
        picks, rejected, tg = self._run_scan([ev])

        draw_rej = [r for r in rejected if r["outcome"] == "DRAW"]
        self.assertTrue(len(draw_rej) >= 1)
        self.assertEqual(draw_rej[0]["gate_blocked_reason"], "draw_suspenso")

    def test_draw_n1_tracking(self):
        """DRAW Eredivisie com div≥3% → draw_observacao_n1 (gravado, não alertado)."""
        ev = self._ev_with_1x2("g3", league="Eredivisie",
                                pinn=(2.10, 3.30, 3.60), b365=(2.10, 3.50, 3.60))
        picks, rejected, tg = self._run_scan([ev])

        draw_rej = [r for r in rejected if r["outcome"] == "DRAW"]
        self.assertTrue(len(draw_rej) >= 1)
        self.assertEqual(draw_rej[0]["gate_blocked_reason"], "draw_observacao_n1")

    def test_home_n1_blocked(self):
        """HOME Eredivisie → n1_home_negativo."""
        ev = self._ev_with_1x2("g4", league="Eredivisie",
                                pinn=(1.85, 3.50, 4.20), b365=(1.95, 3.50, 4.20))
        picks, rejected, tg = self._run_scan([ev])

        home_rej = [r for r in rejected if r["outcome"] == "HOME"]
        self.assertTrue(len(home_rej) >= 1)
        self.assertEqual(home_rej[0]["gate_blocked_reason"], "n1_home_negativo")

    def test_timing_gate(self):
        """Timing > 6h → timing_apos_6h."""
        ev = self._ev_with_1x2("g5", hours_to_ko=10.0,
                                pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        picks, rejected, tg = self._run_scan([ev])

        timing_rej = [r for r in rejected if r.get("gate_blocked_reason") == "timing_apos_6h"]
        self.assertTrue(len(timing_rej) >= 1)
        self.assertEqual(len(tg), 0)

    def test_dedup_sharp1x2(self):
        """Mesmo pick em duas corridas → só 1 alerta."""
        ev = self._ev_with_1x2("g6", pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))

        picks1, _, tg1 = self._run_scan([ev])
        away1 = [p for p in picks1 if p["outcome"] == "AWAY"]
        self.assertTrue(len(away1) >= 1)

        picks2, _, tg2 = self._run_scan([ev], existing_picks=picks1)
        self.assertEqual(len(tg2), 0, "Sem alerta duplicado")

    # ── Regressão: deduplicação de rejected_picks_1x2.json (auditoria 9 ago 2026) ──
    # Mesmo bug/fix do Over 2.5: rej_index chaveado por id+saved_at → agora id puro.

    def test_rejected_same_event_repeated_scan_no_duplicate(self):
        """MESMO EVENTO + MESMA REJEIÇÃO em scans sucessivos → sem duplicar
        nenhum dos 3 outcomes (HOME/DRAW/AWAY) rejeitados."""
        ev = self._ev_with_1x2("g_rej_dup", league="La Liga",
                                pinn=(1.50, 4.00, 6.00), b365=(1.50, 4.30, 6.00))
        _, rejected1, _ = self._run_scan([ev])
        self.assertEqual(len(rejected1), 3)  # HOME div_baixa, DRAW draw_suspenso, AWAY div_baixa

        _, rejected2, _ = self._run_scan([ev], existing_rejected=rejected1)
        self.assertEqual(len(rejected2), 3, "não deve duplicar nenhum dos 3 outcomes")
        self.assertEqual({r["id"] for r in rejected1}, {r["id"] for r in rejected2})

    def test_rejected_legitimate_update_replaces_old_reason(self):
        """MESMO EVENTO + ACTUALIZAÇÃO LEGÍTIMA → o motivo mais recente
        substitui o anterior para o mesmo outcome, sem duplicar."""
        ev1 = self._ev_with_1x2("g_rej_upd", league="La Liga", hours_to_ko=10.0,
                                 pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.20))
        _, rejected1, _ = self._run_scan([ev1])
        away_rej1 = [r for r in rejected1 if r["id"] == "g_rej_upd_away_sh"]
        self.assertEqual(len(away_rej1), 1)
        self.assertEqual(away_rej1[0]["gate_blocked_reason"], "timing_apos_6h")

        # Mesmo evento, agora dentro da janela de timing mas div_baixa (AWAY sem movimento)
        ev2 = self._ev_with_1x2("g_rej_upd", league="La Liga", hours_to_ko=3.0,
                                 pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.20))
        _, rejected2, _ = self._run_scan([ev2], existing_rejected=rejected1)
        away_rej2 = [r for r in rejected2 if r["id"] == "g_rej_upd_away_sh"]
        self.assertEqual(len(away_rej2), 1, "deve substituir, não duplicar")
        self.assertEqual(away_rej2[0]["gate_blocked_reason"], "div_baixa")

    def test_rejected_new_event_adds_separate_record(self):
        """NOVO EVENTO → gera registo(s) adicional(is), sem tocar nos anteriores."""
        ev_a = self._ev_with_1x2("g_rej_a", hours_to_ko=10.0,
                                  pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        _, rejected1, _ = self._run_scan([ev_a])

        ev_b = self._ev_with_1x2("g_rej_b", hours_to_ko=10.0,
                                  pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        _, rejected_b_alone, _ = self._run_scan([ev_b])
        _, rejected2, _ = self._run_scan([ev_b], existing_rejected=rejected1)

        self.assertEqual(len(rejected2), len(rejected1) + len(rejected_b_alone))
        ids1 = {r["id"] for r in rejected1}
        ids2 = {r["id"] for r in rejected2}
        self.assertTrue(ids1.issubset(ids2))

    def test_rejected_dedup_survives_multiple_restarts(self):
        """RESTART DO WORKFLOW (simulado por chamadas sucessivas que só veem o
        output persistido da corrida anterior) → deduplicação mantém-se."""
        ev = self._ev_with_1x2("g_rej_restart", hours_to_ko=10.0,
                                pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        rejected = None
        for _ in range(4):
            _, rejected, _ = self._run_scan([ev], existing_rejected=rejected)
        ids = {r["id"] for r in rejected}
        self.assertEqual(len(rejected), len(ids))

    def test_rejected_dedup_unaffected_by_persistence_failure(self):
        """FALHA DE PERSISTÊNCIA → não avança/corrompe o estado de dedup (ver
        justificação equivalente em TestScanOver25 — mesma arquitectura de
        fresh-checkout por corrida em scanner.yml)."""
        ev = self._ev_with_1x2("g_rej_pushfail", league="La Liga",
                                pinn=(1.50, 4.00, 6.00), b365=(1.50, 4.30, 6.00))
        _, rejected1, _ = self._run_scan([ev], git_push_ok=False)
        self.assertEqual(len(rejected1), 3)

        _, rejected2, _ = self._run_scan([ev], existing_rejected=rejected1, git_push_ok=False)
        self.assertEqual(len(rejected2), 3, "falha de push não deve introduzir duplicados")

    def test_liga_irresolvel_nunca_string_vazia(self):
        """Liga irresolúvel no Sharp 1X2 grava 'DESCONHECIDA' + reject_reason
        'liga_desconhecida' — nunca liga='' (ver .claude/rules/data.md)."""
        ev = self._ev_with_1x2("g_unknown", league="", pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        picks, rejected, tg = self._run_scan([ev])

        self.assertEqual(len(tg), 0)
        self.assertEqual(len(picks), 0)
        unknown_rej = [r for r in rejected if r.get("id", "").startswith("g_unknown")]
        self.assertTrue(len(unknown_rej) > 0)
        for r in unknown_rej:
            self.assertEqual(r["liga"], "DESCONHECIDA")
            self.assertNotEqual(r["liga"], "")
            self.assertEqual(r["reject_reason"], "liga_desconhecida")

    def test_pre_ko_scoreline_fields_set(self):
        """Pick Sharp 1X2 grava score_no_alerta='0-0', minuto_no_alerta=None,
        origem_alerta='pre-ko' (data-quality-fixes, Ponto 3)."""
        ev = self._ev_with_1x2("g_scoreline", pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        picks, _, _ = self._run_scan([ev])

        away_picks = [p for p in picks if p["outcome"] == "AWAY"]
        self.assertEqual(len(away_picks), 1)
        self.assertEqual(away_picks[0]["score_no_alerta"], "0-0")
        self.assertIsNone(away_picks[0]["minuto_no_alerta"])
        self.assertEqual(away_picks[0]["origem_alerta"], "pre-ko")

    def test_no_1x2_odds_skipped(self):
        """Evento sem odds 1X2 (sem pinnacle_*) é ignorado silenciosamente."""
        ev = _bsd_event("g7", "Arsenal", "Chelsea", hours_to_ko=3.0)  # sem odds 1X2
        picks, rejected, tg = self._run_scan([ev])
        self.assertEqual(len(tg), 0)
        self.assertEqual(len(picks), 0)


# ── Gate unit tests ─────────────────────────────────────────────────────────────

class TestApplySharp1x2Gates(unittest.TestCase):
    """Testes unitários de apply_sharp1x2_gates() — porta Python do JS."""

    def setUp(self):
        from pipeline.scan_sharp1x2 import apply_sharp1x2_gates
        self.gates = apply_sharp1x2_gates

    def test_timing_30h_blocked(self):
        self.assertEqual(self.gates("HOME", "Premier League", 0.05, 30), "timing_apos_6h")

    def test_timing_3h_passes(self):
        self.assertEqual(self.gates("HOME", "Premier League", 0.05, 3), "")

    def test_away_passes(self):
        self.assertEqual(self.gates("AWAY", "Bundesliga", 0.04, 2), "")

    def test_div_baixa(self):
        self.assertEqual(self.gates("HOME", "Serie A", 0.02, 4), "div_baixa")

    def test_div_none(self):
        self.assertEqual(self.gates("HOME", "Bundesliga", None, 4), "div_baixa")

    def test_draw_suspenso(self):
        self.assertEqual(self.gates("DRAW", "La Liga", 0.05, 4), "draw_suspenso")

    def test_draw_n1_tracking(self):
        self.assertEqual(self.gates("DRAW", "Eredivisie", 0.05, 4), "draw_observacao_n1")

    def test_home_n1_blocked(self):
        self.assertEqual(self.gates("HOME", "Eredivisie", 0.05, 4), "n1_home_negativo")

    def test_liga_fora_whitelist(self):
        self.assertEqual(self.gates("HOME", "MLS", 0.05, 4), "liga_fora_whitelist")

    def test_timing_exactly_6h(self):
        self.assertEqual(self.gates("AWAY", "Ligue 1", 0.04, 6.0), "")

    # ── Regressão: ligas específicas que motivaram a correcção ───────────────
    def test_usl_league_one_blocked(self):
        """Oakland Roots SC (USL League One) deve ser bloqueado."""
        self.assertEqual(self.gates("AWAY", "USL League One", 0.09, 3), "liga_fora_whitelist")

    def test_usl_championship_blocked(self):
        self.assertEqual(self.gates("HOME", "USL Championship", 0.05, 2), "liga_fora_whitelist")

    def test_world_cup_blocked(self):
        """Ghana vs Panama (Mundial) deve ser bloqueado."""
        self.assertEqual(self.gates("HOME", "FIFA World Cup", 0.05, 3), "liga_fora_whitelist")

    def test_empty_liga_blocked(self):
        """Liga vazia (pré-fix BSD API) deve ser bloqueada com motivo explícito
        distinto de 'liga_fora_whitelist' (nunca string vazia — data.md)."""
        self.assertEqual(self.gates("AWAY", "", 0.05, 3), "liga_desconhecida")

    def test_unknown_league_sentinel_blocked(self):
        """Sentinela UNKNOWN_LEAGUE (liga irresolúvel na BSD) deve ser bloqueada
        com o mesmo motivo explícito 'liga_desconhecida'."""
        from pipeline.scan_common import UNKNOWN_LEAGUE
        self.assertEqual(self.gates("AWAY", UNKNOWN_LEAGUE, 0.05, 3), "liga_desconhecida")


class TestRegressionWhitelist(unittest.TestCase):
    """Regressão: Ghana vs Panama e Oakland Roots SC devem ser rejeitados pelo scanner."""

    def test_ghana_panama_over25_rejected(self):
        """Ghana vs Panama (liga vazia = sem whitelist match) → rejeitado no Over 2.5."""
        import pipeline.scan_over25 as mod
        import tempfile

        ev = _bsd_event("ghana1", "Ghana", "Panama", league="FIFA World Cup",
                        hours_to_ko=3.0, odds_over=2.20)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            picks_file = tmp_path / "picks.json"
            rejected_file = tmp_path / "rejected_picks.json"
            state_file = tmp_path / "scan_state_over25.json"

            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "SCAN_STATE_FILE", state_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "_fetch_all_events", return_value=[ev]),
                patch.object(mod, "compute_prob", return_value={"p_final": 0.60, "p_market": 0.50, "ev_final": 0.10, "p_model_source": "dc", "p_dc_raw": 0.60, "p_model": 0.60, "p_market_source": "devig"}),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push"),
            ):
                mod.scan()

            picks = json.loads(picks_file.read_text()) if picks_file.exists() else []
            rejected = json.loads(rejected_file.read_text()) if rejected_file.exists() else []

        self.assertEqual(len(tg_calls), 0, "Não deve enviar TG para jogo fora whitelist")
        self.assertEqual(len(picks), 0, "Ghana vs Panama não deve estar em picks")
        world_cup_rejected = [r for r in rejected if r.get("reject_reason") == "liga_fora_whitelist"]
        self.assertGreater(len(world_cup_rejected), 0, "Ghana vs Panama deve estar em rejected")

    def test_oakland_roots_sharp1x2_rejected(self):
        """Oakland Roots SC (USL League One) → rejeitado no Sharp 1X2, sem TG."""
        import pipeline.scan_sharp1x2 as mod
        import tempfile

        ev = _bsd_event("oak1", "Oakland Roots SC", "Birmingham Legion",
                        league="USL League One", hours_to_ko=3.0,
                        pinnacle_home=2.10, pinnacle_draw=3.40, pinnacle_away=3.60,
                        b365_home=2.30, b365_draw=3.50, b365_away=3.80)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            picks_file = tmp_path / "picks_1x2.json"
            rejected_file = tmp_path / "rejected_picks_1x2.json"
            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "_fetch_all_events", return_value=[ev]),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push"),
            ):
                mod.scan()

            picks = json.loads(picks_file.read_text()) if picks_file.exists() else []
            rejected = json.loads(rejected_file.read_text()) if rejected_file.exists() else []

        self.assertEqual(len(tg_calls), 0, "Não deve enviar TG para Oakland Roots SC")
        usl_picks = [p for p in picks if "Oakland" in (p.get("casa") or "")]
        self.assertEqual(len(usl_picks), 0, "Oakland Roots SC não deve estar em picks")
        usl_rejected = [r for r in rejected if r.get("gate_blocked_reason") == "liga_fora_whitelist"]
        self.assertGreater(len(usl_rejected), 0, "Oakland Roots SC deve estar em rejected")


# ── Regressão Bloco D: paginação de /api/v2/events/ no Sharp 1X2 ───────────────
# scan_sharp1x2._fetch_all_events() lia só a 1ª página de eventos (cursor `next`
# descartado) enquanto já paginava /odds/ correctamente — testes acima mockam
# _fetch_all_events() directamente, por isso nunca exercitavam esta lógica.
# Este teste mocka requests.get() para confirmar que um `next` não-vazio
# resulta em mais do que uma request a /api/v2/events/.

class TestFetchAllEventsPagination(unittest.TestCase):

    def _fake_bsd_get(self, requested_urls, page1_events, page2_events):
        def fake_get(url, headers=None, timeout=None):
            requested_urls.append(url)
            resp = Mock()
            resp.raise_for_status = lambda: None
            if "/api/v2/odds/" in url:
                resp.json = lambda: {"results": [], "next": None}
            elif "cursor=page2" in url:
                resp.json = lambda: {"results": page2_events, "next": None}
            else:
                resp.json = lambda: {
                    "results": page1_events,
                    "next": "https://sports.bzzoiro.com/api/v2/events/?cursor=page2",
                }
            return resp
        return fake_get

    def test_events_pagination_follows_next_cursor(self):
        """Uma resposta com `next` preenchido tem de gerar uma 2ª request a
        /api/v2/events/ — e os eventos da 2ª página têm de chegar ao resultado."""
        import pipeline.scan_sharp1x2 as mod

        page1_events = [{"id": "1", "home_team": "Arsenal", "away_team": "Chelsea",
                          "league_id": 1, "event_date": _future_iso(3.0)}]
        page2_events = [{"id": "2", "home_team": "Milan", "away_team": "Inter",
                          "league_id": 4, "event_date": _future_iso(3.0)}]
        requested_urls: list[str] = []

        with (
            patch.object(mod, "BSD_API_KEY", "fake_key"),
            patch("pipeline.scan_sharp1x2.requests.get",
                  side_effect=self._fake_bsd_get(requested_urls, page1_events, page2_events)),
        ):
            result = mod._fetch_all_events()

        events_urls = [u for u in requested_urls if "/api/v2/events/" in u]
        self.assertEqual(len(events_urls), 2,
                          "next preenchido na 1ª página deve gerar uma 2ª request a /events/")
        self.assertEqual({e["event_id"] for e in result}, {"1", "2"},
                          "eventos da 2ª página têm de chegar ao resultado final")

    def test_events_single_page_no_extra_request(self):
        """Sem `next` (ou `next=None`) não deve haver requests extra a /events/."""
        import pipeline.scan_sharp1x2 as mod

        page1_events = [{"id": "1", "home_team": "Arsenal", "away_team": "Chelsea",
                          "league_id": 1, "event_date": _future_iso(3.0)}]
        requested_urls: list[str] = []

        def fake_get(url, headers=None, timeout=None):
            requested_urls.append(url)
            resp = Mock()
            resp.raise_for_status = lambda: None
            if "/api/v2/odds/" in url:
                resp.json = lambda: {"results": [], "next": None}
            else:
                resp.json = lambda: {"results": page1_events, "next": None}
            return resp

        with (
            patch.object(mod, "BSD_API_KEY", "fake_key"),
            patch("pipeline.scan_sharp1x2.requests.get", side_effect=fake_get),
        ):
            result = mod._fetch_all_events()

        events_urls = [u for u in requested_urls if "/api/v2/events/" in u]
        self.assertEqual(len(events_urls), 1)
        self.assertEqual({e["event_id"] for e in result}, {"1"})


if __name__ == "__main__":
    unittest.main()
