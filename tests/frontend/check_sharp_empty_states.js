// Headless check for the Sharp 1X2 empty-state distinction (index.html, renderSharp()).
//
// Ties directly to the P1 fetch-storm fix (whitelist + <=36h + sessionStorage cache) and to
// the earlier sh1x2-scope regression, which specifically escaped review on the cache-hit
// path — so this test exercises BOTH empty-state cases across BOTH cache paths (4 runs):
//   Case A — zero events survive the whitelist+36h filter (off-season): expects the
//            "Sem jogos das ligas whitelisted..." message with the scanner/eligible counts.
//   Case B — events survive the filter but none produced a signal: expects the
//            "N jogos elegíveis analisados..." message.
//   x cache-miss (first load, computed fresh) and cache-hit (fresh sessionStorage cache).
//
// Requires: node with Playwright available, index.html served over http. Example:
//   python3 -m http.server 8934 &
//   node tests/frontend/check_sharp_empty_states.js

function requirePlaywright() {
  try { return require('playwright'); } catch (e) {}
  for (const p of ['/opt/node22/lib/node_modules/playwright', '/usr/lib/node_modules/playwright']) {
    try { return require(p); } catch (e) {}
  }
  throw new Error('playwright not found — install it or set NODE_PATH to its location');
}
const { chromium } = requirePlaywright();

const BASE_URL = process.env.SH1X2_TEST_URL || 'http://localhost:8934/index.html';

// Case A: no whitelisted-league event in the events7 pool at all -> events7Sharp filter yields 0.
const offSeasonEvent = {
  id: 900001,
  league_id: 999, // not in BSD_LEAGUE_ID_MAP -> not whitelisted
  league_name: 'Some Summer Cup',
  home_team: 'Foo FC',
  away_team: 'Bar FC',
  event_date: new Date(Date.now() + 3 * 3600000).toISOString(),
  status: 'notstarted',
};

// Case B: a whitelisted-league event within the window, but odds show no meaningful movement
// (no previous_decimal_odds / no divergence) -> passes the filter, produces zero signals.
const quietEvent = {
  id: 900002,
  league_id: 1, // Premier League -> whitelisted
  league_name: 'Premier League',
  home_team: 'Home FC',
  away_team: 'Away FC',
  event_date: new Date(Date.now() + 3 * 3600000).toISOString(),
  status: 'notstarted',
};

const scannerFillerEvents = Array.from({ length: 5 }, (_, i) => ({
  id: 800000 + i,
  league_id: 999,
  league_name: 'Filler League',
  home_team: 'X' + i,
  away_team: 'Y' + i,
  event_date: new Date(Date.now() + 5 * 3600000).toISOString(),
  status: 'notstarted',
}));

function jsonRoute(route, body) {
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockRoutes(page, { caseName, allowMarket1x2 }) {
  const events7 = caseName === 'A' ? [offSeasonEvent, ...scannerFillerEvents] : [quietEvent, ...scannerFillerEvents];
  let market1x2Calls = 0;
  await page.route('**/sports.bzzoiro.com/api/v2/**', (route) => {
    const url = route.request().url();
    if (url.includes('market=1x2')) {
      market1x2Calls++;
      if (!allowMarket1x2) { route.abort(); return; }
      if (caseName === 'B') {
        // Quiet market: no previous_decimal_odds -> pinnMove null -> score 0 -> no signal pushed.
        return jsonRoute(route, [
          { event_id: quietEvent.id, outcome: 'home', bookmaker_slug: 'pinnacle', decimal_odds: '1.90' },
          { event_id: quietEvent.id, outcome: 'away', bookmaker_slug: 'pinnacle', decimal_odds: '4.00' },
        ]);
      }
      return jsonRoute(route, []);
    }
    if (url.includes('/api/v2/events/') && !url.includes('event_id') && !url.match(/\/events\/\d+\/$/)) {
      return jsonRoute(route, events7); // both 30h and 7-day windows use the same mock list here
    }
    if (url.match(/\/api\/v2\/events\/\d+\/$/)) return jsonRoute(route, {});
    return jsonRoute(route, []);
  });
  return () => market1x2Calls;
}

async function run(label, { caseName, seedCache }) {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  const getMarket1x2Calls = await mockRoutes(page, { caseName, allowMarket1x2: !seedCache });

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => {
    localStorage.setItem('ov_cfg', JSON.stringify({ k: 'FAKEKEY123' }));
    document.getElementById('bsdKey').value = 'FAKEKEY123';
  });

  if (seedCache) {
    await page.evaluate(({ caseName, quietEvent }) => {
      if (caseName === 'A') {
        sessionStorage.setItem('ov_sharp_cache_v1', JSON.stringify({
          ts: Date.now(), data: [], sh1x2: {}, sharpEventsMatched: 0, sharpEventsSkippedTime: 0, eligibleCount: 0,
        }));
      } else {
        sessionStorage.setItem('ov_sharp_cache_v1', JSON.stringify({
          ts: Date.now(), data: [], sh1x2: { [quietEvent.id]: { HOME: { pin: 1.9 } } },
          sharpEventsMatched: 1, sharpEventsSkippedTime: 0, eligibleCount: 1,
        }));
      }
    }, { caseName, quietEvent });
  }

  await page.evaluate(() => loadAll());
  await page.waitForTimeout(500);

  const sharpListText = await page.evaluate(() => document.getElementById('sharpList')?.textContent || '');
  const market1x2Calls = getMarket1x2Calls();
  await browser.close();

  console.log(`--- ${label} ---`);
  console.log('pageerrors:', errors.length ? errors.join(' | ') : '(none)');
  console.log('sharpList:', JSON.stringify(sharpListText.replace(/\s+/g, ' ').trim()));
  console.log('market=1x2 requests:', market1x2Calls);

  return { errors, sharpListText, market1x2Calls };
}

(async () => {
  const results = {};
  results.A_miss = await run('CASE A (zero eligible) — cache-miss', { caseName: 'A', seedCache: false });
  results.A_hit  = await run('CASE A (zero eligible) — cache-hit',  { caseName: 'A', seedCache: true });
  results.B_miss = await run('CASE B (eligible, no signal) — cache-miss', { caseName: 'B', seedCache: false });
  results.B_hit  = await run('CASE B (eligible, no signal) — cache-hit',  { caseName: 'B', seedCache: true });

  console.log('\n=== VERDICT ===');
  let ok = true;
  const noErrors = (r, label) => { if (r.errors.length) { ok = false; console.log(`FAIL: ${label} threw: ${r.errors.join(' | ')}`); } };

  for (const [key, r] of Object.entries(results)) noErrors(r, key);

  if (!/whitelisted.*36h|36h.*whitelisted|Sem jogos das ligas whitelisted/i.test(results.A_miss.sharpListText)) {
    ok = false; console.log('FAIL: Case A (cache-miss) did not show the off-season empty message');
  }
  if (!/elegíveis para Sharp 1X2/i.test(results.A_miss.sharpListText)) {
    ok = false; console.log('FAIL: Case A (cache-miss) missing the scanner/eligible count context');
  }
  if (!/Sem jogos das ligas whitelisted/i.test(results.A_hit.sharpListText)) {
    ok = false; console.log('FAIL: Case A (cache-hit) did not show the off-season empty message');
  }

  if (!/jogos elegíveis analisados/i.test(results.B_miss.sharpListText)) {
    ok = false; console.log('FAIL: Case B (cache-miss) did not show the "eligible, no signal" message');
  }
  if (!/jogos elegíveis analisados/i.test(results.B_hit.sharpListText)) {
    ok = false; console.log('FAIL: Case B (cache-hit) did not show the "eligible, no signal" message');
  }

  if (results.A_hit.market1x2Calls !== 0) { ok = false; console.log('FAIL: Case A cache-hit still made market=1x2 requests'); }
  if (results.B_hit.market1x2Calls !== 0) { ok = false; console.log('FAIL: Case B cache-hit still made market=1x2 requests'); }

  console.log(ok ? 'PASS: both empty-state cases render correctly across cache-miss and cache-hit' : 'FAIL: see above');
  process.exit(ok ? 0 : 1);
})();
