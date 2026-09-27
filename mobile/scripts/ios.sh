#!/bin/bash
# Sets up and opens the iPhone app in Xcode. Run on a Mac from the mobile folder:  npm run ios
set -e
cd "$(dirname "$0")/.."
npm install --no-audit --no-fund
npm run prepare-web
npm run assets
if [ ! -d ios ]; then npx cap add ios; fi
npx capacitor-assets generate --ios
node scripts/ios-tweaks.mjs
npx cap sync ios
npx cap open ios
