// Takes the store screenshots from the published app (docs/index.html) with a believable day logged.
// Usage: node store/shots.mjs <outDir>   (run from the repo root; needs playwright + chromium)
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const out = process.argv[2] || 'store-out';
fs.mkdirSync(out, { recursive: true });
let html = fs.readFileSync('docs/index.html', 'utf8');
// expose the app's internals so the script can log meals the same way a tap does
const end = html.lastIndexOf('})();');
html = html.slice(0, end) + 'window.__tf={get MENU(){return MENU},get state(){return state},snap,sampleHistory,todayKey,save};\n' + html.slice(end);
const page = path.resolve(out, '_shot.html');
fs.writeFileSync(page, html);
const menu = JSON.parse(fs.readFileSync('docs/menu.json', 'utf8'));
const day = String(menu.date || menu.generated || '').slice(0, 10);

const SIZES = [
  // App Store 6.9" (1290x2796) and Google Play phone (1080x1920)
  { name: 'ios-6.9', w: 430, h: 932, dpr: 3 },
  // App Store 6.5" (1284x2778), for the 6.5-inch slot
  { name: 'ios-6.5', w: 428, h: 926, dpr: 3 },
  { name: 'play-phone', w: 360, h: 640, dpr: 3 },
];

const browser = await chromium.launch();
for (const size of SIZES) for (const scheme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: size.w, height: size.h }, deviceScaleFactor: size.dpr,
    timezoneId: 'America/Los_Angeles', isMobile: true, hasTouch: true, colorScheme: scheme });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('page error:', e.message));
  if (day) await p.clock.setFixedTime(new Date(`${day}T18:40:00-07:00`));
  const url = 'file://' + page;
  await p.goto(url); await p.waitForTimeout(600);
  const logged = await p.evaluate(() => {
    const { MENU, state, snap, sampleHistory, todayKey, save } = window.__tf;
    const all = MENU.halls.flatMap(h => h.items.map(i => [h.name, i]));
    const used = new Set();
    // pick an appealing item for each meal: matches the rule, prefers the listed words, never repeats
    const find = (period, test, like) => {
      const ok = all.filter(([, i]) => (i.period === period || (period !== 'Breakfast' && i.period === 'Lunch/Dinner')) && !used.has(i.name) && test(i));
      const dens = ([, i]) => i.n.pro / Math.max(i.n.kcal, 1);
      const liked = ok.filter(([, i]) => like.test(i.name)).sort((a, b) => dens(b) - dens(a));
      const hit = liked[0] || ok.sort((a, b) => dens(b) - dens(a))[0];
      if (hit) used.add(hit[1].name);
      return hit;
    };
    const picks = [
      [find('Breakfast', i => i.n.pro >= 15 && i.n.fat <= 18 && i.n.kcal >= 250 && i.n.kcal <= 520, /burrito|omelet|egg|oat|bagel|pancake|waffle/i), 1, 'Breakfast'],
      [find('Lunch', i => i.n.pro >= 35 && i.n.fat <= 22 && i.n.kcal >= 450 && i.n.kcal <= 800, /bowl|shawarma|burrito|chicken|poke/i), 1, 'Lunch'],
      [find('Lunch', i => i.n.fib >= 3 && i.n.fat <= 8 && i.n.kcal >= 80 && i.n.kcal <= 300, /hummus|salad|fruit|soup/i), 1, 'Lunch'],
      [find('Dinner', i => i.n.pro >= 40 && i.n.fat <= 25 && i.n.kcal >= 400 && i.n.kcal <= 750, /salmon|steak|chicken|pasta|teriyaki|tikka/i), 1, 'Dinner'],
    ].filter(x => x[0]);
    state.log = picks.map(([[hall, it], s, meal]) => { const e = snap(it, hall, s, meal); delete e.ex; return e; });
    state.example = false; state.day = todayKey(); state.history = sampleHistory(); save();
    return state.log.map(e => `${e.meal}: ${e.name} (${e.hall})`);
  });
  if (size.name === 'ios-6.9' && scheme === 'light') console.log(logged.join('\n'));
  const lunch = logged[1] || '';
  await p.reload(); await p.waitForTimeout(900);
  await p.addStyleTag({ content: '.file-btn{display:none!important}' });
  const shot = async n => { await p.waitForTimeout(500); await p.screenshot({ path: `${out}/${size.name}-${scheme}-${n}.png` }); };
  await shot('1-today');
  await p.click('#tab-menu'); await shot('2-dining');
  // open the hall the lunch came from, then that item's nutrition label
  const m = lunch.match(/^Lunch: (.*) \((.*)\)$/) || [];
  const hallRow = m[2] ? p.locator('#hall-list [data-h]', { hasText: m[2] }).first() : p.locator('#hall-list [data-h]').first();
  await hallRow.click(); await p.evaluate(() => window.scrollTo(0, 0)); await shot('3-menu');
  await p.click('#venues button:first-child').catch(() => {});
  await p.locator('#period button', { hasText: 'Lunch' }).first().click().catch(() => {});
  const item = m[1] ? p.locator('#menu [data-i]', { hasText: m[1] }).first() : p.locator('#menu [data-i]').first();
  await (await item.count() ? item : p.locator('#menu [data-i]').first()).click(); await p.waitForTimeout(400); await shot('4-item');
  await p.goto(url); await p.waitForTimeout(700);
  await p.addStyleTag({ content: '.file-btn{display:none!important}' });
  await p.click('#tab-week');
  await shot('5-week');
  await ctx.close();
}
await browser.close();
fs.unlinkSync(page);
console.log('screenshots:', fs.readdirSync(out).length);
