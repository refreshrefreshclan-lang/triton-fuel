// Renders the SVG sources in resources/ into the PNGs @capacitor/assets expects in assets/.
import fs from 'node:fs';
import sharp from 'sharp';
fs.mkdirSync('assets', { recursive: true });
const svg = f => fs.readFileSync(`resources/${f}`);
const png = (f, size, out) => sharp(svg(f), { density: 72 * size / 1024 }).resize(size, size).png().toFile(`assets/${out}`);
// the App Store rejects icons with transparency, so the full icon is flattened onto its dark background
await sharp(svg('icon.svg'), { density: 72 }).resize(1024, 1024).flatten({ background: '#08080B' }).removeAlpha().png().toFile('assets/icon-only.png');
await png('icon-foreground.svg', 1024, 'icon-foreground.png');
await png('icon-background.svg', 1024, 'icon-background.png');
// launch screens: the mark centred on the app's light and dark backgrounds
for (const [mark, bg, out] of [['mark-light.svg', '#F2F2F7', 'splash.png'], ['mark-dark.svg', '#000000', 'splash-dark.png']]) {
  const m = await sharp(svg(mark), { density: 72 * 700 / 1024 }).resize(700, 700).png().toBuffer();
  await sharp({ create: { width: 2732, height: 2732, channels: 4, background: bg } })
    .composite([{ input: m, gravity: 'centre' }]).png().toFile(`assets/${out}`);
}
console.log('assets/:', fs.readdirSync('assets').join(', '));
