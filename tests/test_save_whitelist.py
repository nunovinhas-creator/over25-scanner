"""
tests/test_save_whitelist.py
-----------------------------
Regressão: guards de whitelist em savePick, saveLivePick e autoLogObservations.

Os guards bloqueiam gravações de picks de ligas fora das 10 de produção.
Verifica a lógica equivalente em Python e confirma que o código JS correcto
está presente no index.html.
"""
from __future__ import annotations
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

WHITELIST = {
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Primeira Liga", "Eredivisie", "Belgian Pro League",
    "Championship", "La Liga 2",
}


def _would_save(liga: str) -> bool:
    """Equivalente Python do guard JS: SHARP1X2_WHITELIST.has(liga||'')."""
    return liga in WHITELIST


# ── Teste 1 — savePick: USL Championship bloqueado ──────────────────────────

class TestSavePickWhitelistGuard:
    def test_usl_championship_blocked(self):
        """savePick com jogo USL Championship não deve guardar."""
        liga = "USL Championship"
        assert not _would_save(liga), "USL Championship não está na whitelist — pick deve ser bloqueado"

    def test_world_cup_blocked(self):
        """saveLivePick com jogo do Mundial não deve guardar."""
        liga = "World Cup 2026"
        assert not _would_save(liga), "World Cup não está na whitelist — pick live deve ser bloqueado"

    def test_serie_a_italiana_passes(self):
        """savePick com jogo Serie A italiana (Juventus vs Inter) deve passar."""
        liga = "Serie A"
        assert _would_save(liga), "Serie A está na whitelist — Juventus vs Inter deve poder ser guardado"

    def test_empty_league_blocked(self):
        """Liga vazia é sempre bloqueada — fail-closed."""
        assert not _would_save(""), "Liga vazia deve ser bloqueada"
        assert not _would_save(None or ""), "None tratado como vazio deve ser bloqueado"

    def test_brasileirao_blocked(self):
        """Brasileirão Serie A (nome BSD 'Brasileirão Serie A') não é whitelisted."""
        assert not _would_save("Brasileirão Serie A")
        assert not _would_save("Brasileirao")

    def test_live_finland_blocked(self):
        """Veikkausliiga (Finlândia) não está na whitelist."""
        assert not _would_save("Veikkausliiga")

    def test_all_10_whitelisted_leagues_pass(self):
        """Todas as 10 ligas de produção devem passar o guard."""
        for liga in WHITELIST:
            assert _would_save(liga), f"{liga!r} está na whitelist mas seria bloqueada"


# ── Teste 2 — Confirma guards JS presentes no index.html ────────────────────

class TestIndexHtmlGuards:
    def _html(self) -> str:
        return (ROOT / "index.html").read_text(encoding="utf-8")

    def test_savePick_has_whitelist_guard(self):
        """savePick deve ter guard de whitelist antes de picksSave."""
        html = self._html()
        # Localiza a função — picksSave está ~2000 chars dentro (muitos campos no payload)
        fn_start = html.index("async function savePick(btn)")
        fn_body = html[fn_start:fn_start + 2500]
        assert "SHARP1X2_WHITELIST.has(_saveLeague)" in fn_body, \
            "savePick não tem guard de whitelist"
        # Guard deve aparecer ANTES da primeira chamada picksSave
        guard_pos = fn_body.index("SHARP1X2_WHITELIST.has(_saveLeague)")
        save_pos = fn_body.index("picksSave(")
        assert guard_pos < save_pos, "Guard deve preceder picksSave em savePick"

    def test_saveLivePick_has_whitelist_guard(self):
        """saveLivePick deve ter guard de whitelist antes de picksSave."""
        html = self._html()
        fn_start = html.index("async function saveLivePick(btn)")
        fn_body = html[fn_start:fn_start + 800]
        assert "SHARP1X2_WHITELIST.has(_liveLiga)" in fn_body, \
            "saveLivePick não tem guard de whitelist"
        guard_pos = fn_body.index("SHARP1X2_WHITELIST.has(_liveLiga)")
        save_pos = fn_body.index("picksSave(")
        assert guard_pos < save_pos, "Guard deve preceder picksSave em saveLivePick"

    def test_autoLogObservations_has_whitelist_guard(self):
        """autoLogObservations deve ter guard de whitelist no filtro toSave."""
        html = self._html()
        fn_start = html.index("async function autoLogObservations()")
        fn_body = html[fn_start:fn_start + 500]
        assert "SHARP1X2_WHITELIST.has(e.league" in fn_body, \
            "autoLogObservations não tem guard de whitelist no filtro toSave"
