import { execFile } from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

export interface SearchResult {
  file: string;
  name: string;
  type: string;
  signature: string;
  start_line: number;
  score: number;
}

export type Verdict = 'exists' | 'related' | 'novel';

export interface ReuseResult {
  description: string;
  verdict: Verdict;
  top_score: number;
  candidates: SearchResult[];
}

export interface CodeImplementation {
  file: string;
  type: string;
  start_line: number;
  end_line: number;
  body: string;
}

/** Running token/cost totals for the current chat session (reset on New chat). */
export interface SessionUsage {
  input: number;
  output: number;
  /** Portion of `input` re-read from cache (cheap); explains why CLI agents look token-heavy. */
  cached: number;
  cost: number;
  /** True once any turn reported a real cost (Claude); false for cost-less CLIs (Codex). */
  costKnown: boolean;
  turns: number;
}

export interface StatusResult {
  index: {
    exists: boolean;
    project?: string;
    chunks?: number;
    files?: number;
    indexed_at?: string | null;
    model?: string;
    age_days?: number | null;
    stale?: boolean;
  };
  registration: { claude: boolean; codex: boolean };
}

/**
 * Thin wrapper around the `sema` CLI's `--json` output.
 *
 * Every call shells out to the configured sema binary with `--json`, parses the
 * result, and turns a `{ "error": ... }` payload into a thrown Error so callers
 * only deal with success values.
 */
export class SemaClient {
  constructor(
    private readonly binaryPath: string,
    private readonly cwd: string,
  ) {}

  private async run<T>(args: string[]): Promise<T> {
    let stdout: string;
    try {
      ({ stdout } = await execFileAsync(this.binaryPath, args, {
        cwd: this.cwd,
        maxBuffer: 16 * 1024 * 1024,
      }));
    } catch (err: unknown) {
      const e = err as { code?: string; stderr?: string; message?: string };
      if (e.code === 'ENOENT') {
        throw new Error(
          `sema executable not found: "${this.binaryPath}". ` +
            'Set "sema.binaryPath" in settings to an absolute path.',
        );
      }
      const detail = (e.stderr || e.message || '').toString().trim();
      throw new Error(detail || 'sema command failed.');
    }

    let data: unknown;
    try {
      data = JSON.parse(stdout);
    } catch {
      throw new Error(
        'sema did not return valid JSON. Update the CLI to a version with --json support.',
      );
    }
    const maybeError = data as { error?: string; message?: string };
    if (maybeError && maybeError.error) {
      throw new Error(maybeError.message ?? String(maybeError.error));
    }
    return data as T;
  }

  async search(query: string, topK: number): Promise<SearchResult[]> {
    const data = await this.run<{ results?: SearchResult[] }>([
      'search',
      query,
      '--json',
      '--top-k',
      String(topK),
    ]);
    return data.results ?? [];
  }

  async reuse(description: string): Promise<ReuseResult> {
    return this.run<ReuseResult>(['reuse', description, '--json']);
  }

  async get(symbol: string): Promise<CodeImplementation[]> {
    const data = await this.run<{ implementations?: CodeImplementation[] }>([
      'get',
      symbol,
      '--json',
    ]);
    return data.implementations ?? [];
  }

  async status(): Promise<StatusResult> {
    return this.run<StatusResult>(['status', '--json']);
  }

  /** Build or refresh the index (runs `sema index . [--reset]`). Resolves on success. */
  async index(reset = false): Promise<void> {
    await this.runRaw(reset ? ['index', '.', '--reset'] : ['index', '.']);
  }

  /** Register/unregister the MCP server (runs `sema init [--codex] [--uninstall]`). Returns CLI output. */
  async register(target: 'claude' | 'codex', uninstall = false): Promise<string> {
    const args = ['init'];
    if (target === 'codex') {
      args.push('--codex');
    }
    if (uninstall) {
      args.push('--uninstall');
    }
    return this.runRaw(args);
  }

  /** Run `sema doctor` and return its plain-text report. */
  async doctor(): Promise<string> {
    return this.runRaw(['doctor']);
  }

  /** Run a sema subcommand that emits plain text (not JSON); return combined stdout+stderr. */
  private async runRaw(args: string[]): Promise<string> {
    try {
      const { stdout, stderr } = await execFileAsync(this.binaryPath, args, {
        cwd: this.cwd,
        maxBuffer: 32 * 1024 * 1024,
      });
      return (stdout || '') + (stderr || '');
    } catch (err: unknown) {
      const e = err as { code?: string; stderr?: string; stdout?: string; message?: string };
      if (e.code === 'ENOENT') {
        throw new Error(`sema executable not found: "${this.binaryPath}".`);
      }
      throw new Error(
        (e.stderr || e.stdout || e.message || 'sema command failed.').toString().trim(),
      );
    }
  }
}
