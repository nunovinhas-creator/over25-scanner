#!/usr/bin/env python3
"""
pipeline/settle_sharp1x2.py
----------------------------
Settlement dos picks Sharp 1X2 — define `resultado_outcome` (WIN/LOSS/VOID)
a partir do resultado final do evento na BSD API.

CAUSA RAIZ (sessão data-quality-fixes, Ponto 2): antes deste módulo, NADA no
pipeline definia `resultado_outcome` para os picks gerados por
`scan_sharp1x2.py` — o campo ficava sempre em "" para sempre. Por isso
`update_closing_odds.py` (cujo filtro exige `resultado_outcome in (WIN,
LOSS)`) nunca tinha picks elegíveis para tentar o fetch de odds_fecho, e o
gate de CLV nunca podia acender. Os 351 registos settled encontrados em
picks_1x2.json são um import histórico/manual anterior — não produção viva.

Este módulo fecha essa lacuna. Corre ANTES de update_closing_odds.py no
mesmo job (ver .github/workflows/sharp1x2_analysis.yml) — só faz sentido
tentar o fecho de odds Pinnacle depois de saber que o pick está settled.

Janela de settlement (documentada — ver .claude/rules/cycles.md):
  - SETTLE_MIN_H = 2.5h após KO: tempo para 90min + intervalo + descontos +
    margem de segurança antes de considerar "deveria estar acabado".
  - SETTLE_MAX_H = 48h após KO: além disto, se ainda não há score final, é
    tratado como falha explícita (settlement_error), nunca fica pendente
    para sempre silenciosamente.

Fonte do resultado: BSD `/api/v2/events/{event_id}/` (estado + home_score/
away_score). Falha de fetch, evento não finalizado dentro da janela, ou
score inválido → `settlement_error` + `settlement_error_at` explícitos no
próprio pick (nunca silencioso — invariante do projecto).

Quando o evento cai em VOID, `settlement_void_status` grava o token BSD
exacto (já normalizado lower/strip) que disparou essa decisão — só
observabilidade, não altera a semântica de VOID nem o conjunto
_VOID_STATUS (auditoria de continuidade, 9 ago 2026).

Uso: python -m pipeline.settle_sharp1x2
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline.scan_common import git_commit_push, load_json_list, save_json_list

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks_1x2.json"
BSD_BASE = "https://sports.bzzoiro.com"

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")

# Ver docstring do módulo para a justificação destes valores.
SETTLE_MIN_H: float = 2.5
SETTLE_MAX_H: float = 48.0

_FINISHED_STATUS = {
    "finished", "ended", "completed", "closed", "ft", "aet", "afterpenalties",
}
_VOID_STATUS = {"cancelled", "canceled", "postponed", "abandoned", "suspended"}


def fetch_event_result(event_id: str) -> dict | None:
    """
    Fetch do estado final de um evento via BSD API.

    Devolve {"status": str, "home_score": Any, "away_score": Any} ou None se
    o pedido falhar (rede, HTTP, JSON inválido, ou payload sem forma de dict).
    Fail-safe: None nunca é confundido com "evento sem golos" — é sempre
    tratado como "não sabemos" pelo chamador.
    """
    if not BSD_API_KEY:
        return None
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}
    try:
        r = requests.get(
            f"{BSD_BASE}/api/v2/events/{event_id}/",
            headers=headers, timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        print(f"fetch_event_result({event_id}): {exc}", file=sys.stderr)
        return None

    ev = payload if isinstance(payload, dict) else None
    if ev is None:
        return None
    return {
        "status": str(ev.get("status") or "").lower().strip(),
        "home_score": ev.get("home_score"),
        "away_score": ev.get("away_score"),
    }


def resolve_outcome(home_score, away_score) -> str | None:
    """HOME/DRAW/AWAY a partir do score final. None se os scores não forem
    inteiros válidos (nunca inventa um resultado a partir de dados incompletos)."""
    try:
        h = int(home_score)
        a = int(away_score)
    except (TypeError, ValueError):
        return None
    if h > a:
        return "HOME"
    if h < a:
        return "AWAY"
    return "DRAW"


def _event_id_from_pick_id(pick_id: str) -> str:
    part = str(pick_id).split("_")[0]
    return part if part.isdigit() else ""


def settle() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — a abortar", file=sys.stderr)
        sys.exit(0)

    picks = load_json_list(PICKS_FILE)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    n_settled = 0
    n_void = 0
    n_pending = 0
    n_error = 0
    changed = False

    for pick in picks:
        if pick.get("resultado_outcome") in ("WIN", "LOSS", "VOID"):
            continue  # já settled — nada a fazer

        # "data" (KO) ausente ou ilegível não tem âncora temporal nenhuma —
        # elapsed_h/past_deadline (usados por todos os outros ramos de erro
        # abaixo) não podem ser calculados a partir daqui. Sem isto, o pick
        # era descartado da corrida em silêncio, para sempre, em todas as
        # execuções futuras (nunca atingia "past_deadline" porque isso exige
        # elapsed_h) — violava directamente o invariante do módulo (nunca
        # fica pendente para sempre silenciosamente). Não há deadline a
        # esperar aqui: um "data" inválido não fica válido com o tempo, por
        # isso o erro é imediato, não condicional a SETTLE_MAX_H. Nunca
        # inventa nem substitui "data" — só regista o erro explícito e
        # continua para o próximo pick.
        ko_raw = pick.get("data") or ""
        if not str(ko_raw).strip():
            if not pick.get("settlement_error"):
                pick["settlement_error"] = "data_ausente"
                pick["settlement_error_at"] = ts
                n_error += 1
                changed = True
            continue
        try:
            ko = datetime.fromisoformat(str(ko_raw).replace("Z", "+00:00"))
        except Exception:
            if not pick.get("settlement_error"):
                pick["settlement_error"] = "data_invalida"
                pick["settlement_error_at"] = ts
                n_error += 1
                changed = True
            continue
        elapsed_h = (now - ko).total_seconds() / 3600.0

        if elapsed_h < SETTLE_MIN_H:
            n_pending += 1
            continue  # ainda a decorrer / cedo demais para tentar

        event_id = _event_id_from_pick_id(pick.get("id", ""))
        past_deadline = elapsed_h > SETTLE_MAX_H

        if not event_id:
            if past_deadline and not pick.get("settlement_error"):
                pick["settlement_error"] = "id_sem_event_id_valido"
                pick["settlement_error_at"] = ts
                n_error += 1
                changed = True
            elif not past_deadline:
                n_pending += 1
            continue

        result = fetch_event_result(event_id)

        # Guarda: um settlement_error já registado nunca é reescrito. Sem
        # isto, um jogo preso sem resolução da BSD gerava um novo
        # settlement_error_at (e um novo commit [skip ci]) a cada corrida do
        # scan (ver PR #128) — o erro já está explícito, reescrevê-lo é ruído.
        if result is None:
            if past_deadline and not pick.get("settlement_error"):
                pick["settlement_error"] = "bsd_fetch_falhou_apos_48h"
                pick["settlement_error_at"] = ts
                n_error += 1
                changed = True
            elif not past_deadline:
                n_pending += 1
            continue

        status = result["status"]

        if status in _VOID_STATUS:
            pick["resultado_outcome"] = "VOID"
            # Observabilidade pura (auditoria de continuidade, 9 ago 2026):
            # antes disto, o token exacto da BSD que despoletou VOID (qual dos
            # 5 de _VOID_STATUS) era decidido e imediatamente descartado — só
            # ficava "VOID" no pick, sem forma de saber, em produção, se foi
            # "cancelled", "postponed", "suspended", etc. `status` aqui já
            # vem normalizado (lower/strip) por fetch_event_result() — não há
            # nova normalização, só preservação do valor já usado na decisão.
            # Nunca reescrito numa eventual segunda passagem (mesmo padrão de
            # settled_at) — na prática, o guard de topo já impede qualquer
            # pick "VOID" de voltar a este ramo, isto é defesa em profundidade.
            if not pick.get("settlement_void_status"):
                pick["settlement_void_status"] = status
            pick.pop("settlement_error", None)
            pick.pop("settlement_error_at", None)
            n_void += 1
            changed = True
            continue

        if status not in _FINISHED_STATUS:
            if past_deadline and not pick.get("settlement_error"):
                pick["settlement_error"] = f"nao_finalizado_apos_48h:status={status or 'desconhecido'}"
                pick["settlement_error_at"] = ts
                n_error += 1
                changed = True
            elif not past_deadline:
                n_pending += 1
            continue

        actual_outcome = resolve_outcome(result["home_score"], result["away_score"])
        if actual_outcome is None:
            if past_deadline and not pick.get("settlement_error"):
                pick["settlement_error"] = "score_final_invalido"
                pick["settlement_error_at"] = ts
                n_error += 1
                changed = True
            elif not past_deadline:
                n_pending += 1
            continue

        pick_outcome = str(pick.get("outcome", "")).upper()
        pick["resultado_jogo"] = f"{result['home_score']}-{result['away_score']}"
        pick["resultado_outcome"] = "WIN" if actual_outcome == pick_outcome else "LOSS"
        # Idempotente: nunca reescrever settled_at se já existir — é a âncora
        # da janela de closing odds em update_closing_odds.py; reescrevê-lo
        # reiniciaria essa janela (ver pipeline/update_closing_odds.py).
        if not pick.get("settled_at"):
            pick["settled_at"] = ts
        pick.pop("settlement_error", None)
        pick.pop("settlement_error_at", None)
        n_settled += 1
        changed = True

    print(
        f"settle_sharp1x2: {n_settled} settled | {n_void} void | "
        f"{n_pending} ainda pendentes | {n_error} erro explícito registado"
    )

    if changed:
        save_json_list(PICKS_FILE, picks)
        git_commit_push([str(PICKS_FILE)], f"auto-settle sharp1x2 {ts} [skip ci]")
    else:
        print("Nenhuma alteração — sem commit.")


if __name__ == "__main__":
    settle()
