#!/usr/bin/env python3
"""
scripts/generate_readme.py
---------------------------
Regenera as secções dinâmicas do README.md com dados reais dos picks.

Lê data/picks.json, data/picks_1x2.json e data/picks_btts_over25.json,
calcula n picks válidos, n settled e CLV rolling-30 por módulo,
e substitui o bloco entre <!-- DYNAMIC_STATUS_START --> e <!-- DYNAMIC_STATUS_END -->.

Uso: python scripts/generate_readme.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
README_PATH = ROOT / "README.md"

MARKER_START = "<!-- DYNAMIC_STATUS_START -->"
MARKER_END = "<!-- DYNAMIC_STATUS_END -->"


def _load_picks(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _clv_rolling(picks: list[dict], window_days: int = 30, clv_field: str = "clv") -> float | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    values: list[float] = []
    for p in picks:
        if p.get("data_quality_flag"):
            continue
        raw_date = p.get("data") or p.get("commence_time") or p.get("timestamp", "")
        if not raw_date:
            continue
        try:
            dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt < cutoff:
            continue
        raw_clv = p.get(clv_field, "")
        try:
            val = float(raw_clv)
            values.append(val)
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _module_stats(picks: list[dict], clv_field: str = "clv") -> dict:
    valid = [p for p in picks if not p.get("data_quality_flag")]
    settled = [
        p for p in valid
        if p.get("resultado_outcome") in ("WIN", "LOSS")
        or p.get("resultado") in ("WIN", "LOSS")
        or p.get("result") in ("WIN", "LOSS")
    ]
    clv = _clv_rolling(settled, window_days=30, clv_field=clv_field)
    return {
        "n_valid": len(valid),
        "n_settled": len(settled),
        "clv_rolling": clv,
    }


def _format_clv(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _gate_status(clv: float | None, n_settled: int, gate_clv: float, gate_n: int) -> str:
    if clv is None or n_settled < gate_n:
        return "OBSERVAÇÃO"
    if clv > gate_clv:
        return "ACTIVADO ✅"
    return "OBSERVAÇÃO"


def build_status_block() -> str:
    picks_over25 = _load_picks(DATA_DIR / "picks.json")
    picks_1x2 = _load_picks(DATA_DIR / "picks_1x2.json")
    picks_btts = _load_picks(DATA_DIR / "picks_btts_over25.json")

    stats_o25 = _module_stats(picks_over25, clv_field="clv")
    stats_1x2 = _module_stats(picks_1x2, clv_field="clv")
    stats_btts = _module_stats(picks_btts, clv_field="clv_btts_over25")

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rows = [
        (
            "Over 2.5",
            stats_o25["n_valid"],
            stats_o25["n_settled"],
            stats_o25["clv_rolling"],
            "CLV>+1% n≥300",
            _gate_status(stats_o25["clv_rolling"], stats_o25["n_settled"], 1.0, 300),
        ),
        (
            "Sharp 1X2",
            stats_1x2["n_valid"],
            stats_1x2["n_settled"],
            stats_1x2["clv_rolling"],
            "CLV>+1% n≥200",
            _gate_status(stats_1x2["clv_rolling"], stats_1x2["n_settled"], 1.0, 200),
        ),
        (
            "BTTS+O2.5",
            stats_btts["n_valid"],
            stats_btts["n_settled"],
            stats_btts["clv_rolling"],
            "CLV>+5% n≥100",
            _gate_status(stats_btts["clv_rolling"], stats_btts["n_settled"], 5.0, 100),
        ),
    ]

    lines = [
        "| Módulo | Picks válidos | Settled | CLV rolling-30 | Gate | Estado |",
        "|---|---|---|---|---|---|",
    ]
    for modulo, n_valid, n_settled, clv, gate, estado in rows:
        n_v = str(n_valid) if n_valid else "—"
        n_s = str(n_settled) if n_settled else "—"
        lines.append(
            f"| {modulo} | {n_v} | {n_s} | {_format_clv(clv)} | {gate} | {estado} |"
        )
    lines.append("")
    lines.append(f"_Actualizado: {now_str}_")

    return "\n".join(lines)


def update_readme() -> None:
    if not README_PATH.exists():
        print(f"README.md não encontrado em {README_PATH}", flush=True)
        return

    text = README_PATH.read_text(encoding="utf-8")

    start_idx = text.find(MARKER_START)
    end_idx = text.find(MARKER_END)

    if start_idx == -1 or end_idx == -1:
        print("Marcadores DYNAMIC_STATUS não encontrados no README.md", flush=True)
        return

    new_block = (
        MARKER_START
        + "\n"
        + build_status_block()
        + "\n"
        + MARKER_END
    )

    new_text = text[: start_idx] + new_block + text[end_idx + len(MARKER_END) :]
    README_PATH.write_text(new_text, encoding="utf-8")
    print("README.md actualizado com estatísticas em tempo real.", flush=True)


if __name__ == "__main__":
    update_readme()
