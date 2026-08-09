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


# ── git_commit_push — index obsoleto em processo de longa duração ─────────
#
# Bug real (auditoria de continuidade, 9 ago 2026): `git reset --soft
# origin/main` move o HEAD local mas NÃO sincroniza o index. Num processo de
# vida curta (checkout acabado de fazer) isso é inofensivo — mas
# live_scanner.yml corre um único checkout ~5h50 chamando git_commit_push()
# repetidamente; o index fica preso ao conteúdo do checkout inicial para
# qualquer ficheiro fora de `files`, e o commit resultante reverte
# silenciosamente qualquer ficheiro que outro processo tenha entretanto
# alterado em origin/main. Incidentes reais confirmados na mesma corrida de
# live_scanner.yml (9 ago 2026): 0eef16e reverteu data/rejected_picks.json +
# data/scan_state_over25.json; d7a0e92 reverteu pipeline/settle_sharp1x2.py,
# o seu ficheiro de testes, index.html e version.json — apagando a
# instrumentação do PR #145 poucos minutos depois do merge; 171214c reverteu
# ainda README.md, dashboard/analytics.html, data/picks.json, index.html e
# version.json.
#
# Estes testes usam repositórios git REAIS (não subprocess mockado) — a
# falha está na semântica do git (--soft vs --mixed), que um mock da
# sequência de chamadas nunca apanharia (os testes acima, mockados, continuam
# a passar com `--soft` porque só verificam QUE comandos correm, não o
# CONTEÚDO da árvore resultante).


def _init_bare_remote(tmp_path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return remote


def _clone(remote: Path, dest: Path, identity: str) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "config", "user.email", f"{identity}@test.local"], check=True)
    subprocess.run(["git", "-C", str(dest), "config", "user.name", identity], check=True)
    # git_commit_push() faz sempre `git push origin main` — garantir que a
    # branch local se chama "main" independentemente do init.defaultBranch
    # do ambiente onde os testes correm (evita "src refspec main does not
    # match any"). Se origin/main já existir (clone de um remoto não-vazio),
    # a branch local tem de arrancar dela — senão fica órfã e diverge do
    # histórico real, causando um non-fast-forward espúrio no push seguinte.
    has_origin_main = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "--verify", "-q", "origin/main"],
        capture_output=True,
    ).returncode == 0
    if has_origin_main:
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", "-B", "main", "origin/main"], check=True)
    else:
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", "-B", "main"], check=True)
    return dest


def _commit_and_push(repo: Path, files: list[str], msg: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", *files], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "HEAD:main"], check=True)


def _read_from_remote(remote: Path, path: str) -> str | None:
    r = subprocess.run(
        ["git", "--git-dir", str(remote), "show", f"main:{path}"],
        capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else None


class TestGitCommitPushStaleIndexRealGit:
    def test_stale_long_job_index_does_not_revert_unrelated_external_commit(self, tmp_path, monkeypatch):
        """Cenário 1-5 pedido: checkout inicial em commit A; outro processo
        publica commit B (ficheiro X, não relacionado); job longo actualiza
        só o SEU ficheiro e chama git_commit_push(); o resultado tem de conter
        B, a alteração do job, e NENHUMA reversão do ficheiro de B."""
        remote = _init_bare_remote(tmp_path)

        long_job = _clone(remote, tmp_path / "long_job", "long")
        (long_job / "health.json").write_text("v1")
        (long_job / "y.json").write_text("y1")
        _commit_and_push(long_job, ["health.json", "y.json"], "A: commit inicial")

        # Outro processo (checkout independente, ex. scanner.yml) publica commit B.
        external = _clone(remote, tmp_path / "external", "ext")
        (external / "y.json").write_text("y2-alterado-externamente")
        _commit_and_push(external, ["y.json"], "B: alteracao externa a y.json")

        # O job longo NUNCA re-fez checkout — y.json no seu disco continua "y1".
        assert (long_job / "y.json").read_text() == "y1"

        (long_job / "health.json").write_text("v2")
        monkeypatch.chdir(long_job)
        assert sc.git_commit_push(["health.json"], "health update") is True

        assert _read_from_remote(remote, "y.json") == "y2-alterado-externamente"
        assert _read_from_remote(remote, "health.json") == "v2"

    def test_multiple_external_commits_between_two_health_commits_all_survive(self, tmp_path, monkeypatch):
        """Cenário adicional pedido: múltiplos commits externos entre duas
        execuções de git_commit_push() do mesmo job longo — nenhum deve ser
        revertido, em nenhuma das duas chamadas (reproduz 0eef16e + d7a0e92,
        que aconteceram em sequência na mesma corrida)."""
        remote = _init_bare_remote(tmp_path)

        long_job = _clone(remote, tmp_path / "long_job", "long")
        for name in ("health.json", "a.json", "b.json", "c.json"):
            (long_job / name).write_text("v1")
        _commit_and_push(long_job, ["health.json", "a.json", "b.json", "c.json"], "A: commit inicial")

        external = _clone(remote, tmp_path / "external", "ext")

        # Primeira chamada do job longo — sem nada externo ainda.
        (long_job / "health.json").write_text("v2")
        monkeypatch.chdir(long_job)
        assert sc.git_commit_push(["health.json"], "health v2") is True

        # Dois commits externos entretanto (simulando auto-scan + version bump).
        subprocess.run(["git", "-C", str(external), "pull", "-q", "origin", "main"], check=True)
        (external / "a.json").write_text("a2-alterado-externamente")
        _commit_and_push(external, ["a.json"], "C: auto-scan altera a.json")
        (external / "b.json").write_text("b2-alterado-externamente")
        _commit_and_push(external, ["b.json"], "D: version bump altera b.json")

        # Segunda chamada do job longo — só actualiza health.json de novo.
        # O working tree do job longo NUNCA viu a.json/b.json mudar (continuam v1).
        assert (long_job / "a.json").read_text() == "v1"
        assert (long_job / "b.json").read_text() == "v1"
        (long_job / "health.json").write_text("v3")
        monkeypatch.chdir(long_job)
        assert sc.git_commit_push(["health.json"], "health v3") is True

        assert _read_from_remote(remote, "a.json") == "a2-alterado-externamente"
        assert _read_from_remote(remote, "b.json") == "b2-alterado-externamente"
        assert _read_from_remote(remote, "c.json") == "v1"  # nunca tocado por ninguém
        assert _read_from_remote(remote, "health.json") == "v3"

    def test_file_never_touched_by_anyone_is_never_staged_or_committed(self, tmp_path, monkeypatch):
        """Um ficheiro que diverge no disco do job longo (drift acumulado) mas
        que NUNCA está em `files` não deve ser commitado — só o(s) ficheiro(s)
        explicitamente pedido(s) entram no commit."""
        remote = _init_bare_remote(tmp_path)

        long_job = _clone(remote, tmp_path / "long_job", "long")
        (long_job / "health.json").write_text("v1")
        (long_job / "stray.json").write_text("original")
        _commit_and_push(long_job, ["health.json", "stray.json"], "A: commit inicial")

        # O job longo escreve algo em stray.json no disco (ex. artefacto temporário)
        # mas NUNCA o inclui em `files`.
        (long_job / "stray.json").write_text("modificado-localmente-mas-nunca-adicionado")
        (long_job / "health.json").write_text("v2")
        monkeypatch.chdir(long_job)
        assert sc.git_commit_push(["health.json"], "health v2") is True

        # stray.json no remoto continua o original — nunca foi `git add`ado.
        assert _read_from_remote(remote, "stray.json") == "original"

    def test_real_non_fast_forward_push_rejection_returns_false(self, tmp_path, monkeypatch):
        """Concorrência real: dois clones do job longo tentam publicar ao
        mesmo tempo. O segundo fetch+reset+add+commit já correu sobre um HEAD
        que entretanto ficou desactualizado outra vez (outro processo pushou
        entre o fetch e o push deste) — o push tem de ser rejeitado
        (non-fast-forward) e git_commit_push() tem de devolver False, sem
        levantar excepção nem corromper o estado local."""
        remote = _init_bare_remote(tmp_path)

        job1 = _clone(remote, tmp_path / "job1", "job1")
        (job1 / "health.json").write_text("v1")
        _commit_and_push(job1, ["health.json"], "A: commit inicial")

        job2 = _clone(remote, tmp_path / "job2", "job2")

        # job1 avança e publica antes de job2 (job2 já fez fetch/checkout mas
        # ainda não chamou git_commit_push()).
        (job1 / "health.json").write_text("v2-de-job1")
        monkeypatch.chdir(job1)
        assert sc.git_commit_push(["health.json"], "job1 health v2") is True

        # job2 tenta publicar sem ter voltado a fazer fetch entre o commit de
        # job1 e o seu próprio push — mas git_commit_push() faz sempre fetch
        # antes de reset+add+commit, por isso simulamos a corrida real
        # forçando um push manual concorrente ENTRE o fetch e o push desta
        # chamada: monkeypatch subprocess.run para injectar um push externo
        # depois do "git add" e antes do commit/push do próprio job2.
        real_run = subprocess.run
        injected = {"done": False}

        def racing_run(cmd, check=False, **kwargs):
            result = real_run(cmd, check=check, **kwargs)
            if not injected["done"] and cmd[:2] == ["git", "add"]:
                injected["done"] = True
                (job1 / "health.json").write_text("v3-de-job1-durante-a-corrida")
                real_run(["git", "-C", str(job1), "commit", "-q", "-am", "job1 health v3 (corrida)"], check=True)
                real_run(["git", "-C", str(job1), "push", "-q", "origin", "HEAD:main"], check=True)
            return result

        (job2 / "health.json").write_text("v2-de-job2")
        monkeypatch.chdir(job2)
        monkeypatch.setattr(sc.subprocess, "run", racing_run)

        assert sc.git_commit_push(["health.json"], "job2 health v2") is False
        assert _read_from_remote(remote, "health.json") == "v3-de-job1-durante-a-corrida"
