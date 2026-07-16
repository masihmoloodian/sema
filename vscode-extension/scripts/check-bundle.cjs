const fs = require('fs');
const path = require('path');

const bundle = fs.readFileSync(path.join(__dirname, '..', 'out', 'extension.js'), 'utf8');

if (!bundle.includes('var __semaImportMetaUrl = require("url").pathToFileURL(__filename).href;')) {
  throw new Error('extension bundle is missing the CommonJS import.meta.url shim');
}

if (/createRequire\)?\(import_meta\.url\)/.test(bundle)) {
  throw new Error('extension bundle still passes undefined import_meta.url to createRequire');
}

for (const marker of ['permissionRequest', 'permissionDecision', 'Full access is enabled']) {
  if (!bundle.includes(marker)) {
    throw new Error(`extension bundle is missing the inline permission UI marker: ${marker}`);
  }
}

console.log('bundle compatibility check passed');
