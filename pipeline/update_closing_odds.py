#!/usr/bin/env python3
"""
pipeline/update_closing_odds.py
--------------------------------
Preenche odds_fecho (Pinnacle) e calcula CLV para picks Sharp 1X2 settled.

Lógica:
- Lê picks_1x2.json
- Filtra: resultado_outcome in (WIN, LOSS) AND odds_fecho vazio
  AND KO entre CLOSE_MIN_MIN e CLOSE_MAX_H atrás
- Para cada pick, chama BSD API via fetch_closing_odds()
- Grava odds_fecho e clv = round((odds_entrada / odds_fecho - 1) * 100, 4)
- Faz commit dos picks actualizados [skip ci]

Pré-requisito: BSD API devolve odds para eventos settled.
Confirmar com scripts/probe_bsd_closing_odds.py antes de usar em produção.

Uso: python -m pipeline.update_closing_odds
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.scan_sharp1x2 import fetch_closing_odds

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks_1x2.json"

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")

# Janela temporal: só tenta fetch entre 15min e 24h após KO
CLOSE_MIN_MIN: float = 15.0
CLOSE_MAX_H: float = 24.0


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


def _git_commit(msg: str) -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            check=True,
        )
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=True)
        subprocess.run(["git", "add", str(PICKS_FILE)], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run(["git", "commit", "-m", msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print(f"Commit: {msg}")
        else:
            print("Sem alterações para commitar.")
    except subprocess.CalledProcessError as exc:
        print(f"git falhou: {exc}", file=sys.stderr)


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
    updated = 0
    skipped_window = 0
    skipped_no_odds = 0

    for pick in picks:
        if pick.get("resultado_outcome") not in ("WIN", "LOSS"):
            continue
        if str(pick.get("odds_fecho", "")).strip():
            continue  # já preenchido

        ko_raw = pick.get("data") or pick.get("commence_time", "")
        if not ko_raw:
            continue
        try:
            ko = datetime.fromisoformat(ko_raw.replace("Z", "+00:00"))
        except Exception:
            continue

        elapsed_min = (now - ko).total_seconds() / 60.0
        if elapsed_min < CLOSE_MIN_MIN or elapsed_min > CLOSE_MAX_H * 60:
            skipped_window += 1
            continue

        pick_id = str(pick.get("id", ""))
        event_id = _event_id_from_pick_id(pick_id)
        if not event_id:
            continue

        outcome = str(pick.get("outcome", "")).upper()
        if outcome not in ("HOME", "DRAW", "AWAY"):
            continue

        odds_close = fetch_closing_odds(event_id, outcome)
        if odds_close is None:
            print(f"  {pick_id}: BSD sem odds Pinnacle pós-KO", file=sys.stderr)
            skipped_no_odds += 1
            continue

        try:
            odds_entrada = float(pick.get("odds_entrada") or 0)
        except (TypeError, ValueError):
            continue
        if odds_entrada <= 1.0:
            continue

        # CLV positivo = conseguimos odds melhores que o fecho da Pinnacle
        clv = round((odds_entrada / odds_close - 1) * 100, 4)
        pick["odds_fecho"] = str(round(odds_close, 4))
        pick["clv"] = str(clv)
        updated += 1
        print(f"  {pick_id}: odds_fecho={odds_close:.4f} | CLV={clv:+.2f}%")

    print(
        f"update_closing_odds: {updated} actualizados | "
        f"{skipped_window} fora janela temporal | "
        f"{skipped_no_odds} sem odds BSD"
    )

    if updated:
        _save_picks(picks)
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _git_commit(f"auto-update closing odds sharp1x2 {ts} [skip ci]")
    else:
        print("Nenhuma alteração — sem commit.")


if __name__ == "__main__":
    update()
