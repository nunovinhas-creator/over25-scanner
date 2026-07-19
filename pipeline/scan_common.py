"""
pipeline/scan_common.py
-----------------------
Constantes e helpers partilhados pelos dois scanners de produção
(scan_over25.py e scan_sharp1x2.py). Fonte única de verdade para a
whitelist de ligas, o mapa de IDs BSD, Telegram, git e I/O JSON.

Nota para testes: os scanners re-exportam estes nomes ao nível do módulo,
por isso `patch.object(mod, "send_telegram", ...)` continua a funcionar.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "1352687611")

# Whitelist de produção — 10 ligas BSD (ver .claude/rules/data.md).
# Bundesliga 2 e Serie B ausentes da BSD API — não geram picks em produção.
WHITELIST = {
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Primeira Liga", "Eredivisie", "Belgian Pro League",
    "Championship", "La Liga 2",
}

# Mapa defensivo BSD league_id → nome canónico.
# Fail-closed: ID desconhecido → UNKNOWN_LEAGUE → WHITELIST rejeita.
# BSD devolve nomes diferentes: id=2→"Liga Portugal Betclic",
# id=14→"Pro League", id=38→"Segunda División" — o mapa tem prioridade.
BSD_LEAGUE_ID_MAP: dict[int, str] = {
    1: "Premier League", 2: "Primeira Liga", 3: "La Liga", 4: "Serie A",
    5: "Bundesliga", 6: "Ligue 1", 10: "Eredivisie",
    12: "Championship", 14: "Belgian Pro League", 38: "La Liga 2",
}

# Sentinela explícita para liga irresolúvel (nunca gravar string vazia — ver
# .claude/rules/data.md). Usada quando a BSD não devolve league_id mapeável
# nem league_name/league utilizável. Distinto de uma liga conhecida mas fora
# da whitelist (ex.: "MLS", "USL League One"), que mantém o nome real.
UNKNOWN_LEAGUE = "DESCONHECIDA"

# Piso de odds válida — abaixo disto (ou igual) a odd implica margem <=0%,
# matematicamente impossível para um preço real (ver issue #127). 1.00 é a
# sentinela confirmada que a BSD devolve nos feeds live quando um mercado
# está suspenso (golo/VAR); alinhado com o piso já usado em
# data/schema/bsd_schema.py (pinnNow/oddsOver) e models/math/kelly.py.
MIN_VALID_ODDS = 1.01

# Tokens de estado de mercado suspenso/fechado que a BSD *poderia* expor num
# campo de status dedicado. NÃO confirmados numa resposta real da BSD nesta
# sessão (sem acesso a rede/API key) — mantidos defensivamente para quando a
# API os expuser (ver scripts/probe_bsd_markets.py). O piso numérico
# MIN_VALID_ODDS é o único mecanismo confirmado.
_SUSPENDED_STATUS_TOKENS = {"suspended", "stopped", "closed", "paused", "off", "inactive"}


def classify_odds(raw_odds, market_status=None) -> tuple[str, float | None]:
    """
    Classifica uma odd crua da BSD em VALID / SUSPENDED / MISSING. Nunca deixar
    uma odd suspensa (ou ausente) entrar no cálculo de probabilidade/de-vig
    como se fosse um preço real (issue #127 — "Prob Over 100% · Odd 1.00").

    VALID     — número > MIN_VALID_ODDS: preço negociável.
    SUSPENDED — número <= MIN_VALID_ODDS (inclui a sentinela confirmada 1.00,
                0 e negativos) OU market_status indica mercado suspenso/
                fechado. A BSD respondeu, mas não há preço real.
    MISSING   — odds ausentes (None / não numérico). A BSD não devolveu nada.

    Devolve (status, valor) — valor é sempre None excepto quando VALID.
    """
    status_token = (
        str(market_status or "")
        .strip().lower()
        .replace("_", "").replace("-", "").replace(" ", "")
    )
    if status_token in _SUSPENDED_STATUS_TOKENS:
        return "SUSPENDED", None

    if raw_odds is None:
        return "MISSING", None
    try:
        value = float(raw_odds)
    except (TypeError, ValueError):
        return "MISSING", None

    if value <= MIN_VALID_ODDS:
        return "SUSPENDED", None

    return "VALID", value


def send_telegram(text: str) -> None:
    if not TG_TOKEN:
        print("TG_TOKEN não definido — skip TG", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text}).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, method="POST"), timeout=10
        ) as resp:
            print(f"TG enviado (status {resp.status})")
    except Exception as exc:
        print(f"TG falhou (não fatal): {exc}", file=sys.stderr)


def git_commit_push(files: list[str], msg: str) -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=True)
        subprocess.run(["git", "add"] + files, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run(["git", "commit", "-m", msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print(f"Commit feito: {msg}")
        else:
            print("Sem alterações para commitar.")
    except subprocess.CalledProcessError as exc:
        print(f"git commit/push falhou: {exc}", file=sys.stderr)


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_json_list(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
