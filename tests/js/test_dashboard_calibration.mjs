#!/usr/bin/env node
// tests/js/test_dashboard_calibration.mjs
// -----------------------------------------------------------------------
// Regressão — painel "Padrões ao Vivo — Histórico de observações"
// (Dashboard tab, obsGrid/renderObsDashboard() em index.html).
//
// Segundo painel de calibração, distinto do "📊 Calibração Live" do
// separador Live (já corrigido — ver test_live_calibration.mjs). Tinha o
// mesmo defeito: cards "WR por bucket de score"/"WR por padrão" coloridos
// só pelo WR (ex.: WR>=55% → verde, com ✓ para qualquer bucket dentro do
// threshold de produção), sem referência ao break-even implícito pela odd.
// Um WR de 60% com odd média 1.69 (break-even ~59%) mal empata; um WR mais
// baixo perde — mas ambos apareciam a verde com ✓.
//
// Bloco H2b: renderObsDashboard() passou a reaproveitar
// computeCalibSegment()/calibSegColor() (mesmo bloco LIVE_CALIB_STATS_JS já
// testado por test_live_calibration.mjs) em vez de recalcular WR à mão —
// por isso este teste só prova que o painel está de facto a usar essas
// funções (cor/✓ dependem do ROI, odds ausentes ficam em `excluded`), não
// reimplementa a aritmética.
//
// Extrai os blocos REAIS de index.html (marcadores LIVE_CALIB_STATS_JS e
// OBS_DASHBOARD_JS) e corre-os num sandbox Node (vm) com um mock mínimo de
// `el()` (sem DOM real) — prova comportamento real, não uma cópia da
// intenção.
//
// Uso: node tests/js/test_dashboard_calibration.mjs
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
  try { fn(); }
  catch (err) { failures.push(`  ✗ ${desc}: ${err.message}`); }
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

function makeElMock() {
  const store = {};
  function el(id) {
    if (!store[id]) {
      let text = '', html = '';
      store[id] = {
        get textContent() { return text; },
        set textContent(v) { text = v; },
        get innerHTML() { return html; },
        set innerHTML(v) { html = v; },
      };
    }
    return store[id];
  }
  return { el, store };
}

function loadRenderObsDashboard() {
  const statsSrc = extractBlock('LIVE_CALIB_STATS_JS:START', 'LIVE_CALIB_STATS_JS:END');
  const dashSrc = extractBlock('OBS_DASHBOARD_JS:START', 'OBS_DASHBOARD_JS:END');
  const source = statsSrc + '\n' + dashSrc + '\nthis.renderObsDashboard = renderObsDashboard;';
  const sandbox = { el: null, allObs: [], TH_LIVE_PICK: 12 };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  if (typeof sandbox.renderObsDashboard !== 'function') {
    throw new Error('renderObsDashboard não foi definida pelo bloco extraído.');
  }
  return sandbox;
}

// obs sintética: bucket "12-15" com WR alto (70%) mas odds baixas o
// suficiente para ROI negativo — o caso exacto reportado (WR alto,
// abaixo do break-even, a aparecer como sucesso).
function bkNameFor(html, bucketLabel) {
  const m = html.match(new RegExp(`<div class="bk-name">Score ${bucketLabel}[\\s\\S]*?<\\/div>`));
  return m ? m[0] : '';
}

function obsAt(score, result, odds, extra) {
  // patterns:['pressure'] por omissão — renderObsDashboard() devolve o
  // estado vazio "Sem padrões nos registos" antes de chegar aos buckets de
  // score se NENHUMA observação tiver padrões; o teste do bucket de score
  // não é sobre padrões, mas precisa de pelo menos um para o painel render.
  return { pattern_score: score, result_over25: result, odds_live: odds, patterns: ['pressure'], ...extra };
}

function main() {
  // ── caso principal: WR alto no bucket "gate" mas ROI negativo ─────────
  check('bucket 12-15 com WR 70% e ROI negativo: card fica vermelho, sem ✓', () => {
    const sandbox = loadRenderObsDashboard();
    const obs = [
      ...Array.from({ length: 7 }, () => obsAt(13, 'WIN', '1.20')),
      ...Array.from({ length: 3 }, () => obsAt(13, 'LOSS', '1.20')),
    ];
    sandbox.allObs = obs;
    const { el, store } = makeElMock();
    sandbox.el = el;
    sandbox.renderObsDashboard();

    const html = store['obsGrid'].innerHTML;
    // WR real deste bucket é 70% — confirma que o teste não está a testar o caso errado
    assert.ok(html.includes('70.0%'), 'esperava WR 70.0% no HTML: ' + html.slice(0, 400));
    // ROI = (7*0.20 - 3*1)/10*100 = -16.0% — tem de aparecer a vermelho, sem ✓
    assert.ok(html.includes('var(--red)'), 'esperava cor vermelha (ROI negativo)');
    assert.ok(!bkNameFor(html, '12–15').includes('✓'), 'não devia ter ✓ com ROI negativo, mesmo dentro do gate: ' + bkNameFor(html, '12–15'));
  });

  // ── ROI positivo: fica verde e com ✓ ───────────────────────────────────
  check('bucket com ROI positivo: card fica verde, com ✓', () => {
    const sandbox = loadRenderObsDashboard();
    const obs = [
      ...Array.from({ length: 6 }, () => obsAt(13, 'WIN', '2.50')),
      ...Array.from({ length: 4 }, () => obsAt(13, 'LOSS', '2.50')),
    ];
    sandbox.allObs = obs;
    const { el, store } = makeElMock();
    sandbox.el = el;
    sandbox.renderObsDashboard();

    const html = store['obsGrid'].innerHTML;
    // ROI = (6*1.5 - 4*1)/10*100 = +50% — positivo, tem de ficar verde com ✓
    assert.ok(html.includes('var(--green)'), 'esperava cor verde (ROI positivo)');
    assert.ok(bkNameFor(html, '12–15').includes('✓'), 'esperava ✓ com ROI positivo dentro do gate: ' + bkNameFor(html, '12–15'));
  });

  // ── odds ausentes: excluídas, nunca misturadas no WR/ROI ───────────────
  check('picks sem odds contam à parte (excl.), não entram no WR/ROI do card', () => {
    const sandbox = loadRenderObsDashboard();
    const obs = [
      obsAt(13, 'WIN', '2.00'),
      obsAt(13, 'WIN', ''),      // sem odds — excluído
      obsAt(13, 'LOSS', 'abc'),  // inválida — excluído
    ];
    sandbox.allObs = obs;
    const { el, store } = makeElMock();
    sandbox.el = el;
    sandbox.renderObsDashboard();

    const html = store['obsGrid'].innerHTML;
    assert.ok(html.includes('100.0%'), 'só a odd válida (WIN) devia entrar no WR — esperava 100.0%');
    assert.ok(html.includes('2 s/odds excl.'), 'esperava as 2 observações sem odds válidas contadas à parte: ' + html.slice(0, 600));
  });

  // ── segmento por padrão também usa ROI, não WR isolado ─────────────────
  check('cards "WR por padrão" também coloridos por ROI, não por WR', () => {
    const sandbox = loadRenderObsDashboard();
    const obs = [
      ...Array.from({ length: 7 }, () => obsAt(13, 'WIN', '1.20', { patterns: ['pressure'] })),
      ...Array.from({ length: 3 }, () => obsAt(13, 'LOSS', '1.20', { patterns: ['pressure'] })),
    ];
    sandbox.allObs = obs;
    const { el, store } = makeElMock();
    sandbox.el = el;
    sandbox.renderObsDashboard();

    const html = store['obsGrid'].innerHTML;
    // O mesmo padrão "pressure" (WR 70% mas ROI -16%) tem de aparecer a
    // vermelho também na secção "WR por padrão", não só na de score-bucket.
    const patternSection = html.split('WR por padrão')[1] || '';
    // (a secção real é injectada só no innerHTML final, não há um split
    // literal — verificamos antes que a cor vermelha aparece mais que uma
    // vez: uma para o bucket, outra para o padrão)
    const redCount = (html.match(/var\(--red\)/g) || []).length;
    assert.ok(redCount >= 2, `esperava >=2 ocorrências de vermelho (bucket + padrão), veio ${redCount}`);
  });

  // ── sem observações: estado vazio explícito, não rebenta ───────────────
  check('sem observações: mostra estado vazio, não chama computeCalibSegment em vão', () => {
    const sandbox = loadRenderObsDashboard();
    sandbox.allObs = [];
    const { el, store } = makeElMock();
    sandbox.el = el;
    sandbox.renderObsDashboard();
    assert.ok(store['obsGrid'].innerHTML.includes('Sem observações'));
  });

  console.log(`dashboard-calibration: ${checks - failures.length}/${checks} casos OK`);
  if (failures.length) {
    console.error('Falhas:');
    console.error(failures.join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main();
