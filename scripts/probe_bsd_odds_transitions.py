#!/usr/bin/env python3
"""
scripts/probe_bsd_odds_transitions.py
----------------------------------------
Sondagem automática de TRANSIÇÕES de estado de odds na BSD API — sucessor de
scripts/probe_bsd_odds_states.py para o estado "suspenso mid-game" (golo/VAR).

Problema que resolve: uma suspensão de mercado dura segundos. Um
workflow_dispatch manual (probe_bsd_odds_states.py) exige que alguém o
dispare exactamente nessa janela — inviável na prática. Este script
substitui a intervenção manual por polling: corre em loop durante
PROBE_RUN_MINUTES minutos, sondando os jogos ao vivo a cada
POLL_INTERVAL_SECONDS segundos, e grava a sequência ANTES → DURANTE → DEPOIS
sempre que a classificação de uma odd muda (ver classify_odds() em
pipeline/scan_common.py — é a mesma função, não uma cópia).

Auto-desligamento: isto é diagnóstico one-off, não monitorização permanente.
Assim que já existir no output uma transição para o estado SUSPENDED, as
corridas seguintes (cron) detectam-no e saem de imediato, sem gastar
nenhuma chamada à API. `workflow_dispatch` com `force=true` ignora o marker
para permitir corridas extra deliberadas.

Rate limits: só faz polling ao endpoint /odds/ dos jogos já identificados
como ao vivo (MAX_TRACKED_EVENTS no máximo) — a lista de jogos ao vivo em si
(que exige uma query mais pesada, delimitada por data) só é actualizada a
cada LIVE_REFRESH_SECONDS, não a cada poll.

Corre via GitHub Actions (`probe_bsd_odds_transitions.yml`, cron 2x/semana +
workflow_dispatch) com o secret BSD_API_KEY. Fail-closed: sem API key,
aborta sem inventar nada (ver docs/ODDS_VALIDATION.md).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from pipeline.scan_common import classify_odds, git_commit_push
from pipeline.scan_live import fetch_live_events

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
BASE = "https://sports.bzzoiro.com"
TIMEOUT = 20

POLL_INTERVAL_SECONDS = int(os.environ.get("PROBE_POLL_INTERVAL", "25"))
RUN_MINUTES_DEFAULT = 60
LIVE_REFRESH_SECONDS = 180  # actualiza a lista de jogos ao vivo a cada 3 min, não a cada poll
MAX_TRACKED_EVENTS = 20     # protege a BSD de um poll amplo demais se houver muitos jogos
MAX_TRANSITIONS = 5

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = _PROJECT_ROOT / "data" / "probe_odds_transitions.json"

# Chaves que identificam equipas/liga em qualquer nível do payload — nunca
# saem para o ficheiro commitado nem para o artefacto do workflow.
_IDENTIFYING_KEYS = {
    "home_team", "away_team", "home", "away",
    "league_name", "league", "team_home", "team_away",
}


def _get(path: str) -> object:
    url = path if path.startswith("http") else BASE + path
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _extract_over_odds(odds_response) -> object:
    if not isinstance(odds_response, dict):
        return None
    odds_block = odds_response.get("odds") or {}
    return odds_block.get("over_25_goals") if isinstance(odds_block, dict) else None


def _extract_market_status(odds_response) -> object:
    if not isinstance(odds_response, dict):
        return None
    odds_block = odds_response.get("odds") or {}
    if isinstance(odds_block, dict) and odds_block.get("over_25_goals_status"):
        return odds_block.get("over_25_goals_status")
    return odds_response.get("status")


def _anonymize_payload(obj):
    """Anonimização recursiva — substitui qualquer chave identificativa
    (equipa/liga) em qualquer nível do payload, mantendo odds/status intactos."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _IDENTIFYING_KEYS:
                out[k] = f"<{k}>"
            else:
                out[k] = _anonymize_payload(v)
        return out
    if isinstance(obj, list):
        return [_anonymize_payload(v) for v in obj]
    return obj


@dataclass
class _EventState:
    last_status: str
    last_market_status: object
    last_response: dict
    pending_before: dict | None = None
    pending_during: dict | None = None
    pending_status: str | None = None


class TransitionTracker:
    """Máquina de estados por evento: acumula leituras de /odds/ e emite um
    registo ANTES/DURANTE/DEPOIS assim que uma transição de classify_odds()
    (ou do market_status cru) é seguida de uma leitura posterior.

    Puro — sem I/O de rede — para ser testável com payloads sintéticos.
    """

    def __init__(self, max_transitions: int = MAX_TRANSITIONS):
        self.max_transitions = max_transitions
        self._state: dict[str, _EventState] = {}
        self.transitions: list[dict] = []

    def is_full(self) -> bool:
        return len(self.transitions) >= self.max_transitions

    def observe(self, event_id: str, odds_response: dict, now_iso: str) -> dict | None:
        if self.is_full():
            return None

        raw_over = _extract_over_odds(odds_response)
        market_status = _extract_market_status(odds_response)
        status, _ = classify_odds(raw_over, market_status)

        state = self._state.get(event_id)
        if state is None:
            self._state[event_id] = _EventState(status, market_status, odds_response)
            return None

        if state.pending_during is not None:
            record = {
                "event_id": event_id,
                "captured_at": now_iso,
                "before_status": state.last_status,
                "during_status": state.pending_status,
                "after_status": status,
                "before": state.pending_before,
                "during": state.pending_during,
                "after": odds_response,
            }
            self.transitions.append(record)
            self._state[event_id] = _EventState(status, market_status, odds_response)
            return record

        changed = status != state.last_status or market_status != state.last_market_status
        if changed:
            state.pending_before = state.last_response
            state.pending_during = odds_response
            state.pending_status = status
            return None

        state.last_status = status
        state.last_market_status = market_status
        state.last_response = odds_response
        return None


def _load_existing_transitions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _already_captured_suspended(records: list[dict]) -> bool:
    """Auto-desligamento: já temos pelo menos um exemplo do estado alvo
    (suspenso mid-game) — a sondagem cumpriu o objectivo, não precisa de
    voltar a correr sozinha (cron). workflow_dispatch com force=true ignora
    isto para permitir corridas extra deliberadas."""
    return any(r.get("during_status") == "SUSPENDED" for r in records)


def _is_force_enabled() -> bool:
    return os.environ.get("PROBE_FORCE", "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    if not BSD_API_KEY:
        print(
            "BSD_API_KEY não definido — abortar sem inventar payloads. "
            "Correr via workflow_dispatch (probe_bsd_odds_transitions.yml) com o secret configurado.",
            file=sys.stderr,
        )
        sys.exit(1)

    existing = _load_existing_transitions(OUTPUT_PATH)
    force = _is_force_enabled()
    if not force and _already_captured_suspended(existing):
        print(
            f"Estado 'suspenso mid-game' já capturado anteriormente ({OUTPUT_PATH}) — "
            "auto-desligamento, 0 chamadas à API. Usar workflow_dispatch com force=true "
            "para forçar nova corrida."
        )
        return

    run_minutes = int(os.environ.get("PROBE_RUN_MINUTES", str(RUN_MINUTES_DEFAULT)))
    print("=" * 70)
    print("BSD ODDS TRANSITIONS PROBE")
    print(f"ts={datetime.now(timezone.utc).isoformat()} run_minutes={run_minutes} "
          f"poll_interval={POLL_INTERVAL_SECONDS}s max_transitions={MAX_TRANSITIONS} force={force}")
    print("=" * 70)

    tracker = TransitionTracker(max_transitions=MAX_TRANSITIONS)
    tracked_ids: list[str] = []
    last_live_refresh = 0.0
    deadline = time.monotonic() + run_minutes * 60
    poll_count = 0

    while time.monotonic() < deadline and not tracker.is_full():
        now = time.monotonic()
        if not tracked_ids or (now - last_live_refresh) >= LIVE_REFRESH_SECONDS:
            try:
                live_events = fetch_live_events(BSD_API_KEY)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ fetch_live_events falhou: {exc}", file=sys.stderr)
                live_events = []
            tracked_ids = [str(e.get("id")) for e in live_events if e.get("id") is not None][:MAX_TRACKED_EVENTS]
            last_live_refresh = now
            print(f"[refresh] a acompanhar {len(tracked_ids)} jogo(s) ao vivo")

        for ev_id in tracked_ids:
            try:
                odds = _get(f"/api/v2/events/{ev_id}/odds/")
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ odds ev={ev_id} → erro: {exc}")
                continue
            record = tracker.observe(ev_id, odds, datetime.now(timezone.utc).isoformat())
            if record:
                print(
                    f"  TRANSIÇÃO ev={ev_id} {record['before_status']} → "
                    f"{record['during_status']} → {record['after_status']} "
                    f"({len(tracker.transitions)}/{MAX_TRANSITIONS})"
                )
            if tracker.is_full():
                break

        poll_count += 1
        if tracker.is_full():
            print(f"Cap de {MAX_TRANSITIONS} transições atingido após {poll_count} ciclo(s) — a terminar cedo.")
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    print("\n" + "=" * 70)
    print("SUMÁRIO")
    print("=" * 70)
    print(f"Ciclos de polling: {poll_count}")
    print(f"Transições novas capturadas: {len(tracker.transitions)}")
    if not tracker.transitions:
        print("Nenhuma transição observada nesta corrida — normal se não houve golo/VAR/"
              "início de intervalo durante a janela. Não escrever nada inventado; "
              "a próxima corrida agendada tenta de novo.")

    anonymized_new = [_anonymize_payload(r) for r in tracker.transitions]
    all_records = existing + anonymized_new
    if anonymized_new:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(all_records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        git_commit_push(
            [str(OUTPUT_PATH)],
            f"auto-probe odds transitions {ts} ({len(anonymized_new)} nova(s)) [skip ci]",
        )
    else:
        print("Sem transições novas — nada para commitar.")


if __name__ == "__main__":
    main()
