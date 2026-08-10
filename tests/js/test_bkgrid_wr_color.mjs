#!/usr/bin/env node
// tests/js/test_bkgrid_wr_color.mjs
// -----------------------------------------------------------------------
// Regressão — cards "bkGrid" do dashboard Over 2.5 (renderDash(), index.html):
// o rótulo WR era sempre pintado a verde (style="color:var(--green)"),
// independentemente do valor — só o card ROI ao lado estava correctamente
// colorido. Um filtro com WR alto mas ROI negativo mostrava as duas cores
// lado a lado: WR sempre verde, ROI vermelho — inconsistente e enganador
// no mesmo padrão de "WR verde como falso sucesso" do Bloco H2/H2b.
//
// Correcção: WR passa a usar a mesma `roiCol` já calculada para o ROI —
// nunca verde quando ROI é negativo.
//
// Extrai o bloco REAL _bkGridCard (marcadores BKGRID_CARD_JS:START/END) de
// index.html e corre-o num sandbox Node (vm) — não reimplementa a lógica.
//
// Uso: node tests/js/test_bkgrid_wr_color.mjs
// Sem dependências externas — só módulos nativos do Node.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');
const INDEX_HTML_PATH = join(REPO_ROOT, 'index.html');

const START_MARKER = '// BKGRID_CARD_JS:START';
const END_MARKER = '// BKGRID_CARD_JS:END';

function extractSource(html) {
  const si = html.indexOf(START_MARKER);
  const ei = html.indexOf(END_MARKER);
  if (si === -1 || ei === -1 || ei < si) {
    throw new Error(
      `Marcadores ${START_MARKER} / ${END_MARKER} não encontrados em index.html — ` +
      'o bloco _bkGridCard foi movido/renomeado sem actualizar este teste.'
    );
  }
  return html.slice(si + START_MARKER.length, ei);
}

function loadBkGridCard() {
  const html = readFileSync(INDEX_HTML_PATH, 'utf8');
  const source = extractSource(html);
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(source + '\nthis._bkGridCard = _bkGridCard;', sandbox);
  if (typeof sandbox._bkGridCard !== 'function') {
    throw new Error('_bkGridCard não foi definida pelo bloco extraído.');
  }
  return sandbox._bkGridCard;
}

// helper: n picks WIN a `oddsWin`, m picks LOSS
function group(wins, oddsWin, losses) {
  const g = [];
  for (let i = 0; i < wins; i++) g.push({ result_over25: 'WIN', odds_over: String(oddsWin) });
  for (let i = 0; i < losses; i++) g.push({ result_over25: 'LOSS', odds_over: String(oddsWin) });
  return g;
}

function wrColorOf(html) {
  const m = html.match(/<div class="bk-wr" style="color:(var\(--\w+\))">/);
  return m ? m[1] : null;
}
function roiColorOf(html) {
  const m = html.match(/<div class="bk-roi" style="color:(var\(--\w+\))">/);
  return m ? m[1] : null;
}

let checks = 0;
const failures = [];
function check(desc, fn) {
  checks++;
  try { fn(); }
  catch (err) { failures.push(`  ✗ ${desc}: ${err.message}`); }
}

function main() {
  const bkGridCard = loadBkGridCard();

  check('WR alto (70%) mas ROI negativo: WR nunca fica verde — usa a mesma cor do ROI', () => {
    // 7 WIN a odd 1.20 (+0.20 cada) + 3 LOSS = profit -1.6 em 10 → ROI -16%, WR 70%
    const grp = group(7, 1.20, 3);
    const html = bkGridCard(['teste', grp]);
    assert.ok(html.includes('70%'), 'esperava WR 70% no HTML: ' + html);
    const wrCol = wrColorOf(html);
    const roiCol = roiColorOf(html);
    assert.equal(wrCol, 'var(--red)', `WR devia ficar vermelho com ROI negativo, veio ${wrCol}`);
    assert.equal(wrCol, roiCol, 'WR e ROI têm de usar exactamente a mesma cor');
  });

  check('ROI claramente positivo (>=10%): WR e ROI ficam verdes, mesma cor', () => {
    const grp = group(6, 2.50, 4); // profit +5.0 em 10 → ROI +50%
    const html = bkGridCard(['teste', grp]);
    assert.equal(wrColorOf(html), 'var(--green)');
    assert.equal(wrColorOf(html), roiColorOf(html));
  });

  check('ROI entre 0 e 10%: WR e ROI ficam âmbar, mesma cor', () => {
    const grp = group(6, 1.75, 4); // 6*0.75=4.5 - 4 = 0.5 em 10 → ROI +5%
    const html = bkGridCard(['teste', grp]);
    assert.equal(wrColorOf(html), 'var(--amber)', 'ROI +5% devia ser âmbar');
    assert.equal(wrColorOf(html), roiColorOf(html));
  });

  check('grupo com <2 picks: sem card (comportamento inalterado)', () => {
    assert.equal(bkGridCard(['teste', [{ result_over25: 'WIN', odds_over: '2.0' }]]), '');
  });

  console.log(`bkgrid-wr-color: ${checks - failures.length}/${checks} casos OK`);
  if (failures.length) {
    console.error('Falhas:');
    console.error(failures.join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main();
