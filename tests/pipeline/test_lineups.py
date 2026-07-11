"""
Testes de pipeline/extract.py — summarize_lineups e passagem de campos
informativos da BSD API (H2H, predictions ML) em scan_over25._event_fields.

Todos os payloads são sintéticos.  # synthetic
"""

from __future__ import annotations

import unittest

from pipeline.extract import summarize_lineups
from pipeline.scan_over25 import _event_fields


class TestSummarizeLineups(unittest.TestCase):
    def test_none_payload(self):
        """Fail-safe: payload None → campos vazios, counts None."""
        out = summarize_lineups(None)
        self.assertEqual(out["lineup_status"], "")
        self.assertIsNone(out["indisp_casa"])
        self.assertIsNone(out["indisp_fora"])
        self.assertEqual(out["indisp_casa_det"], "")

    def test_unavailable_state(self):
        """lineup_status=unavailable → sem contagens (BSD devolve null)."""
        out = summarize_lineups({
            "event_id": 1, "lineup_status": "unavailable", "beta": False,
            "lineups": None, "unavailable_players": None, "updated_at": None,
        })
        self.assertEqual(out["lineup_status"], "unavailable")
        self.assertIsNone(out["indisp_casa"])
        self.assertIsNone(out["indisp_fora"])

    def test_confirmed_with_unavailable(self):
        """XI confirmado com indisponíveis → contagens e detalhe por equipa."""
        payload = {
            "event_id": 1, "lineup_status": "confirmed", "beta": False,
            "lineups": {"home": {}, "away": {}},
            "unavailable_players": {
                "home": [
                    {"id": 1, "name": "João Silva", "short_name": "J. Silva",
                     "status": "injured", "reason": "Muscle Injury"},
                    {"id": 2, "name": "Pedro Costa", "short_name": "P. Costa",
                     "status": "suspended", "reason": "5 yellow cards"},
                ],
                "away": [],
            },
            "updated_at": "2026-07-11T18:00:00Z",
        }
        out = summarize_lineups(payload)
        self.assertEqual(out["lineup_status"], "confirmed")
        self.assertEqual(out["indisp_casa"], 2)
        self.assertEqual(out["indisp_fora"], 0)
        self.assertIn("J. Silva (injured)", out["indisp_casa_det"])
        self.assertIn("P. Costa (suspended)", out["indisp_casa_det"])
        self.assertEqual(out["indisp_fora_det"], "")

    def test_names_truncated(self):
        """Mais de 6 indisponíveis → detalhe truncado com sufixo +N."""
        players = [
            {"id": i, "name": f"Player {i}", "short_name": f"P{i}",
             "status": "injured", "reason": "x"}
            for i in range(9)
        ]
        out = summarize_lineups({
            "lineup_status": "predicted",
            "unavailable_players": {"home": players, "away": []},
        })
        self.assertEqual(out["indisp_casa"], 9)
        self.assertIn("+3", out["indisp_casa_det"])
        # 6 nomes + sufixo "+3"
        self.assertEqual(out["indisp_casa_det"].count(","), 6)

    def test_malformed_payload(self):
        """Payload malformado (tipos errados) não rebenta."""
        out = summarize_lineups({
            "lineup_status": "confirmed",
            "unavailable_players": {"home": "not-a-list", "away": [42, None]},
        })
        self.assertIsNone(out["indisp_casa"])
        # entradas não-dict são ignoradas mas a contagem reflecte a lista
        self.assertEqual(out["indisp_fora"], 2)
        self.assertEqual(out["indisp_fora_det"], "")


class TestEventFieldsPassthrough(unittest.TestCase):
    def test_new_informative_fields(self):
        """h2h_* e prob_*_ml fluem do evento normalizado para o dict de pick."""
        ev = {  # synthetic
            "event_id": "123", "home": "Porto", "away": "Braga",
            "league": "Primeira Liga", "date": "2026-07-11T20:00:00Z",
            "odds_over": 1.85, "odds_under": 2.05, "movement": "SHORTENING",
            "h2h_matches": 12, "h2h_avg_goals": 3.25,
            "prob_over25_ml": 0.61, "prob_btts_ml": 0.55,
        }
        fields = _event_fields(ev)
        self.assertEqual(fields["h2h_matches"], 12)
        self.assertEqual(fields["h2h_avg_goals"], 3.25)
        self.assertEqual(fields["prob_over25_ml"], 0.61)
        self.assertEqual(fields["prob_btts_ml"], 0.55)

    def test_fields_absent(self):
        """Eventos sem os campos novos → None (compatibilidade retro)."""
        fields = _event_fields({"event_id": "1", "home": "A", "away": "B"})
        self.assertIsNone(fields["h2h_avg_goals"])
        self.assertIsNone(fields["prob_over25_ml"])


if __name__ == "__main__":
    unittest.main()
