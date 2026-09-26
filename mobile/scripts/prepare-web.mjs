// Copies the published app (docs/index.html, with the latest menu built in as an offline fallback)
// and the privacy page into www/, which Capacitor packages into the native apps.
import fs from 'node:fs';
fs.mkdirSync('www', { recursive: true });
for (const f of ['index.html', 'privacy.html', 'icon.svg']) {
  const src = `../docs/${f}`;
  if (fs.existsSync(src)) fs.copyFileSync(src, `www/${f}`);
}
if (!fs.existsSync('www/index.html')) throw new Error('docs/index.html is missing: run the menu sync first.');
console.log('www/ ready:', fs.readdirSync('www').join(', '));
