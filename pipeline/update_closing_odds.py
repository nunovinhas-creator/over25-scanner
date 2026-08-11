#!/usr/bin/env python3
"""
pipeline/update_closing_odds.py
--------------------------------
Preenche odds_fecho (Pinnacle) e calcula CLV para picks Sharp 1X2 settled.

Lógica:
- Lê picks_1x2.json
- Filtra: resultado_outcome in (WIN, LOSS) AND odds_fecho vazio
  AND settled_at entre CLOSE_MIN_MIN e CLOSE_MAX_H atrás
- Para cada pick, chama BSD API via fetch_closing_odds()
- Grava odds_fecho e clv = round((odds_entrada / odds_fecho - 1) * 100, 4)
- Faz commit dos picks actualizados [skip ci]

Janela ancorada em settled_at, NUNCA em data/KO (correcção da causa raiz
diagnosticada nesta sessão): settle_sharp1x2.py pode legitimamente demorar
até SETTLE_MAX_H=48h pós-KO a definir resultado_outcome (ver
pipeline/settle_sharp1x2.py). A janela antiga (CLOSE_MAX_H=24h desde o KO)
descartava, sem nunca tentar o fetch, qualquer pick cujo settlement
acontecesse entre as 24h e as 48h pós-KO — e todo o backlog histórico, cujo
settlement só aconteceu muito depois do KO original. Ancorando a
CLOSE_MIN_MIN/CLOSE_MAX_H a settled_at (o momento em que settle_sharp1x2.py
definiu resultado_outcome), a janela deixa de competir com a janela de
settlement — já não precisa de reservar margem para os 48h do settle,
porque só começa a contar depois de o settlement estar feito.

Picks sem settled_at (backlog anterior a esta correcção, settled antes de
settle_sharp1x2.py gravar esse campo) ficam não-elegíveis de forma
silenciosa e correcta — ausência de settled_at nunca é um fetch_error
(não sabemos há quanto tempo foram settled; inventar essa informação
violaria a invariante "nenhum estado de erro pode parecer sucesso" ao
contrário — aqui seria "ausência de dado pode parecer erro").

Pré-requisito: BSD API devolve odds para eventos settled.
Confirmar com scripts/probe_bsd_closing_odds.py antes de usar em produção.

Uso: python -m pipeline.update_closing_odds
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.scan_common import git_commit_push
from pipeline.scan_sharp1x2 import fetch_closing_odds

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks_1x2.json"

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")

# Janela temporal: só tenta fetch entre 15min e 12h após settled_at (NUNCA
# após o KO — ver docstring do módulo). CLOSE_MIN_MIN mantém-se em 15min por
# margem de segurança (mesmo critério de antes, só que agora conta a partir
# do settlement, não do KO — dá tempo ao preço de fecho estabilizar do lado
# da BSD). CLOSE_MAX_H desce de 24h (desde o KO) para 12h (desde settled_at):
# já não precisa de cobrir os 48h de SETTLE_MAX_H, porque só começa a contar
# depois de settle_sharp1x2.py já ter definido resultado_outcome. Com o cron
# de 30 em 30 min (sharp1x2_analysis.yml), 12h dão ~24 tentativas — margem
# ampla para falhas transitórias da BSD sem deixar a janela aberta mais
# tempo do que o necessário.
CLOSE_MIN_MIN: float = 15.0
CLOSE_MAX_H: float = 12.0


def _load_picks() -> list[dict]:
    if not PICKS_FILE.exists():
        return []
    try:
        d = json.loads(PICKS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_picks(picks: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PICKS_FILE.write_text(
        json.dumps(picks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _event_id_from_pick_id(pick_id: str) -> str:
    """Extrai event_id numérico do pick_id (ex: '209508_sh' → '209508')."""
    part = pick_id.split("_")[0]
    return part if part.isdigit() else ""


def update() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — a abortar", file=sys.stderr)
        sys.exit(0)

    picks = _load_picks()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0
    skipped_window = 0
    errored = 0
    changed = False

    for pick in picks:
        if pick.get("resultado_outcome") not in ("WIN", "LOSS"):
            continue  # sem settlement ainda — ver pipeline/settle_sharp1x2.py
        if str(pick.get("odds_fecho", "")).strip():
            continue  # já preenchido

        settled_at_raw = pick.get("settled_at") or ""
        if not settled_at_raw:
            # Backlog anterior a esta correcção (settle_sharp1x2.py ainda não
            # gravava settled_at) — não-elegível, mas NUNCA um erro: não
            # sabemos há quanto tempo foi settled, e inventar essa informação
            # violaria a invariante de nunca fabricar dados. Fica pendente
            # silenciosamente, sem fetch_error, para sempre (ou até um
            # backfill manual decidido à parte — fora do âmbito aqui).
            continue
        try:
            settled_at = datetime.fromisoformat(str(settled_at_raw).replace("Z", "+00:00"))
        except Exception:
            # settled_at ilegível — trata-se como ausente pela mesma razão:
            # não inventamos elegibilidade a partir de dados corrompidos.
            continue

        elapsed_min = (now - settled_at).total_seconds() / 60.0
        pick_id = str(pick.get("id", ""))

        if elapsed_min < CLOSE_MIN_MIN:
            skipped_window += 1
            continue  # ainda cedo demais — sem erro, só ainda não é a altura

        if elapsed_min > CLOSE_MAX_H * 60:
            # Janela fechada sem nunca ter conseguido odds_fecho — falha
            # explícita e definitiva (só marca uma vez, evita commits em loop).
            if not pick.get("fetch_error"):
                pick["fetch_error"] = "janela_fechada_sem_odds_fecho"
                pick["fetch_error_at"] = ts
                errored += 1
                changed = True
            skipped_window += 1
            continue

        event_id = _event_id_from_pick_id(pick_id)
        outcome = str(pick.get("outcome", "")).upper()
        if not event_id or outcome not in ("HOME", "DRAW", "AWAY"):
            if not pick.get("fetch_error"):
                pick["fetch_error"] = "id_ou_outcome_invalido"
                pick["fetch_error_at"] = ts
                errored += 1
                changed = True
            continue

        odds_close = fetch_closing_odds(event_id, outcome)
        if odds_close is None:
            # Falha explícita e visível no próprio pick — nunca só stderr.
            # Mantém-se dentro da janela: próxima corrida tenta de novo.
            pick["fetch_error"] = "bsd_sem_odds_pinnacle_pos_ko"
            pick["fetch_error_at"] = ts
            errored += 1
            changed = True
            print(f"  {pick_id}: BSD sem odds Pinnacle pós-KO", file=sys.stderr)
            continue

        try:
            odds_entrada = float(pick.get("odds_entrada") or 0)
        except (TypeError, ValueError):
            odds_entrada = 0.0
        if odds_entrada <= 1.0:
            pick["fetch_error"] = "odds_entrada_invalida"
            pick["fetch_error_at"] = ts
            errored += 1
            changed = True
            continue

        # CLV positivo = conseguimos odds melhores que o fecho da Pinnacle
        clv = round((odds_entrada / odds_close - 1) * 100, 4)
        pick["odds_fecho"] = str(round(odds_close, 4))
        pick["clv"] = str(clv)
        pick.pop("fetch_error", None)
        pick.pop("fetch_error_at", None)
        updated += 1
        changed = True
        print(f"  {pick_id}: odds_fecho={odds_close:.4f} | CLV={clv:+.2f}%")

    print(
        f"update_closing_odds: {updated} actualizados | "
        f"{skipped_window} fora janela temporal | "
        f"{errored} erro explícito registado"
    )

    if changed:
        _save_picks(picks)
        git_commit_push([str(PICKS_FILE)], f"auto-update closing odds sharp1x2 {ts} [skip ci]")
    else:
        print("Nenhuma alteração — sem commit.")


if __name__ == "__main__":
    update()
