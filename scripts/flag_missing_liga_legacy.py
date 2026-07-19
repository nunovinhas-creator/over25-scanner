#!/usr/bin/env python3
"""
scripts/flag_missing_liga_legacy.py
------------------------------------
One-off de conformidade — TAREFA (Ponto 1, sessão data-quality-fixes).

Backfill exacto de `liga` nos picks legacy com liga="" é IMPOSSÍVEL: nenhum
registo histórico em picks.json / picks_1x2.json / picks_btts_over25.json
guarda `league_id` (ou equivalente) — os campos observados são apenas o nome
já resolvido (`liga`), nunca o ID BSD de origem. Sem o ID, qualquer
preenchimento seria heurística por nome de equipa (ambíguo: a mesma equipa
pode jogar em ligas diferentes por época/competição) — decisão explícita:
NÃO inventar ligas por heurística.

Em vez disso, este script garante que todo o pick com `liga` vazia/ausente
já tem `data_quality_flag` definido (para exclusão limpa dos KPIs — ver
.claude/rules/data.md). Idempotente: corre sempre sem risco, só escreve
ficheiro se houver alguma alteração real. Faz backup (.bak-<timestamp>)
antes de escrever.

Uso:
    python scripts/flag_missing_liga_legacy.py           # aplica alterações
    python scripts/flag_missing_liga_legacy.py --dry-run # só reporta
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_FILES = [
    ROOT / "data" / "picks.json",
    ROOT / "data" / "picks_1x2.json",
    ROOT / "data" / "picks_btts_over25.json",
]
FLAG_REASON = "pre_bugfix_liga_vazia"


def flag_missing_liga(picks: list[dict]) -> tuple[list[dict], int]:
    """
    Marca com data_quality_flag qualquer pick cuja liga esteja vazia/ausente
    e ainda não tenha o flag. Não altera o valor de `liga` (permanece como
    está no histórico) nem inventa um nome de liga.

    Devolve (picks_actualizados, n_marcados).
    """
    n_marked = 0
    out: list[dict] = []
    for pick in picks:
        liga = str(pick.get("liga", "")).strip()
        if not liga and not pick.get("data_quality_flag"):
            pick = {**pick, "data_quality_flag": FLAG_REASON}
            n_marked += 1
        out.append(pick)
    return out, n_marked


def _backup(path: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_suffix(path.suffix + f".bak-{ts}")
    shutil.copy2(path, backup_path)
    return backup_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="só reporta, não escreve")
    args = ap.parse_args()

    total_marked = 0
    for path in TARGET_FILES:
        if not path.exists():
            print(f"{path}: não existe — skip")
            continue
        try:
            picks = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"{path}: JSON inválido ({exc}) — skip", file=sys.stderr)
            continue
        if not isinstance(picks, list):
            print(f"{path}: conteúdo não é uma lista — skip", file=sys.stderr)
            continue

        updated, n_marked = flag_missing_liga(picks)
        total_marked += n_marked

        if n_marked == 0:
            print(f"{path}: {len(picks)} picks, 0 por marcar — já conforme")
            continue

        print(f"{path}: {len(picks)} picks, {n_marked} marcados com data_quality_flag")
        if args.dry_run:
            print(f"  [dry-run] nenhuma escrita feita")
            continue

        backup_path = _backup(path)
        print(f"  backup: {backup_path}")
        path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  escrito: {path}")

    print(f"\nTotal marcado: {total_marked} picks")
    if total_marked == 0:
        print(
            "Nenhuma alteração necessária — todos os picks com liga vazia já "
            "têm data_quality_flag. Backfill exacto por league_id continua "
            "impossível (campo nunca foi guardado nos registos históricos)."
        )


if __name__ == "__main__":
    main()
