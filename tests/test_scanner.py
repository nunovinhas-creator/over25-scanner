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
from unittest.mock import patch
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

    def _run_scan(self, bsd_events: list[dict], existing_picks: list | None = None):
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
                patch.object(mod, "compute_prob", side_effect=fake_compute_prob),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push"),
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

    def _run_scan(self, bsd_events: list[dict], existing_picks: list | None = None):
        import pipeline.scan_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            picks_file = tmp_path / "picks_1x2.json"
            rejected_file = tmp_path / "rejected_picks_1x2.json"
            if existing_picks:
                picks_file.write_text(json.dumps(existing_picks))

            def fake_fetch():
                return bsd_events

            tg_calls = []

            with (
                patch.object(mod, "DATA_DIR", tmp_path),
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "REJECTED_FILE", rejected_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "_fetch_all_events", side_effect=fake_fetch),
                patch.object(mod, "send_telegram", side_effect=lambda t: tg_calls.append(t)),
                patch.object(mod, "git_commit_push"),
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
        """AWAY com div>3% e timing<6h → alerta TG."""
        # B365 AWAY=4.41, Pin AWAY=4.20 → div=5%
        ev = self._ev_with_1x2("g1", pinn=(1.85, 3.50, 4.20), b365=(1.85, 3.50, 4.41))
        picks, rejected, tg = self._run_scan([ev])

        away_picks = [p for p in picks if p["outcome"] == "AWAY"]
        self.assertEqual(len(away_picks), 1)
        self.assertEqual(away_picks[0]["id"], "g1_away_sh")
        self.assertTrue(any("SHARP 1X2" in t for t in tg))

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


if __name__ == "__main__":
    unittest.main()
