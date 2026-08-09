#!/usr/bin/env node
// tests/js/test_sharp1x2_settlement_authority.mjs
// -----------------------------------------------------------------------
// Regressão estrutural — Settlement Bug 1 (auditoria de continuidade,
// 9 ago 2026): existiam DOIS escritores não coordenados de
// resultado_outcome/resultado_jogo/settled_at em data/picks_1x2.json —
// pipeline/settle_sharp1x2.py (servidor, autoridade) e index.html
// (silentSync() parte 3 + syncResults1x2()). O retry 409 de ghWrite()
// reenvia o array tal como estava em memória, sem se fundir com o
// conteúdo actual — por isso um conflito com o servidor podia apagar
// silenciosamente resultado_outcome/settled_at recém-gravados.
//
// Correcção: o browser deixou de escrever settlement de Sharp 1X2. Este
// teste não corre as funções num sandbox (exigiria mockar demasiados
// globais — ghRead/get/el/renderDash1x2/sendTG/etc.) — em vez disso prova
// estruturalmente, a partir do bloco literal extraído de index.html
// (mesma técnica de tests/js/test_classify_odds.mjs), que:
//   1. nenhum dos dois pontos chama ghWrite(PICKS1X2_FILE, ...);
//   2. nenhum dos dois pontos atribui a resultado_outcome/resultado_jogo;
//   3. nenhum dos dois pontos faz fetch a /api/v2/events/ (só o servidor
//      decide o resultado de um evento);
//   4. a leitura (ghRead) continua presente — prova que a correcção foi
//      "parar de escrever", não "partir a funcionalidade de leitura".
//
// Qualquer edição futura a index.html que reintroduza escrita de
// settlement Sharp 1X2 nestes dois pontos parte este teste.
//
// Uso: node tests/js/test_sharp1x2_settlement_authority.mjs
// Sem dependências externas — só módulos nativos do Node.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');
const INDEX_HTML_PATH = join(REPO_ROOT, 'index.html');

const BLOCKS = [
  {
    name: 'silentSync() — bloco Sharp 1X2 (parte 3)',
    startMarker: '// SHARP1X2_SETTLEMENT_JS:START',
    endMarker: '// SHARP1X2_SETTLEMENT_JS:END',
  },
  {
    name: 'syncResults1x2()',
    startMarker: '// SYNCRESULTS1X2_JS:START',
    endMarker: '// SYNCRESULTS1X2_JS:END',
  },
];

function extractBlock(html, { name, startMarker, endMarker }) {
  const startIdx = html.indexOf(startMarker);
  const endIdx = html.indexOf(endMarker);
  if (startIdx === -1 || endIdx === -1 || endIdx < startIdx) {
    throw new Error(
      `Não encontrei os marcadores ${startMarker} / ${endMarker} em index.html ` +
      `(bloco "${name}") — foi movido ou renomeado sem actualizar este teste.`
    );
  }
  return html.slice(startIdx + startMarker.length, endIdx);
}

function main() {
  const html = readFileSync(INDEX_HTML_PATH, 'utf8');
  const failures = [];
  let checks = 0;

  for (const blockSpec of BLOCKS) {
    const src = extractBlock(html, blockSpec);
    const label = blockSpec.name;

    // 1. Nunca escreve o ficheiro de picks 1X2.
    checks++;
    try {
      assert.ok(
        !/ghWrite\s*\(\s*PICKS1X2_FILE/.test(src),
        `${label}: chama ghWrite(PICKS1X2_FILE, ...) — voltou a ser um escritor de settlement`
      );
    } catch (err) { failures.push('  ✗ ' + err.message); }

    // 2. Nunca atribui a resultado_outcome / resultado_jogo (só pode ler).
    checks++;
    try {
      assert.ok(
        !/resultado_outcome\s*=\s*(?!=)/.test(src),
        `${label}: atribui a resultado_outcome (assinatura de escrita de settlement)`
      );
    } catch (err) { failures.push('  ✗ ' + err.message); }
    checks++;
    try {
      assert.ok(
        !/resultado_jogo\s*=\s*(?!=)/.test(src),
        `${label}: atribui a resultado_jogo (assinatura de escrita de settlement)`
      );
    } catch (err) { failures.push('  ✗ ' + err.message); }

    // 3. Nunca consulta o resultado do evento na BSD — isso é decisão
    //    exclusiva do servidor (pipeline/settle_sharp1x2.py).
    checks++;
    try {
      assert.ok(
        !/\/api\/v2\/events\//.test(src),
        `${label}: faz fetch a /api/v2/events/ — voltou a decidir settlement no browser`
      );
    } catch (err) { failures.push('  ✗ ' + err.message); }

    // 4. Sanidade: a leitura tem de continuar — prova que não ficou morto.
    checks++;
    try {
      assert.ok(
        /ghRead\s*\(\s*PICKS1X2_FILE\s*\)/.test(src),
        `${label}: já não lê PICKS1X2_FILE — deixou de reflectir o settlement do servidor`
      );
    } catch (err) { failures.push('  ✗ ' + err.message); }
  }

  const passed = checks - failures.length;
  console.log(`sharp1x2 settlement authority: ${passed}/${checks} verificações OK`);
  if (failures.length) {
    console.error('Falhas:');
    console.error(failures.join('\n'));
    process.exit(1);
  }
  process.exit(0);
}

main();
