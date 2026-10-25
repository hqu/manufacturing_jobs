/* Browser plugin not available: reusable regular Playwright QA.
   Start the static server separately; this test never launches a server.
   NODE_PATH=/path/to/runtime/node_modules BROWSER_CHANNEL=chrome node tests/county_browser_qa.cjs
   Optional: BASE_URL, QA_OUTPUT_DIR, COUNTY_FIXTURES (validator --fixtures output).
*/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const base = process.env.BASE_URL || 'http://127.0.0.1:4173';
const out = process.env.QA_OUTPUT_DIR || '/tmp/county-dashboard-qa';
fs.mkdirSync(out, {recursive: true});
const fixtures = process.env.COUNTY_FIXTURES ? JSON.parse(fs.readFileSync(process.env.COUNTY_FIXTURES)) : null;
const results = {url: base, browser: process.env.BROWSER_CHANNEL || 'chromium', fallback: 'Browser plugin not available', checks: [], timings: {}, errors: [], warnings: []};
function check(name, value) { assert.ok(value, name); results.checks.push(name); }
(async () => {
  const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_CHANNEL ? {channel: process.env.BROWSER_CHANNEL} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    page.on('pageerror', e => results.errors.push(String(e)));
    page.on('console', m => {if (m.type() === 'error') results.errors.push(m.text()); if (m.type() === 'warning') results.warnings.push(m.text());});
    const started = Date.now();
    await page.goto(base, {waitUntil: 'load'});
    await page.locator('.leaflet-interactive').first().waitFor({timeout: 30000});
    await page.locator('#top-10-county-body tr').first().waitFor();
    results.timings.initialUsableMs = Date.now() - started;
    check('page identity', page.url().startsWith(base) && /manufactur|employment|county/i.test(await page.title()));
    check('meaningful screen', (await page.locator('body').innerText()).includes('Employment Count'));
    check('no framework error overlay', await page.locator('nextjs-portal, vite-error-overlay').count() === 0);
    check('source scope and default date', await page.evaluate(() => bundle.schemaVersion === 2 && bundle.counties.length === 3108 && allDates.length === 291 && currentDate === '03/2026'));
    check('county terminology', !/Commuting Zone|Employment Rate|Contiguous/i.test(await page.locator('body').innerText()));
    check('zoom controls fit within visible map frame', await page.evaluate(() => {
      const frame = document.querySelector('.map-wrap').getBoundingClientRect();
      const zoom = document.querySelector('.leaflet-control-zoom').getBoundingClientRect();
      return zoom.left >= frame.left && zoom.right <= frame.right;
    }));
    check('chart date ticks do not overlap', await page.evaluate(() => {
      const ticks = [...document.querySelector('.national-trend-chart').querySelectorAll('text')]
        .filter(el => Number(el.getAttribute('y')) === 106).map(el => el.getBoundingClientRect());
      return ticks.every((tick, index) => index === 0 || tick.left >= ticks[index - 1].right + 2);
    }));
    await page.screenshot({path: path.join(out, 'desktop.png'), fullPage: true});
    check('trend SVG restarts after unavailable point', await page.evaluate(() => {
      const path = buildNationalTrendPathFromGeometry([{x: 0, y: 1, value: 1}, {x: 1, y: 0, value: null}, {x: 2, y: 3, value: 3}]);
      return (path.match(/M/g) || []).length === 2 && !path.includes('L');
    }));
    check('zero denominator is unavailable; observed sector zero remains zero', await page.evaluate(() => {
      const original = bundle.values[0][0];
      try {
        bundle.values[0][0] = [0, 0, null, 0];
        if (getValue(bundle.counties[0].id, allDates[0], 'emplvl31_33', true) !== null) return false;
        if (getValue(bundle.counties[0].id, allDates[0], 'emplvl31_33', false) !== 0) return false;
        bundle.values[0][0] = [10, 0, null, 0];
        return getValue(bundle.counties[0].id, allDates[0], 'emplvl31_33', true) === 0 && getValue(bundle.counties[0].id, allDates[0], 'emplvl334', false) === null;
      } finally {bundle.values[0][0] = original;}
    }));
    // Exercise the real polygon path before controls and Reset: a pending CSS zoom
    // used to finish after Reset and restore the old county zoom with no pin.
    await page.evaluate(() => findLayerByCounty('01001')._path.setAttribute('data-qa-county', '01001'));
    const autauga = page.locator('[data-qa-county="01001"]');
    await autauga.hover();
    await autauga.click();
    await page.mouse.move(0, 0);
    check('real county polygon click pins Autauga', await page.evaluate(() => pinnedCounty === '01001' && getInfoFocusCounty() === '01001'));
    await page.locator('input[name=metric][value="emplvl336"]').check();
    await page.locator('[data-display-metric="percent"]').click();
    await page.locator('#year-select').selectOption('2002');
    check('polygon selection survives metric, share and date changes', await page.evaluate(() => pinnedCounty === '01001' && currentMetric === 'emplvl336' && currentDisplayMetric === 'percent' && currentDate.endsWith('/2002')));
    await page.locator('#map-reset-btn').click();
    await page.mouse.move(0, 0);
    await page.waitForFunction(() => pinnedCounty === null && hoveredCounty === null && map.getZoom() === fullViewZoom && isFullView && !map._animatingZoom);
    check('real polygon/control/reset sequence restores stable overview', await page.evaluate(() => map.getZoom() === fullViewZoom && pinnedCounty === null && hoveredCounty === null && isFullView));
    check('polygon reset restores aggregate chart', /Reported county employment/.test(await page.locator('#county-info-line').innerText()));
    await page.locator('#month-slider').evaluate(el => {el.value = el.max; el.dispatchEvent(new Event('input', {bubbles: true}));});

    // Test all six sector × measure views against direct compact-array arithmetic.
    for (const metric of ['emplvl31_33', 'emplvl334', 'emplvl336']) {
      await page.locator(`input[name=metric][value="${metric}"]`).check();
      for (const display of ['jobs', 'percent']) {
        const before = Date.now();
        await page.locator(`[data-display-metric="${display}"]`).click();
        await page.waitForFunction(([m, d]) => currentMetric === m && currentDisplayMetric === d, [metric, display]);
        results.timings[`${metric}:${display}:updateMs`] = Date.now() - before;
        const outcome = await page.evaluate(() => {
          const mi = bundle.metrics.indexOf(currentMetric), di = allDates.indexOf(currentDate);
          const expected = bundle.counties.map((c, ci) => {
            const row = bundle.values[di][ci];
            const value = currentDisplayMetric === 'jobs' ? row[mi] : (row[mi] != null && row[0] != null && row[0] > 0 ? row[mi] / row[0] : null);
            return {id: c.id, value};
          });
          const ranks = getRankedRows();
          const actual = new Map(ranks.map(r => [String(r.county ?? r.id ?? r.county_id), r.value]));
          return {count: ranks.length, expected: expected.filter(r => r.value !== null).length, mismatch: expected.find(r => r.value == null ? actual.has(r.id) : actual.get(r.id) !== r.value), finite: ranks.every(r => Number.isFinite(r.value)), sorted: ranks.every((r, i) => i === 0 || ranks[i - 1].value >= r.value)};
        });
        check(`${metric} ${display}: all county rankings match exact count/share; missing excluded`, !outcome.mismatch && outcome.count === outcome.expected && outcome.finite && outcome.sorted);
      }
    }
    if (fixtures) {
      for (const metric of ['emplvl31_33', 'emplvl334', 'emplvl336']) {
        await page.locator(`input[name=metric][value="${metric}"]`).check();
        for (const date of Object.keys(fixtures.aggregate)) {
          const expected = fixtures.aggregate[date][metric];
          const actual = await page.evaluate(d => getNationalTrendSeries().find(p => p.date === d), date);
          check(`source aggregate ${metric}/${date}: count, matched share, coverage`, Math.abs(actual.count - expected.count) < 1e-6 && Math.abs(actual.rate - expected.share) < 1e-12 && actual.countCoverage === expected.countCoverage && actual.shareCoverage === expected.shareCoverage);
        }
      }
      for (const f of fixtures.endpoints) {
        const values = await page.evaluate(f => { const a = getValue(f.id, f.start, f.metric, false), b = getValue(f.id, f.end, f.metric, false), sa = getValue(f.id, f.start, f.metric, true), sb = getValue(f.id, f.end, f.metric, true); return {count: b, share: sb, countChange: a == null || b == null ? null : b - a, shareChange: sa == null || sb == null ? null : sb - sa}; }, f);
        check(`source endpoint fixture ${f.id}/${f.metric}`, ['count', 'share', 'countChange', 'shareChange'].every(k => values[k] === f[k]));
      }
    }
    // Date selectors must stay within actual source bounds; both range endpoints respond.
    await page.locator('[data-display-metric="jobs"]').click();
    await page.locator('input[name=metric][value="emplvl31_33"]').check();
    await page.locator('#month-slider').evaluate(el => {el.value = '0'; el.dispatchEvent(new Event('input', {bubbles: true}));});
    check('first month selection', await page.evaluate(() => currentDate === '01/2002'));
    check('comparison disabled at first available month', await page.locator('#compare-toggle-btn').isDisabled());
    await page.locator('#month-slider').evaluate(el => {el.value = el.max; el.dispatchEvent(new Event('input', {bubbles: true}));});
    check('latest month selection re-enables comparison', await page.locator('#compare-toggle-btn').isEnabled());
    await page.locator('#compare-toggle-btn').click();
    check('comparison enabled', await page.evaluate(() => isComparisonActive));
    for (const [selector, value] of [['#compare-slider', '12'], ['#month-slider', '250']]) {
      await page.locator(selector).evaluate((el, v) => {el.value = v; el.dispatchEvent(new Event('input', {bubbles: true}));}, value);
    }
    check('both date inputs update', await page.evaluate(() => comparisonDate === allDates[12] && currentDate === allDates[250]));
    for (const display of ['jobs', 'percent']) {
      await page.locator(`[data-display-metric="${display}"]`).click();
      const valid = await page.evaluate(() => {
        const mi = bundle.metrics.indexOf(currentMetric), first = allDates.indexOf(comparisonDate), last = allDates.indexOf(currentDate);
        const ranks = new Map(getRankedRows().map(r => [String(r.county ?? r.id ?? r.county_id), r.value]));
        return bundle.counties.every((c, ci) => {
          const get = di => { const r = bundle.values[di][ci]; return currentDisplayMetric === 'jobs' ? r[mi] : (r[mi] != null && r[0] != null && r[0] > 0 ? r[mi] / r[0] : null); };
          const a = get(first), b = get(last);
          return a == null || b == null ? !ranks.has(c.id) : ranks.get(c.id) === b - a;
        });
      });
      check(`comparison ${display}: all counties later minus earlier, null endpoint excluded`, valid);
    }
    // Exercise pointer dragging of both visible comparison handles, beyond input events.
    for (const id of ['compare-range-thumb-start', 'compare-range-thumb-end']) {
      const thumb = page.locator(`#${id}`);
      const box = await thumb.boundingBox();
      if (box && box.width && box.height) {
        const stateBefore = await page.evaluate(() => [comparisonDate, currentDate]);
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + (id.endsWith('start') ? 30 : -30), box.y + box.height / 2, {steps: 5}); await page.mouse.up();
        const stateAfter = await page.evaluate(() => [comparisonDate, currentDate]);
        check(`${id}: pointer drag changes endpoint`, JSON.stringify(stateBefore) !== JSON.stringify(stateAfter));
      } else throw new Error(`Visible comparison handle missing: ${id}`);
    }
    await page.locator('#exit-compare-btn').click();
    await page.locator('#month-slider').evaluate(el => {el.value = el.max; el.dispatchEvent(new Event('input', {bubbles: true}));});
    if (fixtures) {
      for (const example of Object.values(fixtures.examples).filter(f => f.metric !== 'emplvl_total')) {
        await page.locator(`input[name=metric][value="${example.metric}"]`).check();
        await page.locator('[data-display-metric="jobs"]').click();
        const actual = await page.evaluate(f => {const record = getRecordForDate(f.id, f.date); return getDisplayValue(record);}, example);
        check(`source fixture ${example.id}/${example.date}/${example.metric}/${example.count}`, actual === example.count);
      }
      const missing = fixtures.examples['missing:emplvl336'];
      await page.locator('input[name=metric][value="emplvl336"]').check();
      const gap = await page.evaluate(f => {const series = getCountyTrendSeries(f.id); return series.find(p => p.date === f.date)?.value;}, missing);
      check('unavailable county trend preserves null gap', gap === null);
      await page.evaluate(f => {zoomToCounty(f.id); info.update(getInfoFocusCounty());}, missing);
      check('missing-data county remains selectable', await page.locator('#county-info-line').innerText().then(s => s.includes(missing.name.split(',')[0])));
    }
    if (await page.locator('#map-reset-btn').isVisible()) await page.locator('#map-reset-btn').click();
    const row = page.locator('#top-10-county-body tr[data-top-county]').first();
    const hoveredName = await row.locator('.rank-name').innerText();
    const hoverStart = Date.now(); await row.hover();
    check('ranking hover shows county map label', await page.evaluate(name => [...document.querySelectorAll('.leaflet-marker-icon')].some(el => el.textContent.includes(name.split(',')[0])), hoveredName));
    results.timings.hoverMs = Date.now() - hoverStart;
    const expectedPinnedCounty = await row.getAttribute('data-top-county');
    await row.click();
    await page.waitForFunction(id => pinnedCounty === id && !map._animatingZoom && !map._panAnim?._inProgress, expectedPinnedCounty);
    check('ranking click pins county', await page.evaluate(id => pinnedCounty === id, expectedPinnedCounty) && await page.locator('#map-reset-btn').isVisible());
    await page.locator('#map-reset-btn').click();
    await page.locator('#map-reset-btn').waitFor({state: 'hidden'});
    check('reset restores overview', await page.evaluate(() => map.getZoom() <= fullViewZoom && pinnedCounty === null && hoveredCounty === null && isFullView));
    await page.locator('#panel-legend').hover();
    check('legend renders unavailable explanation', /unavailable/i.test(await page.locator('#panel-legend').innerText()));
    await page.mouse.move(0, 0);
    await page.screenshot({path: path.join(out, 'desktop-final.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => map.getBounds().contains(mainlandBounds));
    check('mobile overview contains complete source extent', await page.evaluate(() => map.getBounds().contains(mainlandBounds) && map.getZoom() === fullViewZoom));
    check('mobile comparison button fits date panel', await page.locator('#compare-toggle-btn').evaluate(el => {
      const button = el.getBoundingClientRect(), panel = el.closest('.time-card').getBoundingClientRect();
      return button.top >= panel.top && button.bottom <= panel.bottom && button.left >= panel.left && button.right <= panel.right;
    }));
    await page.screenshot({path: path.join(out, 'mobile.png'), fullPage: true});
    check('mobile has no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.locator('[data-display-metric="jobs"]').click();
    check('mobile primary control responds', await page.evaluate(() => currentDisplayMetric === 'jobs'));
    const mobileRow = page.locator('#top-10-county-body tr[data-top-county]').first();
    const mobilePinned = await mobileRow.getAttribute('data-top-county');
    await mobileRow.click();
    await page.waitForFunction(id => pinnedCounty === id && !map._animatingZoom && !map._panAnim?._inProgress, mobilePinned);
    await page.setViewportSize({width: 844, height: 390});
    await page.waitForFunction(() => map.getSize().x === document.getElementById('map').clientWidth);
    check('resizing preserves pinned county', await page.evaluate(id => pinnedCounty === id, mobilePinned));
    await page.locator('#map-reset-btn').click();
    await page.waitForFunction(() => pinnedCounty === null && map.getBounds().contains(mainlandBounds));
    check('reset fits resized mobile overview', await page.evaluate(() => map.getBounds().contains(mainlandBounds)));
    await page.setViewportSize({width: 390, height: 844});
    await page.reload({waitUntil: 'load'});
    await page.waitForFunction(() => map.getBounds().contains(mainlandBounds));
    check('initial mobile load fits complete source extent', await page.evaluate(() => pinnedCounty === null && map.getZoom() === fullViewZoom && map.getBounds().contains(mainlandBounds)));
    await page.screenshot({path: path.join(out, 'mobile-initial.png'), fullPage: true});
    check('console has no application errors', results.errors.length === 0);
    results.status = 'PASS';
  } catch (error) {results.status = 'FAIL'; results.failure = String(error.stack || error); throw error;}
  finally {await browser.close(); fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 2)); console.log(JSON.stringify(results, null, 2));}
})().catch(() => process.exitCode = 1);
