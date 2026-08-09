#!/usr/bin/env node
// tests/js/test_over25_score_integrity.mjs
// -----------------------------------------------------------------------
// Regressão — Settlement Over 2.5: integridade do score (auditoria de
// continuidade, 9 ago 2026).
//
// Bug: os 3 caminhos client-side que settlam Over 2.5 (syncResults(),
// silentSync() parte 1, syncAgentThink()/reactSync()) faziam
// `ev.home_score??0, ev.away_score??0` — um score ausente/inválido virava
// "0-0" e gravava result_over25="LOSS" silenciosamente errado, sem nunca
// ser detectado como erro (pior que o Bug 2 do Sharp 1X2: lá, dados
// inválidos geravam settlement_error explícito; aqui fabricavam um
// resultado falso).
//
// Correcção: `_validMatchScore(homeScoreRaw, awayScoreRaw)` — extraída de
// index.html via marcadores (mesma técnica de test_classify_odds.mjs) —
// nunca aceita null/undefined/string vazia/não-numérico/decimal/negativo.
// Os 3 caminhos foram alterados para nunca escrever result_over25 sem um
// score validado.
//
// Este teste extrai os blocos REAIS de index.html (não reimplementa a
// lógica) e corre-os num sandbox Node (vm) com mocks mínimos das
// dependências externas (get/ghWrite/pinnacleRow/calcCLV/el/renderDash/
// normalizePick) — prova comportamento real, não uma cópia da intenção.
//
// Uso: node tests/js/test_over25_score_integrity.mjs
// Sem dependências externas — só módulos nativos do Node.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');
const INDEX_HTML_PATH = join(REPO_ROOT, 'index.html');
const HTML = readFileSync(INDEX_HTML_PATH, 'utf8');

let checks = 0;
const failures = [];

function check(desc, fn) {
  checks++;
  try {
    fn();
  } catch (err) {
    failures.push(`  ✗ ${desc}: ${err.message}`);
  }
}

function extractBlock(startMarker, endMarker) {
  const s = '// ' + startMarker, e = '// ' + endMarker;
  const si = HTML.indexOf(s);
  const ei = HTML.indexOf(e);
  if (si === -1 || ei === -1 || ei < si) {
    throw new Error(`Marcadores ${startMarker} / ${endMarker} não encontrados em index.html — bloco foi movido/renomeado sem actualizar este teste.`);
  }
  return HTML.slice(si + s.length, ei);
}

// ── _validMatchScore() — a função partilhada pelos 3 caminhos ─────────────

function loadValidMatchScore() {
  const src = extractBlock('SCORE_VALIDATION_JS:START', 'SCORE_VALIDATION_JS:END');
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(src + '\nthis._validMatchScore = _validMatchScore;', sandbox);
  if (typeof sandbox._validMatchScore !== 'function') {
    throw new Error('_validMatchScore não foi definida pelo bloco extraído.');
  }
  return sandbox._validMatchScore;
}

// ── syncResults() — núcleo (botão manual) ──────────────────────────────────
// Wrapper fornece pending/picks/status/get/pinnacleRow/calcCLV/ghWrite/
// PICKS_FILE/_validMatchScore como variáveis já existentes na função real
// (o bloco extraído não as declara, usa-as de fora).

function loadSyncResultsCore() {
  const src = extractBlock('SYNCRESULTS_CORE_JS:START', 'SYNCRESULTS_CORE_JS:END');
  const wrapped = `
    async function __run(pending, picks, status, get, pinnacleRow, calcCLV, ghWrite, PICKS_FILE, _validMatchScore) {
      ${src}
      return { updated, notFound };
    }
    this.__run = __run;
  `;
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(wrapped, sandbox);
  return sandbox.__run;
}

// ── silentSync() parte 1 — núcleo (automático) ─────────────────────────────

function loadSilentSyncOver25Core() {
  const src = extractBlock('SILENTSYNC_OVER25_CORE_JS:START', 'SILENTSYNC_OVER25_CORE_JS:END');
  const wrapped = `
    async function __run(picks, get, pinnacleRow, calcCLV, ghWrite, PICKS_FILE, _validMatchScore, el, renderDash, normalizePick) {
      let allPicks;
      ${src}
      return { picks, ghWriteCalled: typeof allPicks !== 'undefined' };
    }
    this.__run = __run;
  `;
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(wrapped, sandbox);
  return sandbox.__run;
}

// ── syncAgentThink() Step 1 update — núcleo (ReAct Sync) ───────────────────
// Extrai só a arrow function `results=>{...}` (fecha sobre `state`); o
// wrapper recebe `state` como parâmetro para a closure se formar
// correctamente, tal como na função real (onde `state` é o argumento de
// syncAgentThink(state)).

function loadSyncAgentUpdate() {
  const src = extractBlock('SYNCAGENT_UPDATE_JS:START', 'SYNCAGENT_UPDATE_JS:END')
    .replace(/^\s*update:/, ''); // remove a chave do object literal, fica só a arrow fn
  const wrapped = `
    function __makeUpdate(state, _validMatchScore) {
      return (${src});
    }
    this.__makeUpdate = __makeUpdate;
  `;
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(wrapped, sandbox);
  return sandbox.__makeUpdate;
}

// ── mocks partilhados ───────────────────────────────────────────────────────

function mockGet(eventsById) {
  return async (url) => {
    const evMatch = url.match(/events\/([^/]+)\//);
    if (evMatch) return eventsById[evMatch[1]] ?? null;
    if (url.includes('/api/v2/odds/')) return { results: [] }; // sem closing odds — CLV no-op
    return null;
  };
}
const noopPinnacleRow = () => undefined;
const noopCalcCLV = () => '';
const noopEl = () => ({ classList: { contains: () => false } });
const noopRenderDash = () => {};
const identityNormalizePick = p => p;

// ── casos canónicos de score (usados nos 3 caminhos) ───────────────────────

const CASES = [
  { name: 'finished 2-1 → WIN',            ev: { status: 'finished', home_score: 2, away_score: 1 },       expect: 'WIN' },
  { name: 'finished 1-1 → LOSS',           ev: { status: 'finished', home_score: 1, away_score: 1 },       expect: 'LOSS' },
  { name: 'home_score null → sem settle',  ev: { status: 'finished', home_score: null, away_score: 1 },    expect: null },
  { name: 'away_score null → sem settle',  ev: { status: 'finished', home_score: 2, away_score: null },    expect: null },
  { name: 'ambos null → sem settle',       ev: { status: 'finished', home_score: null, away_score: null }, expect: null },
  { name: 'scores undefined → sem settle', ev: { status: 'finished' },                                     expect: null },
  { name: 'score não-numérico → sem settle', ev: { status: 'finished', home_score: 'abc', away_score: 1 }, expect: null },
  { name: 'score negativo → sem settle',   ev: { status: 'finished', home_score: -1, away_score: 2 },      expect: null },
  { name: 'strings numéricas → WIN',       ev: { status: 'finished', home_score: '2', away_score: '1' },   expect: 'WIN' },
  { name: 'score elevado sem cap → WIN',   ev: { status: 'finished', home_score: 1000, away_score: 1000 }, expect: 'WIN' },
];

function main() {
  // ── 1. _validMatchScore() directamente ──────────────────────────────────
  const validMatchScore = loadValidMatchScore();
  for (const c of CASES) {
    check(`_validMatchScore: ${c.name}`, () => {
      const r = validMatchScore(c.ev.home_score, c.ev.away_score);
      if (c.expect === null) {
        assert.equal(r, null, `esperava null, obteve ${JSON.stringify(r)}`);
      } else {
        assert.notEqual(r, null, 'esperava score válido, obteve null');
        const total = r.h + r.a;
        const outcome = total > 2.5 ? 'WIN' : 'LOSS';
        assert.equal(outcome, c.expect);
      }
    });
  }

  // ── 2. syncResults() núcleo ──────────────────────────────────────────────
  const syncResultsCore = loadSyncResultsCore();
  for (const c of CASES) {
    await_check(`syncResults(): ${c.name}`, async () => {
      const pick = { id: '1', casa: 'A', fora: 'B', data: '2020-01-01T00:00:00Z', odds_over: '1.90', result_over25: '' };
      const picks = [pick];
      const ghWriteCalls = [];
      const status = { textContent: '' };
      await syncResultsCore(
        [pick], picks, status,
        mockGet({ '1': c.ev }), noopPinnacleRow, noopCalcCLV,
        async (...a) => ghWriteCalls.push(a),
        'data/picks.json', validMatchScore,
      );
      if (c.expect === null) {
        assert.equal(pick.result_over25, '', 'não devia settlar');
        assert.equal(ghWriteCalls.length, 0, 'não devia chamar ghWrite() com score inválido');
      } else {
        assert.equal(pick.result_over25, c.expect);
        assert.equal(ghWriteCalls.length, 1, 'devia persistir com score válido');
      }
    });
  }

  // ── 3. silentSync() parte 1 núcleo ───────────────────────────────────────
  const silentSyncCore = loadSilentSyncOver25Core();
  for (const c of CASES) {
    await_check(`silentSync(): ${c.name}`, async () => {
      const pick = { id: '2', data: '2020-01-01T00:00:00Z', odds_over: '1.90', result_over25: '' };
      const picks = [pick];
      const ghWriteCalls = [];
      await silentSyncCore(
        picks,
        mockGet({ '2': c.ev }), noopPinnacleRow, noopCalcCLV,
        async (...a) => ghWriteCalls.push(a),
        'data/picks.json', validMatchScore,
        noopEl, noopRenderDash, identityNormalizePick,
      );
      if (c.expect === null) {
        assert.equal(pick.result_over25, '', 'não devia settlar');
        assert.equal(ghWriteCalls.length, 0, 'não devia chamar ghWrite() com score inválido');
      } else {
        assert.equal(pick.result_over25, c.expect);
        assert.equal(ghWriteCalls.length, 1, 'devia persistir com score válido');
      }
    });
  }

  // ── 4. syncAgentThink() Step 1 update — núcleo (ReAct Sync) ──────────────
  const makeUpdate = loadSyncAgentUpdate();
  for (const c of CASES) {
    check(`syncAgentThink() update: ${c.name}`, () => {
      const pick = { id: '3', result_over25: '' };
      const state = { picks: [pick], updated: 0 };
      const results = [{ id: '3', ev: c.ev }];
      const updateFn = makeUpdate(state, validMatchScore);
      const out = updateFn(results);
      const newPick = out.picks.find(p => p.id === '3');
      if (c.expect === null) {
        assert.equal(newPick.result_over25 ?? '', '', 'não devia settlar');
        assert.equal(out.updated, 0, 'delta não devia avançar com score inválido');
      } else {
        assert.equal(newPick.result_over25, c.expect);
        assert.equal(out.updated, 1);
      }
    });
  }

  // ── 5. lote misto — pick inválido não bloqueia os restantes (3 caminhos) ─
  await_check('syncResults(): lote misto (1 inválido + 1 válido) processa ambos', async () => {
    const bad = { id: '10', casa: 'A', fora: 'B', data: '2020-01-01T00:00:00Z', odds_over: '1.90', result_over25: '' };
    const good = { id: '11', casa: 'C', fora: 'D', data: '2020-01-01T00:00:00Z', odds_over: '1.90', result_over25: '' };
    const picks = [bad, good];
    const ghWriteCalls = [];
    await syncResultsCore(
      [bad, good], picks, { textContent: '' },
      mockGet({ '10': { status: 'finished', home_score: null, away_score: 1 }, '11': { status: 'finished', home_score: 2, away_score: 1 } }),
      noopPinnacleRow, noopCalcCLV,
      async (...a) => ghWriteCalls.push(a),
      'data/picks.json', validMatchScore,
    );
    assert.equal(bad.result_over25, '', 'pick inválido não deve settlar');
    assert.equal(good.result_over25, 'WIN', 'pick válido no mesmo lote deve settlar normalmente');
    assert.equal(ghWriteCalls.length, 1, 'ghWrite deve correr uma vez, reflectindo só o pick válido');
  });

  await_check('silentSync(): lote misto (1 inválido + 1 válido) processa ambos', async () => {
    const bad = { id: '20', data: '2020-01-01T00:00:00Z', odds_over: '1.90', result_over25: '' };
    const good = { id: '21', data: '2020-01-01T00:00:00Z', odds_over: '1.90', result_over25: '' };
    const picks = [bad, good];
    const ghWriteCalls = [];
    await silentSyncCore(
      picks,
      mockGet({ '20': { status: 'finished', home_score: 'abc', away_score: 1 }, '21': { status: 'finished', home_score: 1, away_score: 1 } }),
      noopPinnacleRow, noopCalcCLV,
      async (...a) => ghWriteCalls.push(a),
      'data/picks.json', validMatchScore,
      noopEl, noopRenderDash, identityNormalizePick,
    );
    assert.equal(bad.result_over25, '', 'pick inválido não deve settlar');
    assert.equal(good.result_over25, 'LOSS', 'pick válido no mesmo lote deve settlar normalmente');
    assert.equal(ghWriteCalls.length, 1);
  });

  finish();
}

// ── runner mínimo p/ testes assíncronos dentro de uma função main() síncrona ─
const pending = [];
function await_check(desc, asyncFn) {
  checks++;
  pending.push(
    asyncFn().catch(err => failures.push(`  ✗ ${desc}: ${err.message}`))
  );
}
async function finish() {
  await Promise.all(pending);
  const passed = checks - failures.length;
  console.log(`over25 score integrity: ${passed}/${checks} verificações OK`);
  if (failures.length) {
    console.error('Falhas:');
    console.error(failures.join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main();
