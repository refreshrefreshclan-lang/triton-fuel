// App Store settings for the iOS project, applied after `npx cap add ios`:
//  - iPhone only (no iPad version, so no iPad screenshots are required)
//  - portrait only
//  - declares that the app uses no special encryption (skips Apple's export compliance question on every upload)
import fs from 'node:fs';

const plistPath = 'ios/App/App/Info.plist';
const pbxPath = 'ios/App/App.xcodeproj/project.pbxproj';
if (!fs.existsSync(plistPath)) throw new Error('ios/ is missing: run `npx cap add ios` first.');

let plist = fs.readFileSync(plistPath, 'utf8');
const drop = key => { plist = plist.replace(new RegExp(`\\s*<key>${key}</key>\\s*(<array>[\\s\\S]*?</array>|<string>[^<]*</string>|<true/>|<false/>)`), ''); };
['UISupportedInterfaceOrientations', 'UISupportedInterfaceOrientations~ipad', 'ITSAppUsesNonExemptEncryption', 'CFBundleDisplayName', 'UIRequiresFullScreen'].forEach(drop);
const add = `
	<key>CFBundleDisplayName</key>
	<string>Triton Fuel</string>
	<key>ITSAppUsesNonExemptEncryption</key>
	<false/>
	<key>UIRequiresFullScreen</key>
	<true/>
	<key>UISupportedInterfaceOrientations</key>
	<array>
		<string>UIInterfaceOrientationPortrait</string>
	</array>
`;
const end = plist.lastIndexOf('</dict>');
plist = plist.slice(0, end) + add.trimStart().replace(/^/, '\t') + plist.slice(end);
fs.writeFileSync(plistPath, plist);

let pbx = fs.readFileSync(pbxPath, 'utf8');
const before = (pbx.match(/TARGETED_DEVICE_FAMILY = "?1,2"?;/g) || []).length;
pbx = pbx.replace(/TARGETED_DEVICE_FAMILY = "?1,2"?;/g, 'TARGETED_DEVICE_FAMILY = 1;');
fs.writeFileSync(pbxPath, pbx);
console.log(`iOS settings applied: iPhone only (${before} build configs), portrait, no-encryption declaration.`);
