const Module = require('module');
const path = require('path');
const vm = require('vm');
const esbuild = require('esbuild');

const entry = path.join(__dirname, '..', 'src', 'chatPanel.ts');
const built = esbuild.buildSync({
  entryPoints: [entry],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  external: ['vscode'],
  write: false,
});

// Load the bundled class with a minimal VS Code stub. getHtml() itself has no
// VS Code runtime dependency, and calling the prototype avoids its constructor.
const filename = path.join(__dirname, '.check-webview.js');
const compiled = new Module(filename, module);
const originalLoad = Module._load;
try {
  Module._load = function load(request, parent, isMain) {
    if (request === 'vscode') return {};
    return originalLoad.call(this, request, parent, isMain);
  };
  compiled.filename = filename;
  compiled.paths = Module._nodeModulePaths(path.dirname(filename));
  compiled._compile(built.outputFiles[0].text, filename);
} finally {
  Module._load = originalLoad;
}

const ChatViewProvider = compiled.exports.ChatViewProvider;
if (!ChatViewProvider) throw new Error('could not load ChatViewProvider for webview validation');
const html = ChatViewProvider.prototype.getHtml.call({});
for (const id of ['plusbtn', 'semabtn', 'modepill', 'permissionpill', 'modelpill']) {
  if (!html.includes(`id="${id}"`)) throw new Error(`composer is missing the ${id} control`);
}
if (html.includes('id="gearbtn"')) throw new Error('legacy overloaded gear control is still present');
if (!html.includes("plusBtn.addEventListener('click', function(){ vscode.postMessage({type:'attach'}); });")) {
  throw new Error('attachment button is not a direct file attachment action');
}
if (!html.includes("showMenu(semaBtn, 'left'") || !html.includes("showMenu(permissionPill, 'right'")) {
  throw new Error('dedicated Sema or permission menu is missing');
}
const match = /<script nonce="[^"]+">([\s\S]*?)<\/script>/.exec(html);
if (!match) throw new Error('could not find the chat webview script');

// Parsing the exact generated script catches template-escaping errors that
// TypeScript cannot see because the JavaScript lives inside an HTML string.
new vm.Script(match[1], { filename: 'sema-chat-webview.js' });
console.log('webview script syntax check passed');
