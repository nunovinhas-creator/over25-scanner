"""
tests/test_scanner.py
---------------------
Smoke tests para pipeline/scan_over25.py e pipeline/scan_sharp1x2.py.

Usa mocks da Odds API e Telegram para não depender de secrets nem rede.
Verifica: gate logic, deduplicação, escrita em picks.json / rejected.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── helpers ────────────────────────────────────────────────────────────────────

def _future_iso(hours: float = 3.0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_odds_event(
    event_id: str,
    home: str,
    away: str,
    sport_key: str = "soccer_epl",
    hours_to_ko: float = 3.0,
    odds_over: float = 1.90,
    odds_under: float = 2.00,
) -> dict:
    """Evento simulado no formato da Odds API (mercado totals)."""
    return {
        "id": event_id,
        "sport_key": sport_key,
        "home_team": home,
        "away_team": away,
        "commence_time": _future_iso(hours_to_ko),
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over",  "point": 2.5, "price": odds_over},
                            {"name": "Under", "point": 2.5, "price": odds_under},
                        ],
                    }
                ],
            }
        ],
    }


def _make_1x2_event(
    event_id: str,
    home: str,
    away: str,
    sport_key: str = "soccer_epl",
    hours_to_ko: float = 3.0,
    pinn: tuple[float, float, float] = (1.85, 3.50, 4.20),
    b365: tuple[float, float, float] = (1.85 * 1.05, 3.50 * 1.04, 4.20 * 1.05),
) -> dict:
    """Evento simulado no formato da Odds API (mercado h2h)."""
    def _outcomes(h, d, a):
        return [
            {"name": "Home", "price": h},
            {"name": "Draw", "price": d},
            {"name": "Away", "price": a},
        ]

    return {
        "id": event_id,
        "sport_key": sport_key,
        "home_team": home,
        "away_team": away,
        "commence_time": _future_iso(hours_to_ko),
        "bookmakers": [
            {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": _outcomes(*pinn)}]},
            {"key": "bet365",   "markets": [{"key": "h2h", "outcomes": _outcomes(*b365)}]},
        ],
    }


# ── Over 2.5 tests ─────────────────────────────────────────────────────────────

class TestScanOver25(unittest.TestCase):

    def _run_scan(self, api_events_per_key: dict, existing_picks: list | None = None):
        """
        Corre scan() com data dir temporária e mocks.
        api_events_per_key: {sport_key: [events]} — substituição de fetch_events.
        Devolve (picks_list, rejected_list, tg_calls).
        """
        import pipeline.scan_over25 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Pré-popula picks existentes se pedido
            picks_file = tmp_path / "picks.json"
            rejected_file = tmp_path / "rejected_picks.json"
            state_file = tmp_path / "scan_state_over25.json"
            if existing_picks:
                picks_file.write_text(json.dumps(existing_picks))

            def fake_fetch(sport_key):
                return api_events_per_key.get(sport_key, [])

            # EV alto garantido: prob pipeline retorna ev_final=0.10
            def fake_compute_prob(ev, dc_ratings, calibrator_fn):
                # Simula pipeline com EV positivo (10%)
                return {
                    "p_model_source": "dc",
                    "p_dc_raw": 0.60,
                    "p_model": 0.62,
                    "p_market": 0.55,
                    "p_market_source": "devig",
                    "p_final": 0.575,
                    "ev_final": 0.10,
                    "odds_band": "1.70–2.00",
                }

            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "SCAN_STATE_FILE", state_file),
                patch.object(mod, "ODDS_API_KEY", "fake_key"),
                patch.object(mod, "fetch_events", side_effect=fake_fetch),
                patch.object(mod, "compute_prob", side_effect=fake_compute_prob),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push"),
            ):
                mod.scan()

            picks = json.loads(picks_file.read_text()) if picks_file.exists() else []
            rejected = json.loads(rejected_file.read_text()) if rejected_file.exists() else []
            return picks, rejected, tg_calls

    def test_one_pass_two_rejected(self):
        """1 jogo passa todos os gates, 2 são rejeitados (liga e timing)."""
        events = {
            # Jogo válido — Premier League, 3h, boas odds
            "soccer_epl": [
                _make_odds_event("ev1", "Arsenal", "Chelsea", hours_to_ko=3.0, odds_over=1.90, odds_under=2.00),
            ],
            # Liga não na whitelist (MLS → não existe em LEAGUE_SPORT_KEYS, mas
            # testamos timing rejeitado via horas > 6)
            "soccer_germany_bundesliga": [
                _make_odds_event("ev2", "Bayern", "Dortmund", hours_to_ko=10.0, odds_over=1.80, odds_under=2.10),
            ],
            # Odds fora da banda (> MAX_ODDS=3.50)
            "soccer_spain_la_liga": [
                _make_odds_event("ev3", "Real Madrid", "Barca", hours_to_ko=2.0, odds_over=4.50, odds_under=1.20),
            ],
        }
        picks, rejected, tg = self._run_scan(events)

        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]["id"], "ev1")
        self.assertEqual(len(tg), 1)

        reject_reasons = {r["id"]: r["reject_reason"] for r in rejected}
        self.assertEqual(reject_reasons.get("ev2"), "timing_apos_6h")
        self.assertEqual(reject_reasons.get("ev3"), "odds_fora_banda")

    def test_dedup_no_double_alert(self):
        """Mesmo evento em duas corridas consecutivas → só 1 alerta."""
        event = _make_odds_event("ev_dedup", "Man City", "Liverpool", hours_to_ko=4.0)
        events = {"soccer_epl": [event]}

        # Primeira corrida
        picks1, _, tg1 = self._run_scan(events)
        self.assertEqual(len(tg1), 1)
        self.assertEqual(len(picks1), 1)

        # Segunda corrida com picks existentes
        picks2, _, tg2 = self._run_scan(events, existing_picks=picks1)
        self.assertEqual(len(tg2), 0, "Não deve haver segundo alerta para o mesmo evento")
        self.assertEqual(len(picks2), 1, "Não deve duplicar o pick")

    def test_update_alert_on_significant_odds_change(self):
        """Odds mudam > 3% → cria pick _update e envia alerta."""
        ev_original = _make_odds_event("ev_upd", "PSG", "Lyon", hours_to_ko=3.0, odds_over=1.90)
        existing = [{
            "id": "ev_upd",
            "casa": "PSG", "fora": "Lyon", "liga": "Ligue 1",
            "data": _future_iso(3.0),
            "odds_over": 1.90,  # odds anteriores
            "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": "2026-01-01T00:00:00Z",
        }]

        # Odds agora em 1.80 — mudança de 5.3% > 3%
        ev_updated = _make_odds_event("ev_upd", "PSG", "Lyon", hours_to_ko=3.0, odds_over=1.80)
        events = {"soccer_france_ligue_one": [ev_updated]}

        picks, _, tg = self._run_scan(events, existing_picks=existing)

        update_ids = [p["id"] for p in picks if "_update" in p["id"]]
        self.assertEqual(len(update_ids), 1, "Deve criar pick _update")
        self.assertTrue(any("ATUALIZAÇÃO" in t for t in tg), "Deve enviar alerta de atualização")

    def test_no_alert_without_odds_api_key(self):
        """Sem ODDS_API_KEY o scan sai silenciosamente sem alertas."""
        import pipeline.scan_over25 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", tmp_path / "picks.json"),
                patch.object(mod, "REJECTED_FILE", tmp_path / "rejected.json"),
                patch.object(mod, "SCAN_STATE_FILE", tmp_path / "state.json"),
                patch.object(mod, "ODDS_API_KEY", ""),
                patch.object(mod, "git_commit_push"),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    mod.scan()
                self.assertEqual(ctx.exception.code, 0)


# ── Sharp 1X2 tests ────────────────────────────────────────────────────────────

class TestScanSharp1x2(unittest.TestCase):

    def _run_scan(self, api_events_per_key: dict, existing_picks: list | None = None):
        import pipeline.scan_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            picks_file = tmp_path / "picks_1x2.json"
            rejected_file = tmp_path / "rejected_picks_1x2.json"
            if existing_picks:
                picks_file.write_text(json.dumps(existing_picks))

            def fake_fetch(sport_key):
                return api_events_per_key.get(sport_key, [])

            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "ODDS_API_KEY", "fake_key"),
                patch.object(mod, "fetch_1x2_events", side_effect=fake_fetch),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push"),
            ):
                mod.scan()

            picks = json.loads(picks_file.read_text()) if picks_file.exists() else []
            rejected = json.loads(rejected_file.read_text()) if rejected_file.exists() else []
            return picks, rejected, tg_calls

    def test_away_passes_gates(self):
        """AWAY com div>3% e timing<6h deve gerar alerta."""
        # B365 AWAY = 4.41, Pinnacle AWAY = 4.20 → div = 4.41/4.20 - 1 = 5%
        event = _make_1x2_event(
            "g1", "Arsenal", "Chelsea",
            sport_key="soccer_epl",
            hours_to_ko=3.0,
            pinn=(1.85, 3.50, 4.20),
            b365=(1.85, 3.50, 4.41),
        )
        picks, rejected, tg = self._run_scan({"soccer_epl": [event]})

        away_picks = [p for p in picks if p["outcome"] == "AWAY"]
        self.assertEqual(len(away_picks), 1)
        self.assertEqual(away_picks[0]["id"], "g1_away_sh")
        self.assertTrue(any("SHARP 1X2" in t for t in tg))

    def test_draw_blocked_non_n1(self):
        """DRAW numa liga que não é Eredivisie → draw_suspenso."""
        event = _make_1x2_event(
            "g2", "Real Madrid", "Barca",
            sport_key="soccer_spain_la_liga",
            hours_to_ko=3.0,
            pinn=(1.50, 4.00, 6.00),
            b365=(1.50, 4.30, 6.00),  # div DRAW = 7.5%
        )
        picks, rejected, tg = self._run_scan({"soccer_spain_la_liga": [event]})

        draw_rejected = [r for r in rejected if r["outcome"] == "DRAW"]
        self.assertTrue(len(draw_rejected) >= 1)
        self.assertEqual(draw_rejected[0]["gate_blocked_reason"], "draw_suspenso")

    def test_draw_n1_tracking(self):
        """DRAW Eredivisie com div≥3% → gate_blocked_reason = draw_observacao_n1 (gravado mas não alertado)."""
        event = _make_1x2_event(
            "g3", "Ajax", "PSV",
            sport_key="soccer_netherlands_eredivisie",
            hours_to_ko=3.0,
            pinn=(2.10, 3.30, 3.60),
            b365=(2.10, 3.50, 3.60),  # div DRAW = 3.30/3.30... ~ 6%
        )
        picks, rejected, tg = self._run_scan({"soccer_netherlands_eredivisie": [event]})

        draw_rej = [r for r in rejected if r["outcome"] == "DRAW"]
        self.assertTrue(len(draw_rej) >= 1)
        self.assertEqual(draw_rej[0]["gate_blocked_reason"], "draw_observacao_n1")

    def test_home_n1_blocked(self):
        """HOME Eredivisie → n1_home_negativo."""
        event = _make_1x2_event(
            "g4", "Ajax", "PSV",
            sport_key="soccer_netherlands_eredivisie",
            hours_to_ko=3.0,
            pinn=(1.85, 3.50, 4.20),
            b365=(1.85 * 1.05, 3.50, 4.20),  # div HOME = 5%
        )
        picks, rejected, tg = self._run_scan({"soccer_netherlands_eredivisie": [event]})

        home_rej = [r for r in rejected if r["outcome"] == "HOME"]
        self.assertTrue(len(home_rej) >= 1)
        self.assertEqual(home_rej[0]["gate_blocked_reason"], "n1_home_negativo")

    def test_timing_gate(self):
        """Timing > 6h → timing_apos_6h."""
        event = _make_1x2_event(
            "g5", "Arsenal", "Chelsea",
            sport_key="soccer_epl",
            hours_to_ko=10.0,
            pinn=(1.85, 3.50, 4.20),
            b365=(1.85 * 1.05, 3.50, 4.20 * 1.05),
        )
        picks, rejected, tg = self._run_scan({"soccer_epl": [event]})

        timing_rej = [r for r in rejected if r.get("gate_blocked_reason") == "timing_apos_6h"]
        self.assertTrue(len(timing_rej) >= 1)
        self.assertEqual(len(tg), 0)

    def test_dedup_sharp1x2(self):
        """Mesmo pick em duas corridas → só 1 alerta."""
        event = _make_1x2_event(
            "g6", "Man City", "Liverpool",
            sport_key="soccer_epl",
            hours_to_ko=4.0,
            pinn=(1.85, 3.50, 4.20),
            b365=(1.85, 3.50, 4.41),  # AWAY div=5%
        )

        picks1, _, tg1 = self._run_scan({"soccer_epl": [event]})
        away_picks = [p for p in picks1 if p["outcome"] == "AWAY"]
        self.assertTrue(len(away_picks) >= 1)

        picks2, _, tg2 = self._run_scan({"soccer_epl": [event]}, existing_picks=picks1)
        self.assertEqual(len(tg2), 0, "Sem alerta duplicado")


# ── Gate unit tests ────────────────────────────────────────────────────────────

class TestApplySharp1x2Gates(unittest.TestCase):
    """Testes unitários diretos em apply_sharp1x2_gates() — replica test_sharp1x2_gates.py."""

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


if __name__ == "__main__":
    unittest.main()
