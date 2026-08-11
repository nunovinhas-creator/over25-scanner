#!/usr/bin/env python3
"""
pipeline/send_coverage_summary.py
----------------------------------
Bloco L1 — resumo diário no Telegram da cobertura de dados do Scanner LIVE
(pipeline/scan_live.py, compute_coverage()). Lê data/observations.json e
envia UMA mensagem por dia com, por liga, quantos jogos foram observados e
que fracção dos campos de stats (xG, DA, remates ao alvo, cantos, posse,
momentum) a BSD populou — agregando tanto as 👁 observações reais (dentro
da whitelist) como os registos `kind="coverage_only"` (fora da whitelist,
ver build_coverage_entry() e passes_coverage_log_gate()).

Nunca toca em detect_patterns(), pattern_score(), nos gates (👁 ou
"🔥 APOSTAR AGORA") nem em LIVE_ALERTS_ENABLED — só lê o que
run_observations() já escreveu. Objectivo: acumular 2-3 semanas de dados
para responder a "que ligas têm instrumentação suficiente" sem depender da
whitelist como proxy (ver .claude/rules/data.md e a investigação que
motivou este bloco).

Falha explícita, nunca silenciosa: ficheiro ausente ou com conteúdo
inválido envia uma mensagem de erro ao Telegram e termina com exit code 1
— mesmo padrão de pipeline/send_shadow_summary.py.

Uso: python -m pipeline.send_coverage_summary
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.scan_common import send_telegram
from pipeline.scan_live import COVERAGE_FIELDS

ROOT = Path(__file__).resolve().parent.parent
OBS_FILE = ROOT / "data" / "observations.json"

LISBON = ZoneInfo("Europe/Lisbon")
WINDOW_HOURS = 24
MAX_LEAGUES_LISTED = 20


# ---------------------------------------------------------------------------
# Leitura — distingue explicitamente ausência/JSON inválido de uma lista
# vazia legítima (mesma razão que send_shadow_summary._read_alerts()).
# ---------------------------------------------------------------------------


def _read_observations() -> list[dict]:
    if not OBS_FILE.exists():
        raise FileNotFoundError(f"{OBS_FILE} não encontrado")
    raw = OBS_FILE.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{OBS_FILE} não é JSON válido: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{OBS_FILE} não é uma lista JSON (tipo: {type(data).__name__})")
    return data


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Agregação por liga — cobre 👁 observações reais e registos coverage_only
# (ambos têm coverage_score/coverage_total/coverage_fields, ver
# build_observation()/build_coverage_entry() em scan_live.py).
# ---------------------------------------------------------------------------


def aggregate_by_league(entries: list[dict]) -> dict[str, dict]:
    """liga -> {n, avg_score, total, field_pct: {campo: percentagem}}.
    Entradas sem coverage_score/coverage_total (anteriores ao Bloco L1)
    ficam de fora — não há cobertura para medir nelas."""
    by_liga: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("coverage_score") is None or e.get("coverage_total") is None:
            continue
        liga = e.get("liga") or "liga desconhecida"
        by_liga.setdefault(liga, []).append(e)

    result: dict[str, dict] = {}
    for liga, items in by_liga.items():
        n = len(items)
        total = items[0].get("coverage_total") or len(COVERAGE_FIELDS)
        avg_score = sum(i.get("coverage_score") or 0 for i in items) / n
        field_pct = {}
        for field in COVERAGE_FIELDS:
            filled = sum(1 for i in items if (i.get("coverage_fields") or {}).get(field))
            field_pct[field] = filled / n * 100
        result[liga] = {"n": n, "avg_score": avg_score, "total": total, "field_pct": field_pct}
    return result


def _format_league_line(liga: str, agg: dict) -> str:
    pct = agg["avg_score"] / agg["total"] * 100 if agg["total"] else 0.0
    return f"{liga}: n={agg['n']} · cobertura média {agg['avg_score']:.1f}/{agg['total']} ({pct:.0f}%)"


def _format_league_block(agg_by_liga: dict[str, dict]) -> list[str]:
    ranked = sorted(agg_by_liga.items(), key=lambda kv: (-kv[1]["n"], kv[0]))
    shown = ranked[:MAX_LEAGUES_LISTED]
    lines = [_format_league_line(liga, agg) for liga, agg in shown]
    if len(ranked) > MAX_LEAGUES_LISTED:
        lines.append(f"… e mais {len(ranked) - MAX_LEAGUES_LISTED} liga(s)")
    return lines


# ---------------------------------------------------------------------------
# Mensagem
# ---------------------------------------------------------------------------


def build_summary(entries: list[dict], now: datetime) -> str:
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    window = [e for e in entries if (ts := _parse_ts(e.get("detected_at"))) and ts >= cutoff]

    local_ts = now.astimezone(LISBON).strftime("%d/%m/%Y %H:%M")
    header = f"📡 Cobertura de dados live — resumo diário ({local_ts} Lisboa)"

    total_agg = aggregate_by_league(entries)
    total_n = sum(agg["n"] for agg in total_agg.values())

    if not window:
        return "\n".join([
            header,
            "Sem jogos observados nas últimas 24h.",
            "",
            f"Acumulado desde o início — {len(total_agg)} liga(s), {total_n} jogo(s) observado(s):",
            *_format_league_block(total_agg),
        ])

    window_agg = aggregate_by_league(window)
    window_n = sum(agg["n"] for agg in window_agg.values())

    lines = [
        header, "",
        f"Últimas 24h — {len(window_agg)} liga(s), {window_n} jogo(s):",
        *_format_league_block(window_agg),
        "",
        f"Acumulado desde o início — {len(total_agg)} liga(s), {total_n} jogo(s) observado(s):",
        *_format_league_block(total_agg),
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        entries = _read_observations()
    except (FileNotFoundError, ValueError) as exc:
        msg = f"⚠️ Cobertura de dados live — resumo diário falhou: {exc}"
        print(msg, file=sys.stderr)
        send_telegram(msg)
        return 1

    summary = build_summary(entries, datetime.now(timezone.utc))
    print(summary)
    if not send_telegram(summary):
        print("send_telegram falhou — resumo NÃO chegou ao Telegram", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
