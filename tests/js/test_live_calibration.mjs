#!/usr/bin/env node
// tests/js/test_live_calibration.mjs
// -----------------------------------------------------------------------
// Regressão — painel "📊 Calibração Live" (Bloco H2, index.html).
//
// O painel antigo era texto estático ("Score≥12 → 46% WR", a verde) sem
// referência ao break-even implícito pela odd — um WR abaixo do break-even
// aparecia como validação positiva quando na verdade perde capital. Este
// teste extrai o bloco REAL computeCalibSegment()/calibSegColor() de
// index.html (marcadores LIVE_CALIB_STATS_JS:START/END, mesma técnica de
// test_classify_odds.mjs) e prova, com dados sintéticos:
//   1. n/WR/odd média/break-even/ROI calculados correctamente por segmento;
//   2. picks sem odds válidas NUNCA entram no cálculo — só contam em
//      `excluded`, nunca misturados no WR/ROI (requisito 5 do Bloco H2);
//   3. observações pendentes (sem result_over25) ficam de fora por completo;
//   4. a cor só depende do ROI — nunca do WR sozinho (requisito 2): um
//      segmento com WR alto mas ROI<=0 nunca é verde.
//
// Não reimplementa a lógica — corre o código real num sandbox Node (vm).
// Uso: node tests/js/test_live_calibration.mjs
// Sem dependências externas — só módulos nativos do Node.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');
const INDEX_HTML_PATH = join(REPO_ROOT, 'index.html');

const START_MARKER = '// LIVE_CALIB_STATS_JS:START';
const END_MARKER = '// LIVE_CALIB_STATS_JS:END';

function extractSource(html) {
  const si = html.indexOf(START_MARKER);
  const ei = html.indexOf(END_MARKER);
  if (si === -1 || ei === -1 || ei < si) {
    throw new Error(
      `Marcadores ${START_MARKER} / ${END_MARKER} não encontrados em index.html — ` +
      'o bloco computeCalibSegment()/calibSegColor() foi movido/renomeado sem actualizar este teste.'
    );
  }
  return html.slice(si + START_MARKER.length, ei);
}

function loadCalibStats() {
  const html = readFileSync(INDEX_HTML_PATH, 'utf8');
  const source = extractSource(html);
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(
    source + '\nthis.computeCalibSegment = computeCalibSegment;\nthis.calibSegColor = calibSegColor;',
    sandbox
  );
  if (typeof sandbox.computeCalibSegment !== 'function' || typeof sandbox.calibSegColor !== 'function') {
    throw new Error('computeCalibSegment/calibSegColor não foram definidas pelo bloco extraído.');
  }
  return { computeCalibSegment: sandbox.computeCalibSegment, calibSegColor: sandbox.calibSegColor };
}

let checks = 0;
const failures = [];
function check(desc, fn) {
  checks++;
  try { fn(); }
  catch (err) { failures.push(`  ✗ ${desc}: ${err.message}`); }
}

function main() {
  const { computeCalibSegment, calibSegColor } = loadCalibStats();

  // ── n/WR/odd média/break-even/ROI — caso misto (requisito 1) ──────────
  check('caso misto: n, wr, odd média, break-even, ROI calculados correctamente', () => {
    const obs = [
      { result_over25: 'WIN',  odds_live: '2.00' },
      { result_over25: 'WIN',  odds_live: '1.50' },
      { result_over25: 'LOSS', odds_live: '1.80' },
      { result_over25: 'LOSS', odds_live: '1.60' },
    ];
    const s = computeCalibSegment(obs);
    assert.equal(s.n, 4);
    assert.equal(s.excluded, 0);
    assert.equal(s.wr, 50);
    assert.ok(Math.abs(s.avgOdds - 1.725) < 1e-9, `avgOdds=${s.avgOdds}`);
    assert.ok(Math.abs(s.breakeven - (100 / 1.725)) < 1e-9, `breakeven=${s.breakeven}`);
    assert.ok(Math.abs(s.roi - (-12.5)) < 1e-9, `roi=${s.roi}`);
  });

  // ── ROI positivo (requisito 2 — cor) ───────────────────────────────────
  check('ROI positivo: segmento fica verde mesmo com WR=50%', () => {
    const obs = [
      { result_over25: 'WIN',  odds_live: '3.00' },
      { result_over25: 'LOSS', odds_live: '1.50' },
    ];
    const s = computeCalibSegment(obs);
    assert.equal(s.wr, 50); // WR sozinho não distingue este caso do anterior
    assert.ok(Math.abs(s.roi - 50) < 1e-9, `roi=${s.roi}`);
    assert.equal(calibSegColor(s), 'var(--green)');
  });

  // ── ROI negativo apesar de WR alto — nunca verde (requisito 2) ─────────
  check('WR alto mas abaixo do break-even: ROI negativo, cor nunca verde', () => {
    // odd média 1.69 (como nas observações reais citadas) — break-even ~59.2%.
    // WR 48% é "alto" à vista mas está abaixo do break-even: ROI tem de ser negativo.
    const obs = [
      { result_over25: 'WIN',  odds_live: '1.69' },
      { result_over25: 'WIN',  odds_live: '1.69' },
      { result_over25: 'LOSS', odds_live: '1.69' },
      { result_over25: 'LOSS', odds_live: '1.69' },
      { result_over25: 'LOSS', odds_live: '1.69' },
    ];
    const s = computeCalibSegment(obs);
    assert.equal(s.wr, 40);
    assert.ok(s.roi < 0, `esperava ROI negativo, veio ${s.roi}`);
    assert.equal(calibSegColor(s), 'var(--red)');
  });

  // ── ROI exactamente 0 — "só se positivo" exclui a fronteira ────────────
  check('ROI exactamente 0 não é verde (só ROI>0 é verde)', () => {
    // 1 WIN a odd 2.00 (+1.00) + 1 LOSS (-1.00) = profit 0 → ROI 0%.
    const obs = [
      { result_over25: 'WIN',  odds_live: '2.00' },
      { result_over25: 'LOSS', odds_live: '1.40' },
    ];
    const s = computeCalibSegment(obs);
    assert.ok(Math.abs(s.roi - 0) < 1e-9, `roi=${s.roi}`);
    assert.equal(calibSegColor(s), 'var(--red)');
  });

  // ── odds ausentes/inválidas nunca entram no cálculo (requisito 5) ─────
  check('odds ausentes/inválidas ficam em `excluded`, nunca misturadas no WR/ROI', () => {
    const obs = [
      { result_over25: 'WIN',  odds_live: '2.00' },  // válida
      { result_over25: 'WIN',  odds_live: '' },       // ausente
      { result_over25: 'LOSS', odds_live: 'abc' },     // não numérica
      { result_over25: 'WIN',  odds_live: '0' },       // <= MIN_VALID_ODDS
      { result_over25: 'LOSS', odds_live: '-1.5' },    // negativa
      { result_over25: 'LOSS', odds_live: null },      // null
    ];
    const s = computeCalibSegment(obs);
    assert.equal(s.n, 1, 'só a odd 2.00 deveria contar');
    assert.equal(s.excluded, 5);
    assert.equal(s.wr, 100); // WIN único que entrou
  });

  // ── observações pendentes (sem resultado) ficam de fora por completo ──
  check('observações pendentes não contam nem em n nem em excluded', () => {
    const obs = [
      { result_over25: 'WIN', odds_live: '2.00' },
      { result_over25: '',    odds_live: '1.80' },   // pendente
      { odds_live: '1.50' },                          // sem campo result_over25
    ];
    const s = computeCalibSegment(obs);
    assert.equal(s.n, 1);
    assert.equal(s.excluded, 0);
  });

  // ── segmento sem nenhum pick com odds válidas: nunca inventa um número ─
  check('segmento sem odds válidas: n=0, stats null, cor neutra', () => {
    const obs = [
      { result_over25: 'WIN',  odds_live: '' },
      { result_over25: 'LOSS', odds_live: 'x' },
    ];
    const s = computeCalibSegment(obs);
    assert.equal(s.n, 0);
    assert.equal(s.excluded, 2);
    assert.equal(s.wr, null);
    assert.equal(s.avgOdds, null);
    assert.equal(s.breakeven, null);
    assert.equal(s.roi, null);
    assert.equal(calibSegColor(s), 'var(--text3)');
  });

  // ── lista vazia ─────────────────────────────────────────────────────────
  check('lista vazia não rebenta e devolve n=0', () => {
    const s = computeCalibSegment([]);
    assert.equal(s.n, 0);
    assert.equal(s.excluded, 0);
  });

  console.log(`live-calibration: ${checks - failures.length}/${checks} casos OK`);
  if (failures.length) {
    console.error('Falhas:');
    console.error(failures.join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main();
