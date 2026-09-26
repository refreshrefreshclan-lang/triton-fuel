// Makes the store icons and the Google Play feature graphic from docs/icon.svg.
// Usage: node store/graphics.mjs <outDir>   (run from the repo root)
import fs from 'node:fs';
import { chromium } from 'playwright';
import sharp from 'sharp';

const out = process.argv[2] || 'store-out';
fs.mkdirSync(out, { recursive: true });
const icon = fs.readFileSync('docs/icon.svg');
// App Store icon: 1024x1024, no transparency. Play icon: 512x512.
await sharp(icon).resize(1024, 1024).flatten({ background: '#08080B' }).removeAlpha().png().toFile(`${out}/app-store-icon-1024.png`);
await sharp(icon).resize(512, 512).png().toFile(`${out}/play-icon-512.png`);

const iconData = 'data:image/svg+xml;base64,' + icon.toString('base64');
const feature = `<!doctype html><html><head>
<link href="https://fonts.googleapis.com/css2?family=Figtree:wght@500;700;800&display=swap" rel="stylesheet">
<style>
html,body{margin:0;width:1024px;height:500px;overflow:hidden}
body{font-family:Figtree,system-ui,sans-serif;color:#fff;display:flex;align-items:center;gap:56px;padding:0 72px;box-sizing:border-box;
background:radial-gradient(circle at 18% 30%,rgba(74,143,255,.35),transparent 42%),radial-gradient(circle at 85% 80%,rgba(52,212,99,.22),transparent 45%),radial-gradient(circle at 70% 10%,rgba(255,176,46,.20),transparent 40%),linear-gradient(135deg,#1E1E26,#08080B)}
img{width:250px;height:250px;border-radius:56px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
h1{font-size:74px;font-weight:800;letter-spacing:-.02em;margin:0 0 12px}
p{font-size:29px;font-weight:500;line-height:1.3;margin:0;color:rgba(255,255,255,.78)}
.dots{display:flex;gap:18px;margin-top:26px;font-size:22px;font-weight:700}
.dots span::before{content:"";display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:8px;background:var(--c)}
</style></head><body>
<img src="${iconData}" alt="">
<div><h1>Triton Fuel</h1><p>Log UCSD dining hall meals in a tap.<br>Calories and macros, instantly.</p>
<div class="dots"><span style="--c:#4A8FFF">Protein</span><span style="--c:#34D463">Carbs</span><span style="--c:#FFB02E">Fat</span><span style="--c:#C77DFF">Fiber</span></div></div>
</body></html>`;
const browser = await chromium.launch();
const p = await browser.newPage({ viewport: { width: 1024, height: 500 } });
await p.setContent(feature, { waitUntil: 'networkidle' });
await p.evaluate(() => document.fonts.ready);
const buf = await p.screenshot();
await sharp(buf).removeAlpha().png().toFile(`${out}/play-feature-graphic-1024x500.png`);
await browser.close();
console.log('graphics done');
