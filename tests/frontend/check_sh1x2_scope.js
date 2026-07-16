// Headless regression check for the Sharp 1X2 sessionStorage cache (index.html, loadAll()).
//
// Bug it guards against (16 jul 2026): sh1x2 / sharpEventsMatched / sharpEventsSkippedTime
// were declared inside the cache-miss branch, but consumed later in loadAll() to build the
// #dbgSharp debug line — out of scope in BOTH the cache-hit and cache-miss paths, throwing
// "sh1x2 is not defined" and surfacing as "Erro ao carregar" for every real page load.
//
// This test mocks the BSD API so loadAll() runs end-to-end without real network access, then
// exercises both branches explicitly:
//   - cache-miss (first load): asserts no crash, #dbgSharp renders, and exactly one request
//     hits the per-event market=1x2 odds endpoint.
//   - cache-hit (fresh sessionStorage cache pre-seeded): asserts no crash, #dbgSharp renders,
//     and ZERO requests hit market=1x2 — proving the cache is genuinely used, not bypassed.
//
// Requires: node with Playwright available (PLAYWRIGHT_BROWSERS_PATH set, or default install),
// and index.html served over http (file:// breaks fetch()). Example:
//   python3 -m http.server 8934 &
//   node tests/frontend/check_sh1x2_scope.js
//
// Not part of the Python pytest suite (this repo's front-end has no build/test tooling per
// CLAUDE.md) — run manually or wire into a JS-capable CI step if one is added later.

function requirePlaywright() {
  try { return require('playwright'); } catch (e) {}
  // Fall back to a global install (e.g. NODE_PATH not set) instead of failing outright.
  for (const p of ['/opt/node22/lib/node_modules/playwright', '/usr/lib/node_modules/playwright']) {
    try { return require(p); } catch (e) {}
  }
  throw new Error('playwright not found — install it or set NODE_PATH to its location');
}
const { chromium } = requirePlaywright();

const BASE_URL = process.env.SH1X2_TEST_URL || 'http://localhost:8934/index.html';

const fakeEvent = {
  id: 111111,
  league_id: 1, // Premier League -> whitelisted
  league_name: 'Premier League',
  home_team: 'Home FC',
  away_team: 'Away FC',
  event_date: new Date(Date.now() + 3 * 3600000).toISOString(), // 3h out, within the 36h Sharp window
  status: 'notstarted',
};

function jsonRoute(route, body) {
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockCommonRoutes(page, { allowMarket1x2 }) {
  let market1x2Calls = 0;
  await page.route('**/sports.bzzoiro.com/api/v2/**', (route) => {
    const url = route.request().url();
    if (url.includes('market=1x2')) {
      market1x2Calls++;
      if (!allowMarket1x2) { route.abort(); return; }
      return jsonRoute(route, [
        { event_id: fakeEvent.id, outcome: 'home', bookmaker_slug: 'pinnacle', decimal_odds: '1.80', previous_decimal_odds: '1.90', movement: 'SHORTENING' },
        { event_id: fakeEvent.id, outcome: 'away', bookmaker_slug: 'pinnacle', decimal_odds: '4.20', previous_decimal_odds: '4.20' },
        { event_id: fakeEvent.id, outcome: 'home', bookmaker_slug: 'bet365', decimal_odds: '1.95', previous_decimal_odds: '2.00', movement: 'SHORTENING' },
      ]);
    }
    if (url.includes('/api/v2/events/') && !url.includes('event_id') && !url.match(/\/events\/\d+\/$/)) {
      return jsonRoute(route, [fakeEvent]); // both the 30h and 7-day event list windows
    }
    if (url.match(/\/api\/v2\/events\/\d+\/$/)) {
      return jsonRoute(route, {}); // event detail
    }
    return jsonRoute(route, []); // predictions, over/under odds, everything else
  });
  return () => market1x2Calls;
}

async function run(label, { seedCache }) {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  const getMarket1x2Calls = await mockCommonRoutes(page, { allowMarket1x2: !seedCache });

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });

  await page.evaluate(({ seedCache, fakeEvent }) => {
    localStorage.setItem('ov_cfg', JSON.stringify({ k: 'FAKEKEY123' }));
    document.getElementById('bsdKey').value = 'FAKEKEY123';
    if (seedCache) {
      sessionStorage.setItem('ov_sharp_cache_v1', JSON.stringify({
        ts: Date.now(),
        data: [{
          id: fakeEvent.id, home: fakeEvent.home_team, away: fakeEvent.away_team,
          date: fakeEvent.event_date, league: 'Premier League',
          sigs: [{ out: 'HOME', label: 'SHARP', score: 40 }],
          totalScore: 40, hoursToKO: 3, label: 'SHARP', scanScore: 0,
        }],
        sh1x2: { [fakeEvent.id]: { HOME: { pin: 1.8 } } },
        sharpEventsMatched: 1,
        sharpEventsSkippedTime: 0,
      }));
    }
  }, { seedCache, fakeEvent });

  await page.evaluate(() => loadAll());
  await page.waitForTimeout(500);

  const scanListText = await page.evaluate(() => document.getElementById('scanList')?.textContent || '');
  const dbgSharpText = await page.evaluate(() => document.getElementById('dbgSharp')?.textContent || '');
  const market1x2Calls = getMarket1x2Calls();
  await browser.close();

  console.log(`--- ${label} ---`);
  console.log('pageerrors:', errors.length ? errors.join(' | ') : '(none)');
  console.log('dbgSharp:', JSON.stringify(dbgSharpText));
  console.log('market=1x2 requests made:', market1x2Calls);

  const sh1x2Crash = errors.some((e) => /sh1x2 is not defined/.test(e)) || /sh1x2 is not defined/.test(scanListText);
  return { errors, sh1x2Crash, dbgSharpText, market1x2Calls };
}

(async () => {
  const miss = await run('CACHE-MISS (first load)', { seedCache: false });
  const hit = await run('CACHE-HIT (fresh sessionStorage cache)', { seedCache: true });

  console.log('\n=== VERDICT ===');
  let ok = true;
  if (miss.sh1x2Crash) { ok = false; console.log('FAIL: cache-miss path threw "sh1x2 is not defined"'); }
  if (!miss.dbgSharpText) { ok = false; console.log('FAIL: cache-miss path did not populate #dbgSharp'); }
  if (miss.market1x2Calls < 1) { ok = false; console.log('FAIL: cache-miss path made no market=1x2 requests (expected 1 per event)'); }

  if (hit.sh1x2Crash) { ok = false; console.log('FAIL: cache-hit path threw "sh1x2 is not defined"'); }
  if (!hit.dbgSharpText) { ok = false; console.log('FAIL: cache-hit path did not populate #dbgSharp'); }
  if (hit.market1x2Calls !== 0) { ok = false; console.log(`FAIL: cache-hit path made ${hit.market1x2Calls} market=1x2 requests (expected 0 — cache should skip the fetch loop)`); }

  console.log(ok ? 'PASS: both paths clean, cache-hit genuinely skips per-event fetch' : 'FAIL: see above');
  process.exit(ok ? 0 : 1);
})();
