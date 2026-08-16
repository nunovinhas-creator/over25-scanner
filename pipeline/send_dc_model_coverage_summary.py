#!/usr/bin/env python3
"""
pipeline/send_dc_model_coverage_summary.py
--------------------------------------------
Bloco P — resumo diário no Telegram da cobertura do modelo Dixon-Coles no
Over 2.5 (pipeline/scan_over25.py). Lê data/rejected_picks.json e
data/picks.json e envia UMA mensagem por dia com, por liga, que fracção
dos candidatos que chegaram ao Gate 4 (EV) tiveram o modelo DC a correr
(p_model_source=="dc") vs caíram no fallback de mercado
(p_model_source=="market_only", REJECT_REASON_SEM_MODELO="sem_modelo_dc",
ver scan_over25.py).

Contexto (Bloco O): quando o modelo cai no fallback, p_model colapsa em
p_market e ev_final vira o simétrico do vig — nunca um EV real. Bloco O
corrigiu a causa mecânica (normalize_team_names() nas duas pontas do
lookup); este resumo mede, ao longo do tempo, quanto da diferença entre
football-data.co.uk e a BSD ainda falta cobrir (ver tabela de aliases
proposta no mesmo bloco — nem toda a equipa tem par possível: mudança de
divisão, equipa B, ou simplesmente sem histórico).

Nunca toca em MODEL_WEIGHT, no threshold de EV, em nenhum gate, nem em
scan_over25.py além de já ler o que ele escreve — só lê os ficheiros que o
scan já produz.

Falha explícita, nunca silenciosa: ficheiro ausente ou com conteúdo
inválido envia uma mensagem de erro ao Telegram e termina com exit code 1
— mesmo padrão de pipeline/send_coverage_summary.py / send_shadow_summary.py.

Uso: python -m pipeline.send_dc_model_coverage_summary
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.scan_common import send_telegram

ROOT = Path(__file__).resolve().parent.parent
REJECTED_FILE = ROOT / "data" / "rejected_picks.json"
PICKS_FILE = ROOT / "data" / "picks.json"

LISBON = ZoneInfo("Europe/Lisbon")
WINDOW_HOURS = 24
MAX_LEAGUES_LISTED = 20


# ---------------------------------------------------------------------------
# Leitura — distingue explicitamente ausência/JSON inválido de uma lista
# vazia legítima (mesma razão que send_coverage_summary._read_observations()).
# ---------------------------------------------------------------------------


def _read_json_list(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} não encontrado")
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} não é JSON válido: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path} não é uma lista JSON (tipo: {type(data).__name__})")
    return data


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Agregação por liga — só entradas que chegaram ao Gate 4 (têm
# p_model_source: "dc" ou "market_only"). Gates anteriores (liga/timing/
# odds/drifting) nunca chamam compute_prob(), não têm este campo, ficam
# de fora — não há cobertura de modelo para medir neles.
# ---------------------------------------------------------------------------


def aggregate_by_league(entries: list[dict]) -> dict[str, dict]:
    by_liga: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("p_model_source") not in ("dc", "market_only"):
            continue
        liga = e.get("liga") or "liga desconhecida"
        by_liga.setdefault(liga, []).append(e)

    result: dict[str, dict] = {}
    for liga, items in by_liga.items():
        n = len(items)
        n_dc = sum(1 for i in items if i.get("p_model_source") == "dc")
        result[liga] = {"n": n, "n_dc": n_dc, "pct": (n_dc / n * 100) if n else 0.0}
    return result


def _format_league_line(liga: str, agg: dict) -> str:
    return f"{liga}: n={agg['n']} · com modelo DC {agg['n_dc']}/{agg['n']} ({agg['pct']:.0f}%)"


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
    window = [e for e in entries if (ts := _parse_ts(e.get("scanned_at"))) and ts >= cutoff]

    local_ts = now.astimezone(LISBON).strftime("%d/%m/%Y %H:%M")
    header = f"🧮 Cobertura do modelo DC (Over 2.5) — resumo diário ({local_ts} Lisboa)"

    total_agg = aggregate_by_league(entries)
    total_n = sum(agg["n"] for agg in total_agg.values())
    total_dc = sum(agg["n_dc"] for agg in total_agg.values())

    if not window:
        lines = [
            header,
            "Sem candidatos avaliados pelo Gate 4 (EV) nas últimas 24h.",
            "",
        ]
        if total_n:
            lines.append(
                f"Acumulado desde o início — {len(total_agg)} liga(s), "
                f"{total_dc}/{total_n} com modelo DC ({total_dc/total_n*100:.0f}%):"
            )
            lines.extend(_format_league_block(total_agg))
        else:
            lines.append("Sem histórico acumulado ainda.")
        return "\n".join(lines)

    window_agg = aggregate_by_league(window)
    window_n = sum(agg["n"] for agg in window_agg.values())
    window_dc = sum(agg["n_dc"] for agg in window_agg.values())

    lines = [
        header, "",
        f"Últimas 24h — {len(window_agg)} liga(s), "
        f"{window_dc}/{window_n} com modelo DC ({window_dc/window_n*100:.0f}%):" if window_n else
        f"Últimas 24h — {len(window_agg)} liga(s), 0 candidatos:",
        *_format_league_block(window_agg),
    ]
    if total_n:
        lines.append("")
        lines.append(
            f"Acumulado desde o início — {len(total_agg)} liga(s), "
            f"{total_dc}/{total_n} com modelo DC ({total_dc/total_n*100:.0f}%):"
        )
        lines.extend(_format_league_block(total_agg))
    return "\n".join(lines)


def main() -> int:
    try:
        rejected = _read_json_list(REJECTED_FILE)
        picks = _read_json_list(PICKS_FILE)
    except (FileNotFoundError, ValueError) as exc:
        msg = f"⚠️ Cobertura do modelo DC — resumo diário falhou: {exc}"
        print(msg, file=sys.stderr)
        send_telegram(msg)
        return 1

    summary = build_summary(rejected + picks, datetime.now(timezone.utc))
    print(summary)
    if not send_telegram(summary):
        print("send_telegram falhou — resumo NÃO chegou ao Telegram", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
