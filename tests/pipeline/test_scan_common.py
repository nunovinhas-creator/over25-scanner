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
from pathlib import Path

import pytest

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
