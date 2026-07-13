import { spawn } from 'child_process';
import { ChatMessage, ChatProvider, StreamOptions, TokenUsage } from './types';

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
  abstract readonly models: string[];
  abstract readonly defaultModel: string;
  abstract readonly efforts: string[];
  readonly requiresKey = false;
  readonly readsWorkspace = true;

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
      let reportedSession = false;
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
          if (!reportedSession && opts.onSession) {
            const sid = this.extractSession(event);
            if (sid) {
              reportedSession = true;
              opts.onSession(sid);
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
  readonly models = ['default', 'fable', 'opus', 'sonnet', 'haiku'];
  readonly defaultModel = 'default';
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
  readonly models = ['default', 'gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini'];
  readonly defaultModel = 'default';
  readonly efforts = ['default', 'minimal', 'low', 'medium', 'high', 'xhigh'];
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
    // codex --json does not report the model; the picker shows the selection instead.
    return null;
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
