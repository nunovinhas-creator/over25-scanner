"""
tests/pipeline/test_scan_common.py
-----------------------------------
Testes de classify_odds() — validação central que classifica odds cruas da
BSD em VALID / SUSPENDED / MISSING (issue #127: "Prob Over 100% · Odd 1.00"
era uma sentinela de mercado suspenso lida como preço real).

Os casos são carregados de tests/fixtures/odds_classification_spec.json —
fonte única de verdade partilhada com tests/js/test_classify_odds.mjs, que
valida a mesma spec contra a implementação JS real em index.html (ver
docs/odds_validation.md para a justificação de manter as duas implementações
em vez de uma só fonte server-side). Qualquer caso novo deve ser adicionado
à spec, não aqui, para que os dois lados continuem sincronizados.

Todos os valores da spec são sintéticos (# synthetic).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pipeline import scan_common as sc
from pipeline.scan_common import MIN_VALID_ODDS, classify_odds

SPEC_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "odds_classification_spec.json"
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_spec_min_valid_odds_matches_python_constant():
    """A spec e o código têm de concordar no piso — evita a spec ficar desactualizada."""
    assert SPEC["min_valid_odds"] == MIN_VALID_ODDS


@pytest.mark.parametrize(
    "case",
    SPEC["cases"],
    ids=[c["name"] for c in SPEC["cases"]],
)  # synthetic
def test_classify_odds(case):
    status, value = classify_odds(case["raw_odds"], case["market_status"])
    assert status == case["expected_status"]
    assert value == case["expected_value"]


def test_suspended_and_missing_never_return_a_value():
    """Invariante central: SUSPENDED/MISSING nunca devolvem um número —
    nunca podem entrar no cálculo de probabilidade/de-vig."""
    for case in SPEC["cases"]:
        if case["expected_status"] == "VALID":
            continue
        status, value = classify_odds(case["raw_odds"], case["market_status"])
        assert status in ("SUSPENDED", "MISSING")
        assert value is None


# ── git_commit_push / git_discard_local_changes ───────────────────────────
#
# Bug real encontrado em produção (run #150, workflow_dispatch de
# live_scanner.yml, 2026-08-09): sem `permissions: contents: write` no
# workflow, o GITHUB_TOKEN do job só tem "Contents: read" e o `git push`
# falha com 403, 4/4 vezes seguidas nos logs reais. git_commit_push() engolia
# o erro e nunca sinalizava a falha ao caller — corrigido para devolver bool.


class _FakeCompleted:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _make_fake_run(diff_returncode, fail_cmd_prefix=None):
    """Fake mínimo de subprocess.run: todos os comandos "passam" (returncode 0)
    excepto o de `git diff --cached --quiet` (controlado por diff_returncode) e,
    se indicado, o primeiro comando cujo prefixo bate com fail_cmd_prefix, que
    levanta CalledProcessError — simula o 403 real de permissions insuficientes."""
    calls: list[list[str]] = []

    def fake_run(cmd, check=False, **kwargs):
        calls.append(cmd)
        if fail_cmd_prefix and cmd[: len(fail_cmd_prefix)] == fail_cmd_prefix:
            raise subprocess.CalledProcessError(1, cmd)
        if cmd[:3] == ["git", "diff", "--cached"]:
            return _FakeCompleted(diff_returncode)
        return _FakeCompleted(0)

    return fake_run, calls


def test_git_commit_push_success_returns_true(monkeypatch):
    fake_run, calls = _make_fake_run(diff_returncode=1)  # há diferenças staged
    monkeypatch.setattr(sc.subprocess, "run", fake_run)

    assert sc.git_commit_push(["data/observations.json"], "msg") is True
    assert ["git", "commit", "-m", "msg"] in calls
    assert ["git", "push", "origin", "main"] in calls


def test_git_commit_push_noop_returns_true_without_commit(monkeypatch):
    """Sem alterações staged (`git diff --cached --quiet` devolve 0): sucesso,
    sem commit nem push — evita commits vazios a cada ciclo."""
    fake_run, calls = _make_fake_run(diff_returncode=0)
    monkeypatch.setattr(sc.subprocess, "run", fake_run)

    assert sc.git_commit_push(["data/observations.json"], "msg") is True
    assert not any(c[:2] == ["git", "commit"] for c in calls)
    assert not any(c[:2] == ["git", "push"] for c in calls)


def test_git_commit_push_returns_false_when_push_denied(monkeypatch, capsys):
    """Reproduz o bug real: git push falha com 403 — git_commit_push() tem de
    devolver False, nunca engolir a falha silenciosamente."""
    fake_run, calls = _make_fake_run(diff_returncode=1, fail_cmd_prefix=["git", "push"])
    monkeypatch.setattr(sc.subprocess, "run", fake_run)

    result = sc.git_commit_push(["data/observations.json"], "msg")

    assert result is False
    assert ["git", "commit", "-m", "msg"] in calls  # o commit local chegou a acontecer
    err = capsys.readouterr().err
    assert "git commit/push falhou" in err


def test_git_commit_push_returns_false_when_fetch_fails(monkeypatch):
    """Falha mais cedo no fluxo (ex.: rede) também tem de devolver False."""
    fake_run, _ = _make_fake_run(diff_returncode=1, fail_cmd_prefix=["git", "fetch"])
    monkeypatch.setattr(sc.subprocess, "run", fake_run)

    assert sc.git_commit_push(["data/observations.json"], "msg") is False


def test_git_discard_local_changes_checks_out_from_origin_main(monkeypatch):
    fake_run, calls = _make_fake_run(diff_returncode=0)
    monkeypatch.setattr(sc.subprocess, "run", fake_run)

    sc.git_discard_local_changes(["data/observations.json"])

    assert ["git", "fetch", "origin", "main"] in calls
    assert ["git", "checkout", "origin/main", "--", "data/observations.json"] in calls


def test_git_discard_local_changes_failure_is_non_fatal(monkeypatch, capsys):
    """Best-effort: se o checkout falhar (ex.: ficheiro ainda não existe em
    origin/main), regista o erro mas não levanta excepção."""
    fake_run, _ = _make_fake_run(diff_returncode=0, fail_cmd_prefix=["git", "checkout"])
    monkeypatch.setattr(sc.subprocess, "run", fake_run)

    sc.git_discard_local_changes(["data/observations.json"])  # não deve levantar

    err = capsys.readouterr().err
    assert "revert de" in err
