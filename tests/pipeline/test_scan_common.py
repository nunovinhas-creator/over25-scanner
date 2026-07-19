"""
tests/pipeline/test_scan_common.py
-----------------------------------
Testes de classify_odds() — validação central que classifica odds cruas da
BSD em VALID / SUSPENDED / MISSING (issue #127: "Prob Over 100% · Odd 1.00"
era uma sentinela de mercado suspenso lida como preço real).

Todos os valores aqui são sintéticos (# synthetic).
"""

from __future__ import annotations

import pytest

from pipeline.scan_common import MIN_VALID_ODDS, classify_odds


@pytest.mark.parametrize("raw,status_field,expected_status,expected_value", [  # synthetic
    (1.90, None, "VALID", 1.90),
    (2.50, None, "VALID", 2.50),
    (MIN_VALID_ODDS + 0.01, None, "VALID", MIN_VALID_ODDS + 0.01),
    ("1.85", None, "VALID", 1.85),          # numérico em string (BSD por vezes devolve string)
    (1.00, None, "SUSPENDED", None),        # sentinela confirmada (issue #127)
    (1.01, None, "SUSPENDED", None),        # limite: igual ao piso ainda é suspenso
    (0, None, "SUSPENDED", None),
    (-1.5, None, "SUSPENDED", None),
    (None, None, "MISSING", None),
    ("n/a", None, "MISSING", None),         # não numérico
    (1.90, "suspended", "SUSPENDED", None),  # status explícito vence mesmo com odd válida
    (1.90, "SUSPENDED", "SUSPENDED", None),  # case-insensitive
    (1.90, "stopped", "SUSPENDED", None),
    (1.90, "open", "VALID", 1.90),          # status desconhecido/normal não bloqueia
])
def test_classify_odds(raw, status_field, expected_status, expected_value):
    status, value = classify_odds(raw, status_field)
    assert status == expected_status
    assert value == expected_value


def test_suspended_and_missing_never_return_a_value():
    """Invariante central: SUSPENDED/MISSING nunca devolvem um número —
    nunca podem entrar no cálculo de probabilidade/de-vig."""
    for raw, status_field in [(1.00, None), (0, None), (None, None), ("bad", None), (5.0, "closed")]:
        status, value = classify_odds(raw, status_field)
        assert status in ("SUSPENDED", "MISSING")
        assert value is None
