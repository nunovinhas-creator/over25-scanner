"""
Testes de pipeline/ws_closing_odds.py — funções puras (sem rede).

Todos os payloads são sintéticos.  # synthetic
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pipeline.ws_closing_odds import (
    ClosingTracker,
    apply_closing,
    build_targets,
    extract_odds_rows,
)

NOW = datetime(2026, 7, 11, 20, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestBuildTargets(unittest.TestCase):
    def test_sharp_pick_in_window(self):
        picks = [{
            "id": "555_away_sh", "outcome": "AWAY", "odds_entrada": 4.41,
            "data": _iso(NOW + timedelta(minutes=20)), "odds_fecho": "",
        }]
        targets = build_targets(picks, [], NOW)
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertEqual(t["kind"], "sharp1x2")
        self.assertEqual(t["event_id"], "555")
        self.assertEqual(t["market"], "1x2")
        self.assertEqual(t["outcome"], "AWAY")
        self.assertEqual(t["odds_entry"], 4.41)

    def test_skips_filled_and_out_of_window(self):
        picks = [
            # já preenchido
            {"id": "1_home_sh", "outcome": "HOME", "odds_entrada": 2.0,
             "data": _iso(NOW + timedelta(minutes=10)), "odds_fecho": "1.95"},
            # KO longe demais
            {"id": "2_home_sh", "outcome": "HOME", "odds_entrada": 2.0,
             "data": _iso(NOW + timedelta(hours=3)), "odds_fecho": ""},
            # KO já passou além da graça
            {"id": "3_home_sh", "outcome": "HOME", "odds_entrada": 2.0,
             "data": _iso(NOW - timedelta(minutes=30)), "odds_fecho": ""},
        ]
        self.assertEqual(build_targets(picks, [], NOW), [])

    def test_over25_pick(self):
        picks = [{
            "id": "777", "odds_over": 1.85,
            "data": _iso(NOW + timedelta(minutes=5)), "odds_over_close": "",
        }]
        targets = build_targets([], picks, NOW)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["kind"], "over25")
        self.assertEqual(targets[0]["market"], "over_under_25")
        self.assertEqual(targets[0]["outcome"], "over")


class TestExtractOddsRows(unittest.TestCase):
    ROW = {"event_id": 555, "market": "1x2", "outcome": "AWAY",
           "decimal_odds": 4.30, "bookmaker_slug": "pinnacle"}

    def test_bare_row_and_list(self):
        self.assertEqual(extract_odds_rows(self.ROW), [self.ROW])
        self.assertEqual(extract_odds_rows([self.ROW, {"x": 1}]), [self.ROW])

    def test_envelope_variants(self):
        for key in ("data", "odds", "payload", "results"):
            frame = {"type": "odds", key: [dict(self.ROW)]}
            rows = extract_odds_rows(frame)
            self.assertEqual(len(rows), 1, key)
            self.assertEqual(rows[0]["decimal_odds"], 4.30)

    def test_envelope_injects_event_id_and_slug(self):
        row = {"market": "1x2", "outcome": "AWAY", "decimal_odds": 4.25}
        frame = {"type": "odds_book", "event_id": 555,
                 "bookmaker_slug": "pinnacle", "data": [row]}
        rows = extract_odds_rows(frame)
        self.assertEqual(rows[0]["event_id"], 555)
        self.assertEqual(rows[0]["bookmaker_slug"], "pinnacle")

    def test_ignores_other_frame_types(self):
        self.assertEqual(extract_odds_rows({"type": "event", "data": [self.ROW]}), [])
        self.assertEqual(extract_odds_rows({"type": "livedata", "ball": []}), [])
        self.assertEqual(extract_odds_rows("not json obj"), [])


class TestClosingTracker(unittest.TestCase):
    def _target(self, ko: datetime) -> dict:
        return {"kind": "sharp1x2", "pick_id": "555_away_sh", "event_id": "555",
                "ko": ko, "market": "1x2", "outcome": "AWAY", "odds_entry": 4.41}

    def test_last_pre_ko_wins_and_post_ko_ignored(self):
        ko = NOW + timedelta(minutes=10)
        t = self._target(ko)
        tracker = ClosingTracker([t])
        row = {"event_id": "555", "market": "1x2", "outcome": "AWAY",
               "bookmaker_slug": "pinnacle"}
        tracker.ingest([{**row, "decimal_odds": 4.30}], NOW)
        tracker.ingest([{**row, "decimal_odds": 4.20}], NOW + timedelta(minutes=9))
        # após o KO — in-play, não é closing
        tracker.ingest([{**row, "decimal_odds": 9.99}], NOW + timedelta(minutes=11))
        self.assertEqual(tracker.closing_for(t), 4.20)

    def test_pinnacle_beats_other_books(self):
        ko = NOW + timedelta(minutes=10)
        t = self._target(ko)
        tracker = ClosingTracker([t])
        base = {"event_id": "555", "market": "1x2", "outcome": "AWAY"}
        tracker.ingest([{**base, "decimal_odds": 4.10, "bookmaker_slug": "pinnacle"}], NOW)
        # bet365 depois — rank menor, não substitui
        tracker.ingest([{**base, "decimal_odds": 4.50, "bookmaker_slug": "bet365"}],
                       NOW + timedelta(minutes=1))
        self.assertEqual(tracker.closing_for(t), 4.10)

    def test_unrelated_rows_ignored(self):
        t = self._target(NOW + timedelta(minutes=10))
        tracker = ClosingTracker([t])
        tracker.ingest([{"event_id": "999", "market": "1x2", "outcome": "AWAY",
                         "decimal_odds": 2.0}], NOW)
        tracker.ingest([{"event_id": "555", "market": "over_under_25",
                         "outcome": "over", "decimal_odds": 1.9}], NOW)
        self.assertIsNone(tracker.closing_for(t))


class TestApplyClosing(unittest.TestCase):
    def test_writes_closing_and_clv(self):
        ko = NOW + timedelta(minutes=10)
        pick = {"id": "555_away_sh", "outcome": "AWAY", "odds_entrada": 4.41,
                "data": _iso(ko), "odds_fecho": "", "clv": ""}
        targets = build_targets([pick], [], NOW)
        tracker = ClosingTracker(targets)
        tracker.ingest([{"event_id": "555", "market": "1x2", "outcome": "AWAY",
                         "decimal_odds": 4.20, "bookmaker_slug": "pinnacle"}], NOW)
        n = apply_closing([pick], targets, tracker, "sharp1x2", "odds_fecho")
        self.assertEqual(n, 1)
        self.assertEqual(pick["odds_fecho"], 4.20)
        self.assertEqual(pick["clv"], 5.0)  # 4.41/4.20 − 1 = +5%
        self.assertEqual(pick["closing_source"], "ws")

    def test_no_capture_no_write(self):
        ko = NOW + timedelta(minutes=10)
        pick = {"id": "555_away_sh", "outcome": "AWAY", "odds_entrada": 4.41,
                "data": _iso(ko), "odds_fecho": ""}
        targets = build_targets([pick], [], NOW)
        tracker = ClosingTracker(targets)
        n = apply_closing([pick], targets, tracker, "sharp1x2", "odds_fecho")
        self.assertEqual(n, 0)
        self.assertEqual(pick["odds_fecho"], "")


if __name__ == "__main__":
    unittest.main()
