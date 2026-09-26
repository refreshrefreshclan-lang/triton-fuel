// Copies the published app (docs/index.html, with the latest menu built in as an offline fallback)
// and the privacy page into www/, which Capacitor packages into the native apps.
// It also downloads the app's two fonts into www/fonts so the phone app never contacts Google Fonts
// (keeps the privacy policy accurate, and the fonts work offline).
import fs from 'node:fs';
fs.mkdirSync('www/fonts', { recursive: true });
for (const f of ['index.html', 'privacy.html', 'icon.svg']) {
  const src = `../docs/${f}`;
  if (fs.existsSync(src)) fs.copyFileSync(src, `www/${f}`);
}
if (!fs.existsSync('www/index.html')) throw new Error('docs/index.html is missing: run the menu sync first.');

let html = fs.readFileSync('www/index.html', 'utf8');
const link = html.match(/<link rel="stylesheet" href="(https:\/\/fonts\.googleapis\.com\/css2[^"]+)">/);
html = html.replace(/<link rel="preconnect" href="https:\/\/fonts\.(googleapis|gstatic)\.com"[^>]*>\n?/g, '');
if (link) {
  let local = '';
  try {
    const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36';
    let css = await (await fetch(link[1].replace(/&amp;/g, '&'), { headers: { 'User-Agent': UA } })).text();
    const urls = [...new Set(css.match(/https:\/\/fonts\.gstatic\.com\/[^)]+\.woff2/g) || [])];
    if (!urls.length) throw new Error('no font files listed');
    for (const [i, u] of urls.entries()) {
      const name = `f${i}.woff2`;
      fs.writeFileSync(`www/fonts/${name}`, Buffer.from(await (await fetch(u)).arrayBuffer()));
      css = css.split(u).join(name);
    }
    fs.writeFileSync('www/fonts/fonts.css', css);
    local = '<link rel="stylesheet" href="fonts/fonts.css">';
    console.log(`fonts bundled: ${urls.length} files`);
  } catch (e) {
    console.warn('Could not bundle fonts, the app will use the system font:', e.message);
  }
  html = html.replace(link[0], local);
}
fs.writeFileSync('www/index.html', html);
console.log('www/ ready:', fs.readdirSync('www').join(', '));
