import { execFile } from 'child_process';
import { ChildProcessWithoutNullStreams, spawn } from 'child_process';
import { promisify } from 'util';
import { createInterface } from 'readline';

const execFileAsync = promisify(execFile);

/** Thrown when the sema executable can't be found on disk (ENOENT). */
export class SemaNotFoundError extends Error {
  constructor(public readonly binaryPath: string) {
    super(`sema executable not found: "${binaryPath}".`);
    this.name = 'SemaNotFoundError';
  }
}

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
    changed_files?: number;
    deleted_files?: number;
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
  private worker?: ChildProcessWithoutNullStreams;
  private workerReady?: Promise<void>;
  private nextRequestId = 1;
  private readonly pending = new Map<
    number,
    { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }
  >();

  constructor(
    private readonly binaryPath: string,
    private readonly cwd: string,
  ) {
    // Warm SBERT in the background at extension activation. The first chat turn
    // usually arrives after this is ready; later turns never pay model startup.
    void this.ensureWorker().catch(() => {});
  }

  private ensureWorker(): Promise<void> {
    if (this.workerReady) return this.workerReady;
    this.workerReady = new Promise<void>((resolve, reject) => {
      const child = spawn(this.binaryPath, ['_query-server', '--project', this.cwd], {
        cwd: this.cwd,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
      this.worker = child;
      child.stderr.resume();
      let settled = false;
      const lines = createInterface({ input: child.stdout });
      lines.on('line', (line) => {
        let message: { ready?: boolean; id?: number; result?: unknown; error?: string };
        try { message = JSON.parse(line); } catch { return; }
        if (message.ready && !settled) {
          settled = true;
          resolve();
          return;
        }
        if (typeof message.id === 'number') {
          const request = this.pending.get(message.id);
          if (!request) return;
          clearTimeout(request.timer);
          this.pending.delete(message.id);
          if (message.error) request.reject(new Error(message.error));
          else request.resolve(message.result);
        }
      });
      const fail = (error: Error): void => {
        if (this.worker !== child) return;
        if (!settled) { settled = true; reject(error); }
        for (const request of this.pending.values()) {
          clearTimeout(request.timer);
          request.reject(error);
        }
        this.pending.clear();
        this.worker = undefined;
        this.workerReady = undefined;
      };
      child.once('error', fail);
      child.once('close', (code) => fail(new Error(`sema query worker exited with code ${code}`)));
    });
    return this.workerReady;
  }

  private async queryWorker<T>(payload: Record<string, unknown>): Promise<T> {
    await this.ensureWorker();
    const child = this.worker;
    if (!child) throw new Error('sema query worker is unavailable.');
    const id = this.nextRequestId++;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('sema query worker timed out.'));
      }, 60_000);
      this.pending.set(id, {
        resolve: (value) => resolve(value as T), reject, timer,
      });
      child.stdin.write(JSON.stringify({ id, ...payload }) + '\n');
    });
  }

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
        throw new SemaNotFoundError(this.binaryPath);
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
    let data: { results?: SearchResult[] };
    try {
      data = await this.queryWorker({ command: 'search', query, top_k: topK });
    } catch {
      data = await this.run<{ results?: SearchResult[] }>([
        'search', query, '--json', '--top-k', String(topK),
      ]);
    }
    return data.results ?? [];
  }

  async reuse(description: string): Promise<ReuseResult> {
    return this.run<ReuseResult>(['reuse', description, '--json']);
  }

  async get(symbol: string): Promise<CodeImplementation[]> {
    let data: { implementations?: CodeImplementation[] };
    try {
      data = await this.queryWorker({ command: 'get', symbol });
    } catch {
      data = await this.run<{ implementations?: CodeImplementation[] }>(['get', symbol, '--json']);
    }
    return data.implementations ?? [];
  }

  async status(): Promise<StatusResult> {
    return this.run<StatusResult>(['status', '--json']);
  }

  /** Build or refresh the index (runs `sema index . [--reset]`). Resolves on success. */
  async index(reset = false): Promise<void> {
    await this.runRaw(reset ? ['index', '.', '--reset'] : ['index', '.']);
    this.restartWorker();
  }

  private restartWorker(): void {
    this.worker?.kill();
    this.worker = undefined;
    this.workerReady = undefined;
    void this.ensureWorker().catch(() => {});
  }

  dispose(): void {
    this.worker?.kill();
    this.worker = undefined;
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
        throw new SemaNotFoundError(this.binaryPath);
      }
      throw new Error(
        (e.stderr || e.stdout || e.message || 'sema command failed.').toString().trim(),
      );
    }
  }
}
