#!/usr/bin/env python3
"""
pipeline/update_closing_odds_over25.py
---------------------------------------
Preenche odds_over_close (Pinnacle) e calcula CLV para picks Over 2.5
server-side — equivalente, para data/picks.json, do que
pipeline/update_closing_odds.py já faz para data/picks_1x2.json.

Porque este módulo NÃO reutiliza a âncora settled_at do Sharp 1X2
------------------------------------------------------------------
update_closing_odds.py ancora a sua janela em settled_at, definido por
settle_sharp1x2.py (settlement server-side do resultado 1X2). O Over 2.5
não tem equivalente: resultado_over25 só é definido no browser
(reactSync()/silentSync() em index.html) — não existe settle_over25.py,
e criá-lo está fora do âmbito deste módulo (decisão explícita do Nuno,
sessão Bloco I, 10 ago 2026).

Este módulo ancora antes na hora do KO (campo "data"), exactamente como
captureClosingOdds() já faz hoje no browser (index.html) — a janela NÃO
depende de saber se o jogo já terminou ou qual foi o resultado, só de estar
perto do kickoff (quando a Pinnacle fecha o mercado). Correr isto a cada
30 min via cron (em vez de depender da página estar aberta) garante várias
tentativas dentro da janela.

Porque a fórmula de CLV diverge da do update_closing_odds.py (Sharp 1X2)
--------------------------------------------------------------------------
NÃO HARMONIZAR sem decisão explícita nova — são duas séries distintas:

- Over 2.5 (aqui): clv = (odds_entrada / (odds_fecho × CLV_DEVIG) − 1) × 100
  Isto é a MESMA fórmula que calcCLV() usa hoje no browser (index.html,
  CLV_DEVIG=1.01) — obrigatório para os valores novos serem comparáveis aos
  já registados em data/picks.json (decisão do Nuno, Bloco I).
- Sharp 1X2 (update_closing_odds.py): clv = (odds_entrada / odds_fecho − 1) × 100
  Sem devig — fórmula própria daquele módulo, não alterada aqui.

As duas fórmulas foram confirmadas divergentes por auditoria dos picks
reais em data/picks.json (8 picks sem data_quality_flag, sessão Bloco I):
todos batem certo com a fórmula com devig, nenhum com a versão sem devig.

Uso: python -m pipeline.update_closing_odds_over25
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.scan_common import git_commit_push, load_json_list, save_json_list
from pipeline.scan_over25 import fetch_closing_odds_over25

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks.json"

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")

# Mesma janela que captureClosingOdds() usa no browser (index.html): captura
# a partir de WINDOW_BEFORE_KO_H antes do KO até WINDOW_AFTER_KO_H depois —
# não à espera de settlement, só de o mercado estar perto do fecho.
WINDOW_BEFORE_KO_H: float = 2.0
WINDOW_AFTER_KO_H: float = 1.5

# Mesma constante que CLV_DEVIG no browser (index.html) — ver docstring do
# módulo para a razão de não ser a mesma fórmula do Sharp 1X2.
CLV_DEVIG: float = 1.01


def _event_id_from_pick_id(pick_id: str) -> str:
    """Extrai event_id numérico do pick_id (ex: '209508_btts' → '209508')."""
    part = str(pick_id).split("_")[0]
    return part if part.isdigit() else ""


def update() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — a abortar", file=sys.stderr)
        sys.exit(0)

    picks = load_json_list(PICKS_FILE)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0
    skipped_window = 0
    errored = 0
    changed = False

    for pick in picks:
        if str(pick.get("odds_over_close", "")).strip():
            continue  # já preenchido

        ko_raw = pick.get("data") or ""
        if not str(ko_raw).strip():
            continue  # sem KO — nunca inventa uma janela a partir daqui
        try:
            ko = datetime.fromisoformat(str(ko_raw).replace("Z", "+00:00"))
        except Exception:
            continue  # data ilegível — tratado como ausente, nunca um erro

        elapsed_h = (now - ko).total_seconds() / 3600.0
        pick_id = str(pick.get("id", ""))

        if elapsed_h < -WINDOW_BEFORE_KO_H:
            skipped_window += 1
            continue  # ainda demasiado longe do KO

        if elapsed_h > WINDOW_AFTER_KO_H:
            # Janela fechada sem nunca ter conseguido odds_over_close — falha
            # explícita e definitiva (marca uma única vez, evita commits em loop).
            if not pick.get("fetch_error"):
                pick["fetch_error"] = "janela_fechada_sem_odds_over_close"
                pick["fetch_error_at"] = ts
                errored += 1
                changed = True
            skipped_window += 1
            continue

        event_id = _event_id_from_pick_id(pick_id)
        if not event_id:
            if not pick.get("fetch_error"):
                pick["fetch_error"] = "id_invalido"
                pick["fetch_error_at"] = ts
                errored += 1
                changed = True
            continue

        odds_close = fetch_closing_odds_over25(event_id)
        if odds_close is None:
            # Falha explícita e visível no próprio pick — nunca só stderr.
            # Mantém-se dentro da janela: próxima corrida tenta de novo.
            pick["fetch_error"] = "bsd_sem_odds_pinnacle_over25"
            pick["fetch_error_at"] = ts
            errored += 1
            changed = True
            print(f"  {pick_id}: BSD sem odds Pinnacle over_under_25", file=sys.stderr)
            continue

        try:
            odds_entrada = float(pick.get("odds_over") or 0)
        except (TypeError, ValueError):
            odds_entrada = 0.0
        if odds_entrada <= 1.01:
            pick["fetch_error"] = "odds_over_invalida"
            pick["fetch_error_at"] = ts
            errored += 1
            changed = True
            continue

        # Mesma fórmula que calcCLV() usa no browser (com devig) — ver
        # docstring do módulo. CLV positivo = conseguimos odds melhores que
        # o fecho da Pinnacle.
        clv = round((odds_entrada / (odds_close * CLV_DEVIG) - 1) * 100, 2)
        pick["odds_over_close"] = f"{odds_close:.4f}"
        pick["clv"] = f"{clv:.2f}"
        pick.pop("fetch_error", None)
        pick.pop("fetch_error_at", None)
        updated += 1
        changed = True
        print(f"  {pick_id}: odds_over_close={odds_close:.4f} | CLV={clv:+.2f}%")

    print(
        f"update_closing_odds_over25: {updated} actualizados | "
        f"{skipped_window} fora janela temporal | "
        f"{errored} erro explícito registado"
    )

    if changed:
        save_json_list(PICKS_FILE, picks)
        git_commit_push([str(PICKS_FILE)], f"auto-update closing odds over25 {ts} [skip ci]")
    else:
        print("Nenhuma alteração — sem commit.")


if __name__ == "__main__":
    update()
