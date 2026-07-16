const esbuild = require('esbuild');

const watch = process.argv.includes('--watch');

const options = {
  entryPoints: ['src/extension.ts'],
  bundle: true,
  outfile: 'out/extension.js',
  external: ['vscode'],
  format: 'cjs',
  platform: 'node',
  target: 'node18',
  // The Claude Agent SDK is ESM and calls createRequire(import.meta.url). When it is
  // bundled into this CommonJS extension, esbuild otherwise replaces import.meta with
  // an empty object and the SDK crashes before it can show a permission prompt.
  banner: {
    js: 'var __semaImportMetaUrl = require("url").pathToFileURL(__filename).href;',
  },
  define: {
    'import.meta.url': '__semaImportMetaUrl',
  },
  sourcemap: true,
  logLevel: 'info',
};

(async () => {
  if (watch) {
    const ctx = await esbuild.context(options);
    await ctx.watch();
    console.log('esbuild: watching for changes…');
  } else {
    await esbuild.build(options);
    console.log('esbuild: build complete');
  }
})();
