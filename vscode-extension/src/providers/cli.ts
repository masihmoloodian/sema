import { spawn } from 'child_process';
import { promises as fs, Dirent } from 'fs';
import * as os from 'os';
import * as path from 'path';
import { ChatMessage, ChatProvider, ModelInfo, StreamOptions, TokenUsage } from './types';

/** Flatten the system context + conversation into a single prompt string for a CLI. */
function flattenPrompt(system: string, messages: ChatMessage[]): string {
  const parts: string[] = [];
  if (system.trim()) {
    parts.push(system.trim(), '');
  }
  if (messages.length === 1 && messages[0].role === 'user') {
    // Single user turn (the common first-turn case) — pass it through verbatim, with
    // no "User:" scaffolding, so the CLI sees exactly what the user typed.
    parts.push(messages[0].content);
  } else {
    for (const m of messages) {
      parts.push((m.role === 'user' ? 'User: ' : 'Assistant: ') + m.content, '');
    }
  }
  return parts.join('\n').trim();
}

function basename(p: unknown): string {
  const s = typeof p === 'string' ? p : '';
  return s.split('/').pop() || s;
}

/** A short human label for a tool call, e.g. Read → "package.json", Bash → the command. */
function describeTool(tool: string, input: Record<string, unknown>): string {
  switch (tool) {
    case 'Read':
    case 'Edit':
    case 'Write':
    case 'NotebookEdit':
      return basename(input.file_path);
    case 'Bash':
      return String(input.command ?? '').slice(0, 60);
    case 'Grep':
    case 'Glob':
      return String(input.pattern ?? '');
    default:
      return '';
  }
}

interface Activity {
  id?: string;
  tool: string;
  detail: string;
}

/**
 * Base for providers that shell out to a locally-installed, already-authenticated
 * CLI (Claude Code / Codex). No API key — they reuse the user's existing login.
 * Each CLI emits JSONL; subclasses say how to pull text deltas and errors out of it.
 */
abstract class CliProvider implements ChatProvider {
  abstract readonly id: string;
  abstract readonly label: string;
  abstract readonly modelInfos: ModelInfo[];
  abstract readonly defaultModel: string;
  abstract readonly efforts: string[];
  readonly requiresKey = false;
  readonly readsWorkspace = true;

  /** Flat model-id list, derived from the rich modelInfos. */
  get models(): string[] {
    return this.modelInfos.map((m) => m.id);
  }

  protected abstract buildInvocation(opts: StreamOptions): {
    bin: string;
    args: string[];
    prompt: string;
  };
  protected abstract extractDelta(event: unknown): string | null;
  protected abstract extractThinking(event: unknown): string | null;
  protected abstract extractActivities(event: unknown): Activity[];
  protected abstract extractSession(event: unknown): string | null;
  protected abstract extractModel(event: unknown): string | null;
  protected abstract extractUsage(event: unknown): TokenUsage | null;
  protected abstract checkError(event: unknown): string | null;

  /**
   * Some CLIs (Codex) never emit the model in their JSON stream, so
   * {@link extractModel} can't see it. After a run finishes cleanly, a provider may
   * override this to resolve the model another way (e.g. from a session log), keyed
   * by the session id from {@link extractSession}. Returns null when unavailable.
   */
  protected async resolveModelAfterStream(
    _session: string | null,
    _opts: StreamOptions,
  ): Promise<string | null> {
    return null;
  }

  async stream(opts: StreamOptions): Promise<void> {
    const { bin, args, prompt } = this.buildInvocation(opts);
    const exe = opts.cliBin && opts.cliBin.trim() ? opts.cliBin : bin;

    return new Promise<void>((resolve, reject) => {
      const child = spawn(exe, [...args, prompt], {
        cwd: opts.cwd || undefined,
        signal: opts.signal,
      });

      let buf = '';
      let stderr = '';
      let errText = '';
      let resolvedSession: string | null = null;
      let reportedModel = false;
      let reportedUsage = false;
      const seen = new Set<string>();

      child.stdin.end();
      child.stdout.setEncoding('utf8');
      child.stdout.on('data', (chunk: string) => {
        buf += chunk;
        let nl: number;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) {
            continue;
          }
          let event: unknown;
          try {
            event = JSON.parse(line);
          } catch {
            continue;
          }
          const err = this.checkError(event);
          if (err) {
            errText = err;
          }
          const delta = this.extractDelta(event);
          if (delta) {
            opts.onDelta(delta);
          }
          const thinking = this.extractThinking(event);
          if (thinking && opts.onThinking) {
            opts.onThinking(thinking);
          }
          if (opts.onActivity) {
            for (const a of this.extractActivities(event)) {
              if (a.id && seen.has(a.id)) {
                continue;
              }
              if (a.id) {
                seen.add(a.id);
              }
              opts.onActivity(a.tool, a.detail);
            }
          }
          if (!resolvedSession) {
            const sid = this.extractSession(event);
            if (sid) {
              resolvedSession = sid;
              if (opts.onSession) {
                opts.onSession(sid);
              }
            }
          }
          if (!reportedModel && opts.onModel) {
            const md = this.extractModel(event);
            if (md) {
              reportedModel = true;
              opts.onModel(md);
            }
          }
          if (!reportedUsage && opts.onUsage) {
            const usage = this.extractUsage(event);
            if (usage) {
              reportedUsage = true;
              opts.onUsage(usage);
            }
          }
        }
      });

      child.stderr.setEncoding('utf8');
      child.stderr.on('data', (c: string) => {
        stderr += c;
      });

      child.on('error', (e: NodeJS.ErrnoException) => {
        if (e.code === 'ENOENT') {
          reject(new Error(`${exe} not found — install it and log in, or set its path in sema settings.`));
        } else {
          reject(e);
        }
      });

      child.on('close', (code) => {
        if (opts.signal.aborted) {
          resolve();
        } else if (errText) {
          reject(new Error(errText));
        } else if (code !== 0) {
          reject(new Error(stderr.trim().split('\n').pop() || `${bin} exited with code ${code}`));
        } else if (!reportedModel && opts.onModel) {
          // The stream carried no model (Codex); resolve it out-of-band, then finish.
          this.resolveModelAfterStream(resolvedSession, opts)
            .then((m) => {
              if (m && opts.onModel) {
                opts.onModel(m);
              }
            })
            .catch(() => {})
            .finally(() => resolve());
        } else {
          resolve();
        }
      });
    });
  }
}

/** Claude Code — `claude -p` headless mode with real token streaming. */
export class ClaudeCodeProvider extends CliProvider {
  readonly id = 'claude-code';
  readonly label = 'Claude Code (local)';
  // Invocation values are the CLI aliases (always resolve to the latest of each tier),
  // with the current version shown as the display name. `claude --model` accepts these.
  readonly modelInfos: ModelInfo[] = [
    { id: 'default', name: 'Default', description: 'Recommended by Claude Code', recommended: true },
    { id: 'opus', alias: 'opus', name: 'Opus 4.8' },
    { id: 'fable', alias: 'fable', name: 'Fable 5' },
    { id: 'sonnet', alias: 'sonnet', name: 'Sonnet 5' },
    { id: 'haiku', alias: 'haiku', name: 'Haiku 4.5' },
  ];
  readonly defaultModel = 'default';
  // `claude --effort` accepts low/medium/high/xhigh/max ('default' = the CLI's own).
  readonly efforts = ['default', 'low', 'medium', 'high', 'xhigh', 'max'];
  readonly auth = { login: ['auth', 'login'], logout: ['auth', 'logout'], status: ['auth', 'status'] };

  protected buildInvocation(opts: StreamOptions): { bin: string; args: string[]; prompt: string } {
    const args = ['-p', '--output-format', 'stream-json', '--include-partial-messages', '--verbose'];
    if (opts.sessionId) {
      args.push('--resume', opts.sessionId);
    }
    if (opts.plan) {
      // Plan mode: explore read-only and present a plan; edits are blocked.
      args.push('--permission-mode', 'plan');
    } else if (opts.agent) {
      // Let it edit files; read-only otherwise (edits are denied non-interactively).
      args.push('--permission-mode', 'acceptEdits');
    }
    if (opts.model && opts.model !== 'default') {
      args.push('--model', opts.model);
    }
    if (opts.effort && opts.effort !== 'default') {
      args.push('--effort', opts.effort);
    }
    // Fresh: system + full history. Resume: only the new user turn (the session holds the rest).
    const prompt = opts.sessionId
      ? opts.messages[opts.messages.length - 1]?.content ?? ''
      : flattenPrompt(opts.system, opts.messages);
    return { bin: 'claude', args, prompt };
  }

  protected extractDelta(event: unknown): string | null {
    const o = event as { type?: string; event?: { type?: string; delta?: { type?: string; text?: string } } };
    if (o?.type === 'stream_event' && o.event?.type === 'content_block_delta') {
      const d = o.event.delta;
      if (d?.type === 'text_delta') {
        return d.text ?? null;
      }
    }
    return null;
  }

  protected extractThinking(event: unknown): string | null {
    const o = event as {
      type?: string;
      event?: { type?: string; delta?: { type?: string; thinking?: string } };
    };
    if (o?.type === 'stream_event' && o.event?.type === 'content_block_delta') {
      const d = o.event.delta;
      if (d?.type === 'thinking_delta') {
        return d.thinking ?? null;
      }
    }
    return null;
  }

  protected extractActivities(event: unknown): Activity[] {
    const o = event as {
      type?: string;
      message?: {
        content?: Array<{ type?: string; name?: string; id?: string; input?: Record<string, unknown> }>;
      };
    };
    if (o?.type === 'assistant' && Array.isArray(o.message?.content)) {
      const acts: Activity[] = [];
      for (const b of o.message.content) {
        if (b?.type === 'tool_use' && b.name) {
          acts.push({ id: b.id, tool: b.name, detail: describeTool(b.name, b.input || {}) });
        }
      }
      return acts;
    }
    return [];
  }

  protected extractSession(event: unknown): string | null {
    const o = event as { session_id?: string };
    return typeof o?.session_id === 'string' ? o.session_id : null;
  }

  protected extractModel(event: unknown): string | null {
    const o = event as { type?: string; model?: string };
    return o?.type === 'system' && typeof o.model === 'string' ? o.model : null;
  }

  protected extractUsage(event: unknown): TokenUsage | null {
    const o = event as {
      type?: string;
      total_cost_usd?: number;
      usage?: {
        input_tokens?: number;
        cache_creation_input_tokens?: number;
        cache_read_input_tokens?: number;
        output_tokens?: number;
      };
    };
    if (o?.type === 'result' && o.usage) {
      const u = o.usage;
      const input =
        (u.input_tokens ?? 0) +
        (u.cache_creation_input_tokens ?? 0) +
        (u.cache_read_input_tokens ?? 0);
      return {
        inputTokens: input,
        outputTokens: u.output_tokens ?? 0,
        cachedInputTokens: u.cache_read_input_tokens ?? 0,
        costUsd: o.total_cost_usd,
      };
    }
    return null;
  }

  protected checkError(event: unknown): string | null {
    const o = event as { type?: string; is_error?: boolean; result?: unknown };
    if (o?.type === 'result' && o.is_error) {
      return typeof o.result === 'string' ? o.result : 'Claude Code returned an error.';
    }
    return null;
  }
}

/** Codex — `codex exec --json` non-interactive; emits the answer as an agent_message item. */
export class CodexProvider extends CliProvider {
  readonly id = 'codex';
  readonly label = 'Codex (local)';
  // Models per `codex debug models` (Codex CLI 0.133): gpt-5.5 / gpt-5.4 / gpt-5.4-mini.
  // (gpt-5.6-sol/terra/luna are OpenAI API models — they belong to the `openai` provider,
  // not the Codex CLI.) 'default' lets Codex pick its own configured model.
  readonly modelInfos: ModelInfo[] = [
    { id: 'default', name: 'Default', description: 'Recommended by Codex', recommended: true },
    { id: 'gpt-5.5', name: 'GPT-5.5' },
    { id: 'gpt-5.4', name: 'GPT-5.4' },
    { id: 'gpt-5.4-mini', name: 'GPT-5.4 Mini' },
  ];
  readonly defaultModel = 'default';
  // codex reasoning efforts per `codex debug models` (low/medium/high/xhigh; default
  // = medium). Current models expose no 'minimal', and there is no 'max' (Claude-only).
  readonly efforts = ['default', 'low', 'medium', 'high', 'xhigh'];
  readonly auth = { login: ['login'], logout: ['logout'], status: ['login', 'status'] };

  protected buildInvocation(opts: StreamOptions): { bin: string; args: string[]; prompt: string } {
    const effort =
      opts.effort && opts.effort !== 'default'
        ? ['-c', `model_reasoning_effort=${opts.effort}`]
        : [];
    if (opts.sessionId) {
      // Resume — the session keeps its model/sandbox; send only the new user turn.
      return {
        bin: 'codex',
        args: ['exec', 'resume', '--json', '--skip-git-repo-check', ...effort, opts.sessionId],
        prompt: opts.messages[opts.messages.length - 1]?.content ?? '',
      };
    }
    const args = [
      'exec',
      '--json',
      '--skip-git-repo-check',
      ...effort,
      '--sandbox',
      opts.agent ? 'workspace-write' : 'read-only',
    ];
    if (opts.model && opts.model !== 'default') {
      args.push('-m', opts.model);
    }
    return { bin: 'codex', args, prompt: flattenPrompt(opts.system, opts.messages) };
  }

  protected extractDelta(event: unknown): string | null {
    const o = event as { type?: string; item?: { type?: string; text?: string } };
    if (o?.type === 'item.completed' && o.item?.type === 'agent_message') {
      return o.item.text ?? null;
    }
    return null;
  }

  protected extractThinking(event: unknown): string | null {
    const o = event as { type?: string; item?: { type?: string; text?: string } };
    if (o?.type === 'item.completed' && o.item?.type === 'reasoning') {
      return o.item.text ?? null;
    }
    return null;
  }

  protected extractActivities(event: unknown): Activity[] {
    const o = event as { type?: string; item?: { id?: string; type?: string; command?: string } };
    const it = o?.item;
    if (o?.type === 'item.completed' && it) {
      if (it.type === 'command_execution') {
        return [{ id: it.id, tool: 'Run', detail: String(it.command ?? '').slice(0, 60) }];
      }
      if (it.type === 'file_change') {
        return [{ id: it.id, tool: 'Edit', detail: '' }];
      }
    }
    return [];
  }

  protected extractSession(event: unknown): string | null {
    const o = event as { type?: string; thread_id?: string };
    return o?.type === 'thread.started' && typeof o.thread_id === 'string' ? o.thread_id : null;
  }

  protected extractModel(): string | null {
    // codex --json never emits the model in its stream; resolveModelAfterStream reads
    // it from the session rollout log once the run finishes.
    return null;
  }

  protected async resolveModelAfterStream(session: string | null): Promise<string | null> {
    return session ? readCodexModelFromRollout(session) : null;
  }

  protected extractUsage(event: unknown): TokenUsage | null {
    const o = event as {
      type?: string;
      usage?: {
        input_tokens?: number;
        cached_input_tokens?: number;
        output_tokens?: number;
        reasoning_output_tokens?: number;
      };
    };
    if (o?.type === 'turn.completed' && o.usage) {
      const u = o.usage;
      return {
        inputTokens: u.input_tokens ?? 0,
        outputTokens: (u.output_tokens ?? 0) + (u.reasoning_output_tokens ?? 0),
        cachedInputTokens: u.cached_input_tokens ?? 0,
      };
    }
    return null;
  }

  protected checkError(): string | null {
    return null;
  }
}

/**
 * Codex doesn't print its model in the `--json` stream, but it records the resolved
 * model in the session rollout log it writes under `$CODEX_HOME/sessions` (a JSONL
 * file whose name ends with the thread id). Given the thread id from `thread.started`,
 * find that log and return the model from its last `turn_context` event — that reflects
 * whatever `default` resolved to (config.toml, profile, or Codex's built-in default).
 * Returns null if the log can't be found, read, or parsed.
 */
async function readCodexModelFromRollout(threadId: string): Promise<string | null> {
  const home = process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
  const file = await findRolloutFile(path.join(home, 'sessions'), threadId);
  if (!file) {
    return null;
  }
  let text: string;
  try {
    text = await fs.readFile(file, 'utf8');
  } catch {
    return null;
  }
  let model: string | null = null;
  for (const line of text.split('\n')) {
    // `turn_context` is one line among many; skip the rest without parsing.
    if (!line.includes('turn_context')) {
      continue;
    }
    try {
      const o = JSON.parse(line) as { type?: string; payload?: { model?: unknown } };
      if (o.type === 'turn_context' && typeof o.payload?.model === 'string') {
        model = o.payload.model; // keep the last — the most recent turn wins.
      }
    } catch {
      // Ignore malformed lines.
    }
  }
  return model;
}

/**
 * Find `rollout-*-<threadId>.jsonl` under `dir`. Codex nests logs by date
 * (`sessions/YYYY/MM/DD/`), so recurse newest-first (dir names sort chronologically)
 * to find a just-written log quickly.
 */
async function findRolloutFile(dir: string, threadId: string): Promise<string | null> {
  const suffix = `-${threadId}.jsonl`;
  let entries: Dirent[];
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch {
    return null;
  }
  const subdirs: string[] = [];
  for (const e of entries) {
    if (e.isDirectory()) {
      subdirs.push(path.join(dir, e.name));
    } else if (e.isFile() && e.name.startsWith('rollout-') && e.name.endsWith(suffix)) {
      return path.join(dir, e.name);
    }
  }
  for (const sub of subdirs.sort().reverse()) {
    const found = await findRolloutFile(sub, threadId);
    if (found) {
      return found;
    }
  }
  return null;
}

/**
 * opencode — `opencode run --format json`, a non-interactive agentic run that emits
 * JSONL events (one per line: text / reasoning / tool_use / error, each carrying
 * `sessionID`). `--auto` auto-approves permissions so a headless run never blocks;
 * Agent mode uses the default `build` agent, Ask/Plan use the read-only `plan`
 * agent. The model is a `provider/model` slug (`opencode models` lists them) and its
 * provider must be signed in (`opencode auth login`) — though it ships free models
 * that work with no auth. JSON mode reports no model id; tokens/cost come from the
 * `step_finish` event.
 */
export class OpenCodeProvider extends CliProvider {
  readonly id = 'opencode';
  readonly label = 'Open Code (local)';
  // Curated subset of `opencode models` (the opencode/ catalog has ~55); "+ custom id…"
  // reaches the rest. 'default' lets opencode use its own configured model.
  readonly modelInfos: ModelInfo[] = [
    { id: 'default', name: 'Default', recommended: true },
    { id: 'opencode/claude-opus-4-8', name: 'Claude Opus 4.8' },
    { id: 'opencode/claude-sonnet-5', name: 'Claude Sonnet 5' },
    { id: 'opencode/claude-fable-5', name: 'Claude Fable 5' },
    { id: 'opencode/claude-haiku-4-5', name: 'Claude Haiku 4.5' },
    { id: 'opencode/gpt-5.2-codex', name: 'GPT-5.2 Codex' },
    { id: 'opencode/gpt-5.6-sol', name: 'GPT-5.6 Sol' },
    { id: 'opencode/gpt-5.4-mini', name: 'GPT-5.4 Mini' },
    { id: 'opencode/gemini-3.1-pro', name: 'Gemini 3.1 Pro' },
    { id: 'opencode/gemini-3-flash', name: 'Gemini 3 Flash' },
    { id: 'opencode/deepseek-v4-pro', name: 'DeepSeek V4 Pro' },
    { id: 'opencode/deepseek-v4-flash-free', name: 'DeepSeek V4 Flash (free)' },
    { id: 'opencode/glm-5.2', name: 'GLM 5.2' },
    { id: 'opencode/qwen3.6-plus', name: 'Qwen3.6 Plus' },
    { id: 'opencode/kimi-k2.7-code', name: 'Kimi K2.7 Code' },
    { id: 'opencode/grok-4.5', name: 'Grok 4.5' },
    { id: 'opencode/minimax-m3', name: 'MiniMax M3' },
  ];
  readonly defaultModel = 'default';
  readonly efforts = ['default'];
  readonly modelHint =
    'provider/model — run `opencode models` (e.g. anthropic/claude-sonnet-4-6, openai/gpt-5, opencode/gpt-5.1-codex)';
  readonly auth = { login: ['auth', 'login'], logout: ['auth', 'logout'], status: ['auth', 'list'] };

  protected buildInvocation(opts: StreamOptions): { bin: string; args: string[]; prompt: string } {
    // --auto auto-approves permissions so a non-interactive run never hangs. Agent mode
    // uses the full-capability `build` agent; Ask/Plan use the read-only `plan` agent.
    const args = ['run', '--format', 'json', '--auto', '--agent', opts.agent ? 'build' : 'plan'];
    if (opts.cwd) {
      // opencode's server-based run does NOT adopt the spawn cwd as its project root —
      // without this it works out of a temp dir. Point it at the workspace explicitly so
      // its file/bash tools operate there, like Claude Code / Codex do via their cwd.
      args.push('--dir', opts.cwd);
    }
    if (opts.sessionId) {
      args.push('--session', opts.sessionId);
    }
    if (opts.model && opts.model !== 'default') {
      args.push('--model', opts.model);
    }
    // Fresh: system + full history. Resume: only the new user turn (the session holds the rest).
    const prompt = opts.sessionId
      ? opts.messages[opts.messages.length - 1]?.content ?? ''
      : flattenPrompt(opts.system, opts.messages);
    return { bin: 'opencode', args, prompt };
  }

  protected extractDelta(event: unknown): string | null {
    const o = event as { type?: string; part?: { text?: string } };
    // A "text" event carries a completed text part; parts don't overlap, so appending
    // each part's full text reconstructs the answer without duplication.
    return o?.type === 'text' ? o.part?.text ?? null : null;
  }

  protected extractThinking(event: unknown): string | null {
    const o = event as { type?: string; part?: { text?: string } };
    return o?.type === 'reasoning' ? o.part?.text ?? null : null;
  }

  protected extractActivities(event: unknown): Activity[] {
    const o = event as {
      type?: string;
      part?: { id?: string; tool?: string; state?: { input?: Record<string, unknown> } };
    };
    if (o?.type === 'tool_use' && o.part?.tool) {
      const input = o.part.state?.input ?? {};
      const detail = String(
        input.filePath ?? input.path ?? input.command ?? input.pattern ?? '',
      ).slice(0, 60);
      return [{ id: o.part.id, tool: o.part.tool, detail }];
    }
    return [];
  }

  protected extractSession(event: unknown): string | null {
    const o = event as { sessionID?: string };
    return typeof o?.sessionID === 'string' ? o.sessionID : null;
  }

  protected extractModel(): string | null {
    // opencode's JSON stream doesn't report the model; the picker shows the selection.
    return null;
  }

  protected extractUsage(event: unknown): TokenUsage | null {
    const o = event as {
      type?: string;
      part?: {
        cost?: number;
        tokens?: {
          input?: number;
          output?: number;
          reasoning?: number;
          cache?: { read?: number; write?: number };
        };
      };
    };
    // opencode reports usage per step; the first step_finish covers a single-step turn.
    if (o?.type === 'step_finish' && o.part?.tokens) {
      const t = o.part.tokens;
      const cacheRead = t.cache?.read ?? 0;
      return {
        inputTokens: (t.input ?? 0) + cacheRead + (t.cache?.write ?? 0),
        outputTokens: (t.output ?? 0) + (t.reasoning ?? 0),
        cachedInputTokens: cacheRead,
        costUsd: typeof o.part.cost === 'number' ? o.part.cost : undefined,
      };
    }
    return null;
  }

  protected checkError(event: unknown): string | null {
    const o = event as { type?: string; error?: unknown };
    if (o?.type !== 'error') {
      return null;
    }
    const e = o.error as { data?: { message?: string }; message?: string } | string | undefined;
    if (typeof e === 'string') {
      return e;
    }
    return e?.data?.message ?? e?.message ?? 'opencode returned an error.';
  }
}
