#!/usr/bin/env node
// tests/js/test_classify_odds.mjs
// -----------------------------------------------------------------------
// Valida classifyOdds() (index.html) contra a mesma spec que
// tests/pipeline/test_scan_common.py usa para classify_odds() (Python).
//
// Não reimplementa a lógica: extrai o bloco literal de index.html entre os
// marcadores ODDS_CLASSIFICATION_JS:START/END e corre-o num sandbox Node
// (vm) — por isso qualquer edição a classifyOdds() em index.html sem
// actualizar a spec/comportamento parte este teste. Ver docs/odds_validation.md
// pela justificação de manter duas implementações (Python + JS) em vez de
// uma só fonte server-side.
//
// Uso: node tests/js/test_classify_odds.mjs
// Sem dependências externas — só módulos nativos do Node.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');

const INDEX_HTML_PATH = join(REPO_ROOT, 'index.html');
const SPEC_PATH = join(REPO_ROOT, 'tests', 'fixtures', 'odds_classification_spec.json');

const START_MARKER = '// ODDS_CLASSIFICATION_JS:START';
const END_MARKER = '// ODDS_CLASSIFICATION_JS:END';

function extractClassifyOddsSource(html) {
  const startIdx = html.indexOf(START_MARKER);
  const endIdx = html.indexOf(END_MARKER);
  if (startIdx === -1 || endIdx === -1 || endIdx < startIdx) {
    throw new Error(
      `Não encontrei os marcadores ${START_MARKER} / ${END_MARKER} em index.html — ` +
      'o bloco classifyOdds() foi movido ou renomeado sem actualizar este teste.'
    );
  }
  return html.slice(startIdx + START_MARKER.length, endIdx);
}

function loadClassifyOdds() {
  const html = readFileSync(INDEX_HTML_PATH, 'utf8');
  const source = extractClassifyOddsSource(html);
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(source + '\nthis.classifyOdds = classifyOdds;', sandbox);
  if (typeof sandbox.classifyOdds !== 'function') {
    throw new Error('classifyOdds não foi definida pelo bloco extraído de index.html.');
  }
  return sandbox.classifyOdds;
}

function main() {
  const classifyOdds = loadClassifyOdds();
  const spec = JSON.parse(readFileSync(SPEC_PATH, 'utf8'));

  let passed = 0;
  const failures = [];

  for (const c of spec.cases) {
    const result = classifyOdds(c.raw_odds, c.market_status);
    try {
      assert.equal(result.status, c.expected_status, `status (got ${result.status})`);
      assert.equal(result.value, c.expected_value, `value (got ${result.value})`);
      passed++;
    } catch (err) {
      failures.push(`  ✗ ${c.name}: ${err.message}`);
    }
  }

  console.log(`classifyOdds: ${passed}/${spec.cases.length} casos OK`);
  if (failures.length) {
    console.error('Falhas:');
    console.error(failures.join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main();
