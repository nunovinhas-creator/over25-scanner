#!/usr/bin/env python3
"""
pipeline/send_shadow_summary.py
--------------------------------
Bloco K — resumo diário no Telegram do MODO SOMBRA (Bloco H1,
pipeline/scan_live.py). Lê data/live_shadow_alerts.json e envia UMA
mensagem por dia com o que o gate "🔥 APOSTAR AGORA" teria enviado nas
últimas 24h (e desde o início do modo sombra), e o que os filtros
bloquearam — sem enviar nenhum alerta por jogo.

Nunca toca em LIVE_ALERTS_ENABLED, ALERT_FILTERS, TH_LIVE_PICK,
detect_patterns()/pattern_score() nem em nenhum threshold — só lê o
ficheiro já escrito por run_shadow_alerts(), que é quem decide o que
seria enviado/bloqueado.

WR nunca aparece sozinho — vem sempre com odd média, break-even implícito
e ROI, mesma fórmula que computeCalibSegment()/calibSegLabel() em
index.html (painel "Calibração Live"): é o erro que já foi corrigido nos
painéis, não faz sentido repeti-lo aqui.

Falha explícita, nunca silenciosa: ficheiro ausente ou com conteúdo
inválido envia uma mensagem de erro ao Telegram e termina com exit code 1
— nunca um "sucesso" silencioso que esconderia o workflow partido. Sem
sinais nas últimas 24h também nunca fica em silêncio: envia uma mensagem
curta a dizê-lo, com o acumulado — silêncio total não distingue "não
houve sinais" de "o workflow falhou" (ver .claude/rules/cycles.md e a
mesma regra já aplicada às 👁 OBSERVAÇÕES/health check).

Uso: python -m pipeline.send_shadow_summary
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline.scan_common import send_telegram

ROOT = Path(__file__).resolve().parent.parent
SHADOW_FILE = ROOT / "data" / "live_shadow_alerts.json"

LISBON = ZoneInfo("Europe/Lisbon")
WINDOW_HOURS = 24
MAX_SIGNALS_LISTED = 25


# ---------------------------------------------------------------------------
# Leitura — distingue explicitamente ausência/JSON inválido de uma lista
# vazia legítima. load_json_list() (scan_common) colapsa os três casos em
# [] — aqui isso esconderia exactamente a falha que este módulo existe
# para reportar.
# ---------------------------------------------------------------------------


def _read_alerts() -> list[dict]:
    if not SHADOW_FILE.exists():
        raise FileNotFoundError(f"{SHADOW_FILE} não encontrado")
    raw = SHADOW_FILE.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{SHADOW_FILE} não é JSON válido: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{SHADOW_FILE} não é uma lista JSON (tipo: {type(data).__name__})")
    return data


# ---------------------------------------------------------------------------
# Estatísticas — mesma fórmula que computeCalibSegment() em index.html
# ---------------------------------------------------------------------------


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_odds(raw) -> float | None:
    """Mirror de _calibParseOdds() (index.html): só nº finito > 0."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def compute_segment(entries: list[dict]) -> dict:
    """WR, odd média, break-even implícito e ROI de um conjunto de sinais
    'enviaria' (blocked_by=None) já resolvidos (result_over25 WIN/LOSS).
    Sinais resolvidos sem odds_live utilizável ficam em 'excluded', nunca
    silenciosamente ignorados do n."""
    settled = [e for e in entries if e.get("result_over25") in ("WIN", "LOSS")]
    with_odds = [(e, o) for e in settled if (o := _parse_odds(e.get("odds_live"))) is not None]
    excluded = len(settled) - len(with_odds)
    if not with_odds:
        return {"n": 0, "excluded": excluded, "wr": None, "avg_odds": None, "breakeven": None, "roi": None}
    n = len(with_odds)
    wins = sum(1 for e, _ in with_odds if e["result_over25"] == "WIN")
    wr = wins / n * 100
    avg_odds = sum(o for _, o in with_odds) / n
    breakeven = 100 / avg_odds
    profit = sum((o - 1) if e["result_over25"] == "WIN" else -1 for e, o in with_odds)
    roi = profit / n * 100
    return {"n": n, "excluded": excluded, "wr": wr, "avg_odds": avg_odds, "breakeven": breakeven, "roi": roi}


def format_segment(label: str, seg: dict) -> str:
    """WR nunca sozinho — sempre com odd média, break-even e ROI juntos."""
    if not seg["n"]:
        exc = f" ({seg['excluded']} settled s/odds excl.)" if seg["excluded"] else ""
        return f"{label}: sem sinais resolvidos com odds (n=0){exc}"
    exc = f" (+{seg['excluded']} s/odds excl.)" if seg["excluded"] else ""
    roi_sign = "+" if seg["roi"] >= 0 else ""
    return (
        f"{label}: WR {seg['wr']:.1f}% · n={seg['n']} · odd média {seg['avg_odds']:.2f} · "
        f"break-even {seg['breakeven']:.1f}% · ROI {roi_sign}{seg['roi']:.1f}%{exc}"
    )


def _counts_by_filter(entries: list[dict]) -> tuple[int, dict[str, int]]:
    """(nº enviaria, {filtro: nº bloqueados}). blocked_by=None -> enviaria;
    qualquer outro valor é o filtro de ALERT_FILTERS que bloqueou (só
    xg_banda_morta/minuto_tardio chegam a ser gravados — ver
    _telegram_gate_block_reason em scan_live.py)."""
    sent = 0
    blocked: dict[str, int] = {}
    for e in entries:
        b = e.get("blocked_by")
        if b is None:
            sent += 1
        else:
            blocked[b] = blocked.get(b, 0) + 1
    return sent, blocked


def _fmt_blocked(blocked: dict[str, int]) -> str:
    if not blocked:
        return "0"
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(blocked.items(), key=lambda x: -x[1]))
    return f"{sum(blocked.values())} ({parts})"


def _fmt_pressao(v) -> str:
    return f"{v:.0f}" if isinstance(v, (int, float)) else "—"


def _fmt_odds_display(v) -> str:
    return f"{v:.2f}" if isinstance(v, (int, float)) and v else "—"


def _signal_line(i: int, e: dict) -> str:
    liga = e.get("liga") or "liga desconhecida"
    return (
        f"{i}. {e.get('casa') or '?'} vs {e.get('fora') or '?'} ({liga}) — "
        f"{e.get('min', '?')}' · {e.get('goals', 0)} golos · {e.get('score', '?')} · "
        f"Pressão {_fmt_pressao(e.get('pressao'))} · odd {_fmt_odds_display(e.get('odds_live'))}"
    )


# ---------------------------------------------------------------------------
# Mensagem
# ---------------------------------------------------------------------------


def build_summary(alerts: list[dict], now: datetime) -> str:
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    window = [e for e in alerts if (ts := _parse_ts(e.get("detected_at"))) and ts >= cutoff]

    local_ts = now.astimezone(LISBON).strftime("%d/%m/%Y %H:%M")
    header = f"🌒 Modo sombra — resumo diário ({local_ts} Lisboa)"

    total_sent, total_blocked = _counts_by_filter(alerts)
    total_seg = compute_segment([e for e in alerts if e.get("blocked_by") is None])

    if not window:
        return "\n".join([
            header,
            "Sem sinais nas últimas 24h.",
            "",
            f"Acumulado desde o início do modo sombra — enviaria: {total_sent} · "
            f"bloqueados: {_fmt_blocked(total_blocked)}",
            format_segment("Resultado acumulado", total_seg),
        ])

    sent_24h = [e for e in window if e.get("blocked_by") is None]
    sent_count, blocked_count = _counts_by_filter(window)
    seg_24h = compute_segment(sent_24h)

    lines = [header, "", f"Últimas 24h — enviaria: {sent_count} · bloqueados: {_fmt_blocked(blocked_count)}"]

    if sent_24h:
        lines.append("")
        lines.append("Sinais que o gate teria enviado (24h):")
        shown = sent_24h[:MAX_SIGNALS_LISTED]
        lines.extend(_signal_line(i, e) for i, e in enumerate(shown, 1))
        if len(sent_24h) > MAX_SIGNALS_LISTED:
            lines.append(f"… e mais {len(sent_24h) - MAX_SIGNALS_LISTED}")

    lines.append("")
    lines.append(format_segment("Resultado 24h", seg_24h))
    lines.append("")
    lines.append(
        f"Acumulado desde o início do modo sombra — enviaria: {total_sent} · "
        f"bloqueados: {_fmt_blocked(total_blocked)}"
    )
    lines.append(format_segment("Resultado acumulado", total_seg))

    return "\n".join(lines)


def main() -> int:
    try:
        alerts = _read_alerts()
    except (FileNotFoundError, ValueError) as exc:
        msg = f"⚠️ Modo sombra — resumo diário falhou: {exc}"
        print(msg, file=sys.stderr)
        send_telegram(msg)
        return 1

    summary = build_summary(alerts, datetime.now(timezone.utc))
    print(summary)
    if not send_telegram(summary):
        print("send_telegram falhou — resumo NÃO chegou ao Telegram", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
