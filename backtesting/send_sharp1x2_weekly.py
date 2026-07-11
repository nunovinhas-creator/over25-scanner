#!/usr/bin/env python3
"""
backtesting/send_sharp1x2_weekly.py
------------------------------------
Weekly Telegram report for Sharp 1X2 signal performance.
Reads data/picks_1x2.json, computes KPIs, sends to Telegram.
Exits 0 even if Telegram fails (non-blocking in CI).
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pipeline.scan_common import send_telegram

PICKS_FILE = Path(__file__).resolve().parent.parent / "data" / "picks_1x2.json"


def _clv_of(p: dict) -> float | None:
    """CLV proxy: explicit clv field first, then div_b365_pin percentage."""
    for field in ("clv", "div_b365_pin"):
        raw = p.get(field, "")
        try:
            v = float(raw)
            if v != 0:
                return v
        except (ValueError, TypeError):
            pass
    return None


def _outcome_stats(picks: list[dict], outcome: str) -> dict:
    ps = [p for p in picks if (p.get("outcome") or "").upper() == outcome]
    settled = [p for p in ps if p.get("resultado_outcome") in ("WIN", "LOSS")]
    wins = [p for p in settled if p.get("resultado_outcome") == "WIN"]
    clv_vals = [v for p in ps if (v := _clv_of(p)) is not None]
    return {
        "n": len(ps),
        "settled": len(settled),
        "wr": len(wins) / len(settled) if settled else None,
        "clv": sum(clv_vals) / len(clv_vals) if clv_vals else None,
    }


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def compute_stats(picks: list[dict]) -> dict:
    today = datetime.now(timezone.utc)
    week_ago = today - timedelta(days=7)

    # alertados = picks que passaram todos os gates e não têm data quality flag
    alertados = [
        p for p in picks
        if not p.get("gate_blocked_reason")
        and not p.get("data_quality_flag")
    ]
    settled = [p for p in alertados if p.get("resultado_outcome") in ("WIN", "LOSS")]

    # CLV rolling-30: últimos 30 settled alertados por data
    settled_sorted = sorted(settled, key=lambda p: p.get("data", ""), reverse=True)
    rolling30 = settled_sorted[:30]
    clv_vals30 = [v for p in rolling30 if (v := _clv_of(p)) is not None]
    clv_mean30 = sum(clv_vals30) / len(clv_vals30) if clv_vals30 else None
    clv_se30: float | None = None
    if len(clv_vals30) > 1 and clv_mean30 is not None:
        var = sum((v - clv_mean30) ** 2 for v in clv_vals30) / (len(clv_vals30) - 1)
        clv_se30 = math.sqrt(var) / math.sqrt(len(clv_vals30))

    # DRAW N1 tracking
    draw_n1 = [p for p in picks if p.get("gate_blocked_reason") == "draw_observacao_n1"]
    draw_n1_settled = [p for p in draw_n1 if p.get("resultado_outcome") in ("WIN", "LOSS")]

    # Gate rejects this week
    all_this_week = [p for p in picks if (d := _parse_date(p.get("data", ""))) and d >= week_ago]
    alertados_this_week = [p for p in all_this_week if not p.get("gate_blocked_reason")]
    rejected_this_week = [p for p in all_this_week if p.get("gate_blocked_reason")]

    reject_reasons: dict[str, int] = {}
    for p in rejected_this_week:
        r = p.get("gate_blocked_reason", "unknown")
        reject_reasons[r] = reject_reasons.get(r, 0) + 1

    return {
        "total_alertados": len(alertados),
        "total_settled": len(settled),
        "alertados_this_week": len(alertados_this_week),
        "rejected_this_week": len(rejected_this_week),
        "reject_reasons": reject_reasons,
        "clv_mean30": clv_mean30,
        "clv_se30": clv_se30,
        "n_rolling30": len(clv_vals30),
        "home": _outcome_stats(alertados, "HOME"),
        "away": _outcome_stats(alertados, "AWAY"),
        "draw_n1_total": len(draw_n1),
        "draw_n1_settled": len(draw_n1_settled),
    }


def _fmt_pct(v: float | None, precision: int = 2) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{precision}f}%"


def _fmt_wr(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.0f}%"


def build_message(stats: dict) -> str:
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    clv_mean = stats["clv_mean30"]
    clv_se = stats["clv_se30"]
    n30 = stats["n_rolling30"]

    if clv_mean is not None:
        se_str = f" (±{clv_se:.2f}%)" if clv_se else ""
        clv_str = f"{_fmt_pct(clv_mean)}{se_str} [n={n30}]"
    else:
        clv_str = "— (sem dados)"

    home = stats["home"]
    away = stats["away"]

    reject = stats["reject_reasons"]
    total_rej = stats["rejected_this_week"]
    if reject:
        rej_detail = ", ".join(f"{k}: {v}" for k, v in sorted(reject.items(), key=lambda x: -x[1]))
        rej_str = f"{total_rej} ({rej_detail})"
    else:
        rej_str = "0"

    lines = [
        f"📊 Sharp 1X2 — semana {today}",
        f"Alertados: {stats['total_alertados']} | Settled: {stats['total_settled']} | CLV médio: {clv_str}",
        f"HOME: n={home['n']} WR={_fmt_wr(home['wr'])} CLV={_fmt_pct(home['clv'], 1)}",
        f"AWAY: n={away['n']} WR={_fmt_wr(away['wr'])} CLV={_fmt_pct(away['clv'], 1)}",
        f"Em observação — DRAW N1: {stats['draw_n1_settled']}/50 settled",
        f"Alertados esta semana: {stats['alertados_this_week']}",
        f"Gate rejeitados esta semana: {rej_str}",
    ]
    return "\n".join(lines)


def main() -> None:
    if not PICKS_FILE.exists():
        print(f"picks_1x2.json não encontrado: {PICKS_FILE} — skip", file=sys.stderr)
        sys.exit(0)

    try:
        picks = json.loads(PICKS_FILE.read_text(encoding="utf-8"))
        if not isinstance(picks, list):
            picks = []
    except Exception as exc:
        print(f"Erro ao ler picks: {exc} — skip", file=sys.stderr)
        sys.exit(0)

    stats = compute_stats(picks)
    msg = build_message(stats)
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
