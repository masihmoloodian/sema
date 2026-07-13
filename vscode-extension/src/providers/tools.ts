import * as fs from 'fs';
import * as path from 'path';
import { execFile } from 'child_process';
import { promisify } from 'util';
import type OpenAI from 'openai';

const execFileAsync = promisify(execFile);

type ToolDef = OpenAI.Chat.Completions.ChatCompletionTool;

function fn(
  name: string,
  description: string,
  properties: Record<string, unknown>,
  required: string[] = [],
): ToolDef {
  return {
    type: 'function',
    function: { name, description, parameters: { type: 'object', properties, required } },
  };
}

const str = (description: string) => ({ type: 'string', description });
const int = (description: string) => ({ type: 'integer', description });
const bool = (description: string) => ({ type: 'boolean', description });

/**
 * Tool catalogue offered to OpenAI-compatible providers in agentic modes. This
 * mirrors the local CLI agents (Claude Code / Codex) — search, read, surgical
 * edit, write, run — and adds sema's own semantic index via `search_code` /
 * `get_code`. sema executes every call in the Node extension host; the model only
 * decides which to call.
 */
const DEFS: Record<string, ToolDef> = {
  search_code: fn(
    'search_code',
    "Semantic code search over sema's index. Finds the most relevant functions, classes, and " +
      'methods by meaning and returns their signatures and locations (not full bodies). Use this ' +
      'first to locate relevant code, then read_file or get_code for details.',
    { query: str('What to look for, in natural language.'), top_k: int('Max results (default 8).') },
    ['query'],
  ),
  get_code: fn(
    'get_code',
    "Fetch the full source of a symbol (function/class/method) by name using sema's index.",
    { symbol: str('The symbol name to fetch.') },
    ['symbol'],
  ),
  grep: fn(
    'grep',
    'Search file contents by regular expression across the workspace (skips node_modules, .git, ' +
      'build output, etc.). Returns matching lines as path:line: text.',
    {
      pattern: str('A JavaScript regular expression to match against each line.'),
      path: str('Directory to search under, relative to the workspace root (default: root).'),
      glob: str('Optional glob to restrict which files are searched, e.g. **/*.ts.'),
      ignore_case: bool('Case-insensitive match (default false).'),
    },
    ['pattern'],
  ),
  glob: fn(
    'glob',
    'Find files whose path matches a glob pattern (e.g. **/*.py, src/**/*.ts, *.json).',
    {
      pattern: str('The glob pattern to match, relative to the workspace root.'),
      path: str('Directory to search under (default: root).'),
    },
    ['pattern'],
  ),
  list_directory: fn(
    'list_directory',
    'List the files and subdirectories of a workspace directory.',
    { path: str('Directory path relative to the workspace root; defaults to the root.') },
  ),
  read_file: fn(
    'read_file',
    'Read a UTF-8 text file from the workspace. Optionally read a line range with offset/limit.',
    {
      path: str('Path relative to the workspace root.'),
      offset: int('1-based line to start from (optional).'),
      limit: int('Number of lines to read from offset (optional).'),
    },
    ['path'],
  ),
  write_file: fn(
    'write_file',
    'Create or overwrite a text file with the given content. Parent directories are created ' +
      'automatically. Prefer edit_file for changes to existing files.',
    { path: str('Path relative to the workspace root.'), content: str('Full file content to write.') },
    ['path', 'content'],
  ),
  edit_file: fn(
    'edit_file',
    'Make a surgical edit by replacing an exact string. old_string must match the file exactly ' +
      'and be unique (include enough surrounding context), unless replace_all is set.',
    {
      path: str('Path relative to the workspace root.'),
      old_string: str('The exact text to replace.'),
      new_string: str('The replacement text.'),
      replace_all: bool('Replace every occurrence instead of requiring uniqueness (default false).'),
    },
    ['path', 'old_string', 'new_string'],
  ),
  delete_file: fn(
    'delete_file',
    'Delete a file from the workspace.',
    { path: str('Path relative to the workspace root.') },
    ['path'],
  ),
  run_command: fn(
    'run_command',
    'Run a shell command in the workspace root and return its combined stdout/stderr. Use for ' +
      'builds, tests, git, installs, scaffolding, etc.',
    { command: str('The shell command to run.') },
    ['command'],
  ),
};

/** Full toolset for Agent mode — read + write + run. */
export const AGENT_TOOLS: ToolDef[] = [
  'search_code',
  'get_code',
  'grep',
  'glob',
  'list_directory',
  'read_file',
  'write_file',
  'edit_file',
  'delete_file',
  'run_command',
].map((n) => DEFS[n]);

/** Read-only subset for Plan mode — investigate without touching the workspace. */
export const READONLY_TOOLS: ToolDef[] = [
  'search_code',
  'get_code',
  'grep',
  'glob',
  'list_directory',
  'read_file',
].map((n) => DEFS[n]);

const MUTATING = new Set(['write_file', 'edit_file', 'delete_file', 'run_command']);

export interface ToolContext {
  cwd: string;
  /** Plan mode: refuse mutating tools even if the model calls them. */
  readOnly?: boolean;
  /** sema binary path, enabling search_code / get_code. */
  semaBin?: string;
}

const MAX_RESULT = 60000;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_WALK_FILES = 20000;
const IGNORE_DIRS = new Set([
  '.git', 'node_modules', '.venv', 'venv', 'env', 'dist', 'build', 'out', '__pycache__',
  '.pytest_cache', '.ruff_cache', '.mypy_cache', '.sema', '.next', '.nuxt', '.cache', 'target',
  '.gradle', '.idea', 'coverage', '.turbo', 'vendor',
]);

/** Resolve a workspace-relative path and refuse to escape the workspace root. */
function resolveInside(cwd: string, rel: string): string {
  const root = path.resolve(cwd);
  const abs = path.resolve(root, rel || '.');
  if (abs !== root && !abs.startsWith(root + path.sep)) {
    throw new Error(`path escapes the workspace: ${rel}`);
  }
  return abs;
}

/** Translate a glob (supporting **, *, ?) into an anchored RegExp over posix paths. */
function globToRegExp(glob: string): RegExp {
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') {
        if (glob[i + 2] === '/') {
          re += '(?:.*/)?';
          i += 2;
        } else {
          re += '.*';
          i += 1;
        }
      } else {
        re += '[^/]*';
      }
    } else if (c === '?') {
      re += '[^/]';
    } else if ('\\^$+.()|{}[]'.includes(c)) {
      re += '\\' + c;
    } else {
      re += c;
    }
  }
  return new RegExp('^' + re + '$');
}

/** Walk files under `start`, skipping heavy/ignored directories, capped at MAX_WALK_FILES. */
async function* walkFiles(start: string): AsyncGenerator<string> {
  const stack = [start];
  let count = 0;
  while (stack.length) {
    const dir = stack.pop() as string;
    let entries: fs.Dirent[];
    try {
      entries = await fs.promises.readdir(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      const abs = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (!IGNORE_DIRS.has(e.name)) {
          stack.push(abs);
        }
      } else if (e.isFile()) {
        yield abs;
        if (++count >= MAX_WALK_FILES) {
          return;
        }
      }
    }
  }
}

/** Short label for the activity line, e.g. edit_file → src/app.ts. */
export function toolDetail(name: string, args: Record<string, unknown>): string {
  switch (name) {
    case 'run_command':
      return String(args.command ?? '').slice(0, 80);
    case 'grep':
    case 'glob':
      return String(args.pattern ?? '');
    case 'search_code':
      return String(args.query ?? '');
    case 'get_code':
      return String(args.symbol ?? '');
    default:
      return String(args.path ?? '');
  }
}

async function semaJson(
  ctx: ToolContext,
  args: string[],
): Promise<{ stdout: string }> {
  return execFileAsync(ctx.semaBin as string, args, {
    cwd: ctx.cwd,
    timeout: 60000,
    maxBuffer: 16 * 1024 * 1024,
  });
}

/** Execute one tool call and return a string result to feed back to the model. */
export async function executeTool(
  name: string,
  args: Record<string, unknown>,
  ctx: ToolContext,
): Promise<string> {
  if (ctx.readOnly && MUTATING.has(name)) {
    return `Error: ${name} is not available in Plan mode (read-only). Switch to Agent mode to make changes.`;
  }
  try {
    switch (name) {
      case 'read_file': {
        const abs = resolveInside(ctx.cwd, String(args.path ?? ''));
        const body = await fs.promises.readFile(abs, 'utf8');
        const offset = Number(args.offset);
        const limit = Number(args.limit);
        if (Number.isFinite(offset) || Number.isFinite(limit)) {
          const lines = body.split('\n');
          const start = Number.isFinite(offset) && offset > 0 ? Math.floor(offset) - 1 : 0;
          const count = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : lines.length - start;
          return lines.slice(start, start + count).join('\n');
        }
        return body.length > MAX_RESULT ? body.slice(0, MAX_RESULT) + '\n… (truncated)' : body;
      }
      case 'write_file': {
        const rel = String(args.path ?? '');
        const abs = resolveInside(ctx.cwd, rel);
        await fs.promises.mkdir(path.dirname(abs), { recursive: true });
        const content = String(args.content ?? '');
        await fs.promises.writeFile(abs, content, 'utf8');
        return `Wrote ${rel} (${content.length} bytes).`;
      }
      case 'edit_file': {
        const rel = String(args.path ?? '');
        const abs = resolveInside(ctx.cwd, rel);
        const oldStr = String(args.old_string ?? '');
        const newStr = String(args.new_string ?? '');
        if (!oldStr) {
          return 'Error: old_string is required.';
        }
        const body = await fs.promises.readFile(abs, 'utf8');
        const parts = body.split(oldStr);
        const occurrences = parts.length - 1;
        if (occurrences === 0) {
          return `Error: old_string not found in ${rel}.`;
        }
        if (occurrences > 1 && !args.replace_all) {
          return `Error: old_string appears ${occurrences} times in ${rel}; add more surrounding context to make it unique, or set replace_all: true.`;
        }
        // Join-based replacement avoids String.replace's $-pattern handling in new_string.
        const updated = args.replace_all
          ? parts.join(newStr)
          : parts[0] + newStr + parts.slice(1).join(oldStr);
        await fs.promises.writeFile(abs, updated, 'utf8');
        return `Edited ${rel} (${occurrences} replacement${occurrences > 1 ? 's' : ''}).`;
      }
      case 'delete_file': {
        const rel = String(args.path ?? '');
        const abs = resolveInside(ctx.cwd, rel);
        await fs.promises.unlink(abs);
        return `Deleted ${rel}.`;
      }
      case 'list_directory': {
        const abs = resolveInside(ctx.cwd, String(args.path ?? '.'));
        const entries = await fs.promises.readdir(abs, { withFileTypes: true });
        const listed = entries.map((e) => (e.isDirectory() ? e.name + '/' : e.name));
        return listed.length ? listed.sort().join('\n') : '(empty)';
      }
      case 'glob': {
        const re = globToRegExp(String(args.pattern ?? '*'));
        const base = resolveInside(ctx.cwd, String(args.path ?? '.'));
        const root = path.resolve(ctx.cwd);
        const out: string[] = [];
        for await (const abs of walkFiles(base)) {
          const rel = path.relative(root, abs).split(path.sep).join('/');
          if (re.test(rel) || re.test(path.basename(abs))) {
            out.push(rel);
            if (out.length >= 500) {
              break;
            }
          }
        }
        return out.length ? out.sort().join('\n') : 'No files matched.';
      }
      case 'grep': {
        const pattern = String(args.pattern ?? '');
        if (!pattern) {
          return 'Error: pattern is required.';
        }
        let re: RegExp;
        try {
          re = new RegExp(pattern, args.ignore_case ? 'i' : '');
        } catch (e) {
          return `Error: invalid regex: ${(e as Error).message}`;
        }
        const base = resolveInside(ctx.cwd, String(args.path ?? '.'));
        const root = path.resolve(ctx.cwd);
        const fileGlob = args.glob ? globToRegExp(String(args.glob)) : undefined;
        const matches: string[] = [];
        for await (const abs of walkFiles(base)) {
          const rel = path.relative(root, abs).split(path.sep).join('/');
          if (fileGlob && !fileGlob.test(rel) && !fileGlob.test(path.basename(abs))) {
            continue;
          }
          let stat: fs.Stats;
          try {
            stat = await fs.promises.stat(abs);
          } catch {
            continue;
          }
          if (stat.size > MAX_FILE_BYTES) {
            continue;
          }
          let buf: Buffer;
          try {
            buf = await fs.promises.readFile(abs);
          } catch {
            continue;
          }
          if (buf.includes(0)) {
            continue; // binary
          }
          const lines = buf.toString('utf8').split('\n');
          for (let i = 0; i < lines.length; i++) {
            if (re.test(lines[i])) {
              matches.push(`${rel}:${i + 1}: ${lines[i].trim().slice(0, 200)}`);
              if (matches.length >= 200) {
                break;
              }
            }
          }
          if (matches.length >= 200) {
            matches.push('… (more matches truncated)');
            break;
          }
        }
        return matches.length ? matches.join('\n') : 'No matches.';
      }
      case 'search_code': {
        if (!ctx.semaBin) {
          return 'search_code unavailable: no sema binary configured. Use grep instead.';
        }
        const topK = Math.min(Math.max(Math.floor(Number(args.top_k) || 8), 1), 30);
        const { stdout } = await semaJson(ctx, [
          'search',
          String(args.query ?? ''),
          '--json',
          '--top-k',
          String(topK),
        ]);
        const data = JSON.parse(stdout) as {
          results?: Array<{ file: string; name: string; type: string; signature: string; start_line: number }>;
        };
        const results = data.results ?? [];
        if (!results.length) {
          return 'No results. Try grep for a literal string.';
        }
        return results
          .map((r) => `${r.file}:${r.start_line}  ${r.type} ${r.name}  ${r.signature}`)
          .join('\n');
      }
      case 'get_code': {
        if (!ctx.semaBin) {
          return 'get_code unavailable: no sema binary configured. Use read_file instead.';
        }
        const { stdout } = await semaJson(ctx, ['get', String(args.symbol ?? ''), '--json']);
        const data = JSON.parse(stdout) as {
          implementations?: Array<{ file: string; start_line: number; end_line: number; body: string }>;
        };
        const impls = data.implementations ?? [];
        if (!impls.length) {
          return `No implementation found for "${String(args.symbol ?? '')}".`;
        }
        return impls
          .map((i) => `// ${i.file}:${i.start_line}-${i.end_line}\n${i.body}`)
          .join('\n\n')
          .slice(0, MAX_RESULT);
      }
      case 'run_command': {
        const command = String(args.command ?? '');
        const { stdout, stderr } = await execFileAsync('bash', ['-lc', command], {
          cwd: ctx.cwd,
          timeout: 120000,
          maxBuffer: 10 * 1024 * 1024,
        });
        const out = (stdout || '') + (stderr ? (stdout ? '\n' : '') + stderr : '');
        const trimmed = out.trim();
        return trimmed ? trimmed.slice(0, MAX_RESULT) : '(command produced no output)';
      }
      default:
        return `Unknown tool: ${name}`;
    }
  } catch (e) {
    // Failed commands / sema calls throw with stdout/stderr attached — surface those.
    const err = e as { stdout?: string; stderr?: string; message?: string };
    if (err.stdout || err.stderr) {
      return `Failed:\n${(err.stdout || '') + (err.stderr || '')}`.slice(0, MAX_RESULT);
    }
    return `Error: ${(e as Error).message}`;
  }
}
