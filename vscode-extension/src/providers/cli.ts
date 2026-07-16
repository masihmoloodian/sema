import { spawn } from 'child_process';
import { promises as fs, Dirent } from 'fs';
import * as os from 'os';
import * as path from 'path';
import type { SDKMessage } from '@anthropic-ai/claude-agent-sdk';
import {
  Attachment,
  AttachmentKind,
  ChatMessage,
  ChatProvider,
  ModelInfo,
  StreamOptions,
  TokenUsage,
} from './types';
import { pathFor } from '../attachments';

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

/**
 * The attachments a CLI needs for this invocation.
 *
 * Turn-scoped, mirroring how the prompt itself is built: on `--resume` the CLI already
 * holds the earlier turns and we send only the newest one, so re-sending every previous
 * turn's files would duplicate them. A fresh run replays the whole transcript — which
 * is also the path taken after an error clears the resume handle — so it needs them all.
 */
function attachmentsFor(opts: StreamOptions): Attachment[] {
  const turns = opts.sessionId ? opts.messages.slice(-1) : opts.messages;
  return turns.flatMap((m) => m.attachments ?? []);
}

/** Absolute staged paths for the given attachments, optionally filtered to one kind. */
function pathsOf(opts: StreamOptions, atts: Attachment[], kind?: AttachmentKind): string[] {
  const dir = opts.attachmentsDir;
  if (!dir) {
    return [];
  }
  return atts.filter((a) => !kind || a.kind === kind).map((a) => pathFor(dir, a.id));
}

/**
 * Build the prompt for a CLI that reads attachments off disk by path (Claude Code).
 * Also covers the attachment-only turn: `flattenPrompt` would return an empty string,
 * which would then be spawned as an empty positional argument and rejected.
 */
function promptWithPaths(opts: StreamOptions, paths: string[]): string {
  const base = opts.sessionId
    ? opts.messages[opts.messages.length - 1]?.content ?? ''
    : flattenPrompt(opts.system, opts.messages);
  if (!paths.length) {
    return base;
  }
  const list = paths.map((p) => `- ${p}`).join('\n');
  const lead = base.trim()
    ? `${base}\n\nAttached file(s) — read them before answering:`
    : 'Look at the attached file(s):';
  return `${lead}\n${list}`;
}

/** Fallback text so an attachment-only turn never spawns an empty prompt argument. */
function promptOrDefault(prompt: string, atts: Attachment[]): string {
  if (prompt.trim()) {
    return prompt;
  }
  return atts.length
    ? `Look at the attached file(s): ${atts.map((a) => a.name).join(', ')}`
    : prompt;
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

/** Claude tools that can change the workspace or execute arbitrary commands. */
export function isClaudeProtectedTool(tool: string): boolean {
  return ['Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'Bash'].includes(tool);
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
  /** Only the CLIs with an effort argument declare this — see ChatProvider.efforts. */
  readonly efforts?: readonly string[];
  readonly requiresKey = false;
  readonly readsWorkspace = true;

  abstract accepts(model: string): readonly AttachmentKind[];

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
        const finish = (): void => {
          if (opts.signal.aborted) {
            resolve();
          } else if (errText) {
            reject(new Error(errText));
          } else if (code !== 0) {
            reject(new Error(stderr.trim().split('\n').pop() || `${bin} exited with code ${code}`));
          } else {
            resolve();
          }
        };
        if (!reportedModel && opts.onModel && !opts.signal.aborted) {
          // The stream carried no model (Codex); resolve it out-of-band.
          //
          // This runs before the success/failure branch on purpose: a run that *fails*
          // still resolved a model, and that is exactly when the user needs to know
          // which one. Reporting only on exit 0 left the picker showing a stale
          // "Default (…)" from some earlier successful run, contradicting an error
          // naming the model Codex actually chose.
          this.resolveModelAfterStream(resolvedSession, opts)
            .then((m) => {
              if (m && opts.onModel) {
                opts.onModel(m);
              }
            })
            .catch(() => {})
            .finally(finish);
        } else {
          finish();
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
    { id: 'opus', alias: 'opus', name: 'Opus 4.8' },
    { id: 'fable', alias: 'fable', name: 'Fable 5' },
    { id: 'sonnet', alias: 'sonnet', name: 'Sonnet 5', recommended: true },
    { id: 'haiku', alias: 'haiku', name: 'Haiku 4.5' },
  ];
  readonly defaultModel = 'sonnet';
  // Mirrors `claude --help`: "--effort <level>  Effort level for the current session
  // (low, medium, high, xhigh, max)". 'max' is Claude-only — Codex rejects it.
  // 'default' means pass no --effort and let the CLI decide.
  readonly efforts = ['default', 'low', 'medium', 'high', 'xhigh', 'max'];
  readonly permissionModes = ['ask', 'bypass'] as const;
  readonly auth = { login: ['auth', 'login'], logout: ['auth', 'logout'], status: ['auth', 'status'] };

  /** Claude Code's Read tool handles images and PDFs as well as text. */
  accepts(): readonly AttachmentKind[] {
    return ['image', 'pdf', 'text'];
  }

  override async stream(opts: StreamOptions): Promise<void> {
    if (opts.agent && opts.permissionMode === 'ask') {
      await this.streamWithApprovals(opts);
      return;
    }
    await super.stream(opts);
  }

  /** Anthropic's Agent SDK supplies the bidirectional per-tool permission callback. */
  private async streamWithApprovals(opts: StreamOptions): Promise<void> {
    const { query: claudeQuery } = await import('@anthropic-ai/claude-agent-sdk');
    const atts = attachmentsFor(opts);
    const paths = pathsOf(opts, atts);
    const prompt = promptWithPaths(
      { ...opts, system: '' },
      paths,
    );
    const abortController = new AbortController();
    const abort = (): void => abortController.abort();
    opts.signal.addEventListener('abort', abort, { once: true });
    const reported = { session: false, model: false, usage: false };
    const seenActivities = new Set<string>();

    try {
      const messages = claudeQuery({
        prompt,
        options: {
          abortController,
          additionalDirectories: paths.length && opts.attachmentsDir ? [opts.attachmentsDir] : undefined,
          canUseTool: async (toolName, input, context) => {
            const decision = opts.onPermissionRequest
              ? await opts.onPermissionRequest({
                  provider: 'claude-code',
                  title: context.title || `Claude Code wants to use ${toolName}`,
                  detail:
                    context.description ||
                    context.decisionReason ||
                    describeTool(toolName, input) ||
                    undefined,
                  tool: toolName,
                })
              : 'deny';
            return decision === 'allow'
              ? {
                  behavior: 'allow' as const,
                  updatedInput: input,
                  toolUseID: context.toolUseID,
                  decisionClassification: 'user_temporary' as const,
                }
              : {
                  behavior: 'deny' as const,
                  message: 'The user rejected this action in sema.',
                  toolUseID: context.toolUseID,
                  decisionClassification: 'user_reject' as const,
                };
          },
          hooks: {
            PreToolUse: [{
              hooks: [async (input) => {
                if (
                  input.hook_event_name !== 'PreToolUse' ||
                  !isClaudeProtectedTool(input.tool_name)
                ) {
                  return {};
                }
                const toolInput =
                  input.tool_input && typeof input.tool_input === 'object'
                    ? input.tool_input as Record<string, unknown>
                    : {};
                const decision = opts.onPermissionRequest
                  ? await opts.onPermissionRequest({
                      provider: 'claude-code',
                      title: `Claude Code wants to use ${input.tool_name}`,
                      detail: describeTool(input.tool_name, toolInput) || undefined,
                      tool: input.tool_name,
                    })
                  : 'deny';
                return {
                  hookSpecificOutput: {
                    hookEventName: 'PreToolUse' as const,
                    permissionDecision: decision,
                    permissionDecisionReason:
                      decision === 'allow'
                        ? 'The user allowed this action in sema.'
                        : 'The user rejected this action in sema.',
                  },
                };
              }],
            }],
          },
          cwd: opts.cwd,
          effort:
            opts.effort && opts.effort !== 'default'
              ? (opts.effort as 'low' | 'medium' | 'high' | 'xhigh' | 'max')
              : undefined,
          includePartialMessages: true,
          model: opts.model && opts.model !== 'default' ? opts.model : undefined,
          pathToClaudeCodeExecutable: opts.cliBin,
          permissionMode: 'default',
          resume: opts.sessionId,
          skills: 'all',
          systemPrompt: { type: 'preset', preset: 'claude_code', append: opts.system },
        },
      });

      for await (const message of messages) {
        this.consumeSdkMessage(
          message,
          opts,
          seenActivities,
          reported,
        );
      }
    } finally {
      opts.signal.removeEventListener('abort', abort);
    }

  }

  private consumeSdkMessage(
    message: SDKMessage,
    opts: StreamOptions,
    seenActivities: Set<string>,
    reported: { session: boolean; model: boolean; usage: boolean },
  ): void {
    const event = message as unknown;
    const session = this.extractSession(event);
    if (!reported.session && session && opts.onSession) {
      opts.onSession(session);
      reported.session = true;
    }
    const model = this.extractModel(event);
    if (!reported.model && model && opts.onModel) {
      opts.onModel(model);
      reported.model = true;
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
      for (const activity of this.extractActivities(event)) {
        if (activity.id && seenActivities.has(activity.id)) {
          continue;
        }
        if (activity.id) {
          seenActivities.add(activity.id);
        }
        opts.onActivity(activity.tool, activity.detail);
      }
    }
    const usage = this.extractUsage(event);
    if (!reported.usage && usage && opts.onUsage) {
      opts.onUsage(usage);
      reported.usage = true;
    }
    const sdkResult = message as { type?: string; is_error?: boolean; errors?: string[] };
    if (sdkResult.type === 'result' && sdkResult.is_error && sdkResult.errors?.length) {
      throw new Error(sdkResult.errors.join('\n'));
    }
    const error = this.checkError(event);
    if (error) {
      throw new Error(error);
    }
    if (sdkResult.type === 'result' && sdkResult.is_error) {
      throw new Error(sdkResult.errors?.join('\n') || 'Claude Code returned an error.');
    }
  }

  protected buildInvocation(opts: StreamOptions): { bin: string; args: string[]; prompt: string } {
    const atts = attachmentsFor(opts);
    const paths = pathsOf(opts, atts);
    const args: string[] = [];
    if (paths.length && opts.attachmentsDir) {
      // Claude Code has no attach flag, so we hand it absolute paths in the prompt and
      // let its Read tool open them — which needs the staged dir allow-listed, since it
      // lives outside the workspace. `--add-dir` takes <directories...>, so it MUST be
      // followed by another flag: left last it swallows the trailing prompt argument.
      args.push('--add-dir', opts.attachmentsDir);
    }
    args.push('-p', '--output-format', 'stream-json', '--include-partial-messages', '--verbose');
    if (opts.sessionId) {
      args.push('--resume', opts.sessionId);
    }
    if (opts.plan) {
      // Plan mode: explore read-only and present a plan; edits are blocked.
      args.push('--permission-mode', 'plan');
    } else if (opts.agent) {
      // Approval mode uses the Agent SDK above. This CLI path is the explicit,
      // dangerous bypass selected by the user in the extension.
      args.push('--dangerously-skip-permissions');
    } else {
      // Ask is conversational, not an agent run. An empty allow-list prevents Claude
      // Code from inspecting the workspace despite being an agentic CLI internally.
      args.push('--tools', '');
    }
    if (opts.model && opts.model !== 'default') {
      args.push('--model', opts.model);
    }
    if (opts.effort && opts.effort !== 'default') {
      args.push('--effort', opts.effort);
    }
    // Fresh: system + full history. Resume: only the new user turn (the session holds
    // the rest). Attachment paths are appended for whichever set of turns that covers.
    return { bin: 'claude', args, prompt: promptWithPaths(opts, paths) };
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
  // Models and reasoning levels reported by `codex debug models` in CLI 0.144.5.
  // codex-auto-review is an internal review model and is deliberately not offered.
  readonly modelInfos: ModelInfo[] = [
    { id: 'gpt-5.6-sol', name: 'GPT-5.6 Sol', recommended: true,
      efforts: ['default', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
    { id: 'gpt-5.6-terra', name: 'GPT-5.6 Terra',
      efforts: ['default', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
    { id: 'gpt-5.6-luna', name: 'GPT-5.6 Luna',
      efforts: ['default', 'low', 'medium', 'high', 'xhigh', 'max'] },
    { id: 'gpt-5.5', name: 'GPT-5.5', efforts: ['default', 'low', 'medium', 'high', 'xhigh'] },
    { id: 'gpt-5.4', name: 'GPT-5.4', efforts: ['default', 'low', 'medium', 'high', 'xhigh'] },
    { id: 'gpt-5.4-mini', name: 'GPT-5.4 Mini', efforts: ['default', 'low', 'medium', 'high', 'xhigh'] },
  ];
  readonly defaultModel = 'gpt-5.6-sol';
  // Codex has no --effort flag; effort is config, passed as
  // `-c model_reasoning_effort=<level>`. Parsing locally is not enough to qualify a
  // Provider-wide union for custom ids; curated models narrow this via ModelInfo.efforts.
  readonly efforts = ['default', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
  readonly permissionModes = ['ask', 'bypass'] as const;
  readonly auth = { login: ['login'], logout: ['logout'], status: ['login', 'status'] };

  /** `codex exec` attaches images only (`-i`); its Read tool is text. No PDF path. */
  accepts(): readonly AttachmentKind[] {
    return ['image', 'text'];
  }

  override async stream(opts: StreamOptions): Promise<void> {
    if (opts.agent && opts.permissionMode === 'ask') {
      await this.streamWithApprovals(opts);
      return;
    }
    await super.stream(opts);
  }

  /** Codex app-server is the official bidirectional transport used by rich clients. */
  private async streamWithApprovals(opts: StreamOptions): Promise<void> {
    const exe = opts.cliBin && opts.cliBin.trim() ? opts.cliBin : 'codex';
    const child = spawn(exe, ['app-server'], {
      cwd: opts.cwd || undefined,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');

    let nextId = 1;
    let buffer = '';
    let stderr = '';
    let settled = false;
    let lastUsage: TokenUsage | null = null;
    const pending = new Map<
      number,
      { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void }
    >();
    let finishResolve!: () => void;
    let finishReject!: (error: Error) => void;
    const finished = new Promise<void>((resolve, reject) => {
      finishResolve = resolve;
      finishReject = reject;
    });

    const fail = (error: Error): void => {
      if (!settled) {
        settled = true;
        finishReject(error);
      }
    };
    const complete = (): void => {
      if (!settled) {
        settled = true;
        finishResolve();
      }
    };
    const send = (message: Record<string, unknown>): void => {
      if (!child.stdin.destroyed) {
        child.stdin.write(`${JSON.stringify(message)}\n`);
      }
    };
    const request = (method: string, params: Record<string, unknown>): Promise<Record<string, unknown>> => {
      const id = nextId++;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        send({ method, id, params });
      });
    };

    const decide = async (
      request: { title: string; detail?: string; tool?: string },
    ): Promise<'allow' | 'deny'> => {
      return opts.onPermissionRequest
        ? opts.onPermissionRequest({ provider: 'codex', ...request })
        : 'deny';
    };

    const handleServerRequest = async (message: {
      id: number | string;
      method: string;
      params?: Record<string, unknown>;
    }): Promise<void> => {
      const params = message.params ?? {};
      if (message.method === 'item/commandExecution/requestApproval') {
        const command = String(params.command ?? '');
        const cwd = String(params.cwd ?? '');
        const reason = String(params.reason ?? '');
        const decision = await decide({
          title: 'Codex wants to run a command',
          detail: [command, cwd ? `Working directory: ${cwd}` : '', reason].filter(Boolean).join('\n\n'),
          tool: command || 'Command execution',
        });
        send({ id: message.id, result: { decision: decision === 'allow' ? 'accept' : 'decline' } });
        return;
      }
      if (message.method === 'item/fileChange/requestApproval') {
        const reason = String(params.reason ?? '');
        const root = String(params.grantRoot ?? '');
        const decision = await decide({
          title: 'Codex wants to modify files',
          detail: [reason, root ? `Requested write root: ${root}` : ''].filter(Boolean).join('\n\n'),
          tool: 'File change',
        });
        send({ id: message.id, result: { decision: decision === 'allow' ? 'accept' : 'decline' } });
        return;
      }
      if (message.method === 'item/permissions/requestApproval') {
        const requested = (params.permissions ?? {}) as Record<string, unknown>;
        const decision = await decide({
          title: 'Codex wants additional access',
          detail: String(params.reason ?? '') || JSON.stringify(requested),
          tool: 'Permission escalation',
        });
        send({
          id: message.id,
          result: {
            permissions: decision === 'allow' ? requested : {},
            scope: 'turn',
          },
        });
        return;
      }
      if (message.method === 'execCommandApproval') {
        const command = Array.isArray(params.command)
          ? params.command.map(String).join(' ')
          : String(params.command ?? '');
        const decision = await decide({
          title: 'Codex wants to run a command',
          detail: [command, String(params.reason ?? '')].filter(Boolean).join('\n\n'),
          tool: command || 'Command execution',
        });
        send({ id: message.id, result: { decision: decision === 'allow' ? 'approved' : 'denied' } });
        return;
      }
      if (message.method === 'applyPatchApproval') {
        const files = Object.keys((params.fileChanges ?? {}) as Record<string, unknown>);
        const decision = await decide({
          title: 'Codex wants to modify files',
          detail: [files.join('\n'), String(params.reason ?? '')].filter(Boolean).join('\n\n'),
          tool: 'File change',
        });
        send({ id: message.id, result: { decision: decision === 'allow' ? 'approved' : 'denied' } });
        return;
      }
      if (message.method === 'item/tool/requestUserInput') {
        // Sema's composer cannot answer a second, in-flight questionnaire yet. Dismiss
        // it explicitly so the Codex turn can continue instead of hanging forever.
        send({ id: message.id, result: { answers: {} } });
        return;
      }
      send({
        id: message.id,
        error: { code: -32601, message: `Unsupported Codex app-server request: ${message.method}` },
      });
    };

    const handleMessage = (message: Record<string, unknown>): void => {
      const method = typeof message.method === 'string' ? message.method : '';
      if (method && message.id !== undefined) {
        void handleServerRequest(message as {
          id: number | string;
          method: string;
          params?: Record<string, unknown>;
        }).catch((error: Error) => fail(error));
        return;
      }
      if (typeof message.id === 'number') {
        const waiter = pending.get(message.id);
        if (waiter) {
          pending.delete(message.id);
          const rpcError = message.error as { message?: string } | undefined;
          if (rpcError) {
            waiter.reject(new Error(rpcError.message || 'Codex app-server request failed.'));
          } else {
            waiter.resolve((message.result ?? {}) as Record<string, unknown>);
          }
          return;
        }
      }

      const params = (message.params ?? {}) as Record<string, unknown>;
      if (method === 'item/agentMessage/delta') {
        const delta = String(params.delta ?? '');
        if (delta) {
          opts.onDelta(delta);
        }
      } else if (
        (method === 'item/reasoning/textDelta' || method === 'item/reasoning/summaryTextDelta') &&
        opts.onThinking
      ) {
        const delta = String(params.delta ?? '');
        if (delta) {
          opts.onThinking(delta);
        }
      } else if (method === 'item/started' && opts.onActivity) {
        const item = (params.item ?? {}) as Record<string, unknown>;
        if (item.type === 'commandExecution') {
          opts.onActivity('Run', String(item.command ?? '').slice(0, 60));
        } else if (item.type === 'fileChange') {
          opts.onActivity('Edit', '');
        }
      } else if (method === 'thread/tokenUsage/updated') {
        const tokenUsage = (params.tokenUsage ?? {}) as Record<string, unknown>;
        const last = (tokenUsage.last ?? {}) as Record<string, unknown>;
        lastUsage = {
          inputTokens: Number(last.inputTokens ?? 0),
          outputTokens: Number(last.outputTokens ?? 0) + Number(last.reasoningOutputTokens ?? 0),
          cachedInputTokens: Number(last.cachedInputTokens ?? 0),
        };
      } else if (method === 'turn/completed') {
        const turn = (params.turn ?? {}) as Record<string, unknown>;
        if (turn.status === 'failed') {
          const error = (turn.error ?? {}) as Record<string, unknown>;
          fail(new Error(String(error.message ?? 'Codex reported a failed turn.')));
        } else {
          if (lastUsage && opts.onUsage) {
            opts.onUsage(lastUsage);
          }
          complete();
        }
      } else if (method === 'error') {
        const error = (params.error ?? params) as Record<string, unknown>;
        fail(new Error(String(error.message ?? 'Codex app-server reported an error.')));
      }
    };

    child.stdout.on('data', (chunk: string) => {
      buffer += chunk;
      let newline: number;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) {
          continue;
        }
        try {
          handleMessage(JSON.parse(line) as Record<string, unknown>);
        } catch {
          // Ignore non-protocol output; app-server diagnostics are also captured on stderr.
        }
      }
    });
    child.stderr.on('data', (chunk: string) => { stderr += chunk; });
    child.on('error', (error: NodeJS.ErrnoException) => {
      fail(
        error.code === 'ENOENT'
          ? new Error(`${exe} not found — install it and log in, or set its path in sema settings.`)
          : error,
      );
    });
    child.on('close', (code) => {
      if (opts.signal.aborted) {
        complete();
      } else if (!settled) {
        fail(new Error(stderr.trim().split('\n').pop() || `codex app-server exited with code ${code}`));
      }
    });
    const abort = (): void => {
      child.kill();
      complete();
    };
    opts.signal.addEventListener('abort', abort, { once: true });

    try {
      await request('initialize', {
        clientInfo: { name: 'sema_vscode', title: 'sema VS Code Extension', version: '0.5.0' },
      });
      send({ method: 'initialized', params: {} });
      const effort = opts.effort && opts.effort !== 'default' ? opts.effort : undefined;
      const threadParams: Record<string, unknown> = {
        cwd: opts.cwd,
        approvalPolicy: 'on-request',
        approvalsReviewer: 'user',
        sandbox: 'read-only',
      };
      if (opts.model && opts.model !== 'default') {
        threadParams.model = opts.model;
      }
      if (effort) {
        threadParams.config = { model_reasoning_effort: effort };
      }
      if (opts.sessionId) {
        threadParams.threadId = opts.sessionId;
      }
      const threadResult = await request(
        opts.sessionId ? 'thread/resume' : 'thread/start',
        threadParams,
      );
      const thread = (threadResult.thread ?? {}) as Record<string, unknown>;
      const threadId = String(thread.id ?? opts.sessionId ?? '');
      if (!threadId) {
        throw new Error('Codex app-server did not return a thread id.');
      }
      opts.onSession?.(threadId);
      const resolvedModel = String(threadResult.model ?? '');
      if (resolvedModel) {
        opts.onModel?.(resolvedModel);
      }

      const atts = attachmentsFor(opts);
      const images = pathsOf(opts, atts, 'image');
      const prompt = opts.sessionId
        ? promptOrDefault(opts.messages[opts.messages.length - 1]?.content ?? '', atts)
        : promptOrDefault(flattenPrompt(opts.system, opts.messages), atts);
      const input: Array<Record<string, unknown>> = [
        { type: 'text', text: prompt, text_elements: [] },
        ...images.map((imagePath) => ({ type: 'localImage', path: imagePath })),
      ];
      await request('turn/start', {
        threadId,
        input,
        approvalPolicy: 'on-request',
        approvalsReviewer: 'user',
        cwd: opts.cwd,
        model: opts.model && opts.model !== 'default' ? opts.model : undefined,
        effort,
      });
      await finished;
    } finally {
      opts.signal.removeEventListener('abort', abort);
      for (const waiter of pending.values()) {
        waiter.reject(new Error('Codex app-server connection closed.'));
      }
      pending.clear();
      child.kill();
    }
  }

  protected buildInvocation(opts: StreamOptions): { bin: string; args: string[]; prompt: string } {
    const effort =
      opts.effort && opts.effort !== 'default'
        ? ['-c', `model_reasoning_effort=${opts.effort}`]
        : [];
    const atts = attachmentsFor(opts);
    // `-i` takes <FILE>..., so one flag with many values would greedily swallow the
    // trailing positionals — the session id and the prompt. Repeat the flag instead.
    const images = pathsOf(opts, atts, 'image').flatMap((p) => ['-i', p]);
    if (opts.sessionId) {
      // Resume — the session keeps its model/sandbox; send only the new user turn.
      return {
        bin: 'codex',
        args: [
          'exec',
          'resume',
          '--json',
          '--skip-git-repo-check',
          ...(opts.agent && opts.permissionMode === 'bypass'
            ? ['--dangerously-bypass-approvals-and-sandbox']
            : []),
          ...effort,
          ...images,
          opts.sessionId,
        ],
        prompt: promptOrDefault(opts.messages[opts.messages.length - 1]?.content ?? '', atts),
      };
    }
    const args = [
      'exec',
      '--json',
      '--skip-git-repo-check',
      ...(opts.agent && opts.permissionMode === 'bypass'
        ? ['--dangerously-bypass-approvals-and-sandbox']
        : []),
      ...effort,
      ...images,
      ...(opts.agent && opts.permissionMode === 'bypass' ? [] : ['--sandbox', 'read-only']),
    ];
    if (opts.model && opts.model !== 'default') {
      args.push('-m', opts.model);
    }
    return {
      bin: 'codex',
      args,
      prompt: promptOrDefault(flattenPrompt(opts.system, opts.messages), atts),
    };
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

  protected checkError(event: unknown): string | null {
    const o = event as { type?: string; error?: { message?: string } };
    if (o?.type !== 'turn.failed' && o?.type !== 'error') {
      return null;
    }
    const raw = o.error?.message;
    if (!raw) {
      return o.type === 'turn.failed' ? 'Codex reported a failed turn.' : null;
    }
    return unwrapCodexError(raw);
  }
}

/**
 * Pull the human-readable message out of a Codex error.
 *
 * Codex nests the upstream API error as a JSON *string* inside `error.message`, e.g.
 * `{"type":"error","status":400,"error":{"message":"The 'gpt-5.6-terra' model requires
 * a newer version of Codex…"}}`. Surfacing that beats the alternative: Codex also logs
 * unrelated warnings to stderr (a models-cache parse failure, say), and without this the
 * run's only reported error is whatever line happened to land there last.
 */
function unwrapCodexError(raw: string): string {
  try {
    const o = JSON.parse(raw) as { error?: { message?: string }; message?: string };
    return o.error?.message ?? o.message ?? raw;
  } catch {
    return raw; // not JSON — already human-readable
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
  // reaches the rest. `accepts` marks what each model can actually read; entries without
  // one fall back to image+text.
  readonly modelInfos: ModelInfo[] = [
    { id: 'opencode/claude-opus-4-8', name: 'Claude Opus 4.8', accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/claude-sonnet-5', name: 'Claude Sonnet 5', recommended: true, accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/claude-fable-5', name: 'Claude Fable 5', accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/claude-haiku-4-5', name: 'Claude Haiku 4.5', accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/gpt-5.2-codex', name: 'GPT-5.2 Codex' },
    { id: 'opencode/gpt-5.6-sol', name: 'GPT-5.6 Sol', accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/gpt-5.4-mini', name: 'GPT-5.4 Mini' },
    { id: 'opencode/gemini-3.1-pro', name: 'Gemini 3.1 Pro', accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/gemini-3-flash', name: 'Gemini 3 Flash', accepts: ['image', 'pdf', 'text'] },
    { id: 'opencode/deepseek-v4-pro', name: 'DeepSeek V4 Pro', accepts: ['text'] },
    { id: 'opencode/deepseek-v4-flash-free', name: 'DeepSeek V4 Flash (free)', accepts: ['text'] },
    { id: 'opencode/glm-5.2', name: 'GLM 5.2', accepts: ['text'] },
    { id: 'opencode/qwen3.6-plus', name: 'Qwen3.6 Plus', accepts: ['text'] },
    { id: 'opencode/kimi-k2.7-code', name: 'Kimi K2.7 Code', accepts: ['text'] },
    { id: 'opencode/grok-4.5', name: 'Grok 4.5' },
    { id: 'opencode/minimax-m3', name: 'MiniMax M3', accepts: ['text'] },
  ];
  readonly defaultModel = 'opencode/claude-sonnet-5';
  // No `efforts`: `opencode run` exposes no reasoning-effort argument.
  readonly modelHint =
    'provider/model — run `opencode models` (e.g. anthropic/claude-sonnet-4-6, openai/gpt-5, opencode/gpt-5.1-codex)';
  readonly auth = { login: ['auth', 'login'], logout: ['auth', 'logout'], status: ['auth', 'list'] };

  /**
   * `opencode run -f` will attach any file, but whether the *model* can read it is
   * per-model — opencode is a gateway to ~55 of them, like OpenRouter and Together.
   * Claiming vision for all of them means a silent "I cannot view images" reply from a
   * text-only model instead of an up-front explanation.
   */
  accepts(model: string): readonly AttachmentKind[] {
    return this.modelInfos.find((m) => m.id === model)?.accepts ?? ['image', 'text'];
  }

  protected buildInvocation(opts: StreamOptions): { bin: string; args: string[]; prompt: string } {
    const atts = attachmentsFor(opts);
    // `-f` is an array option, so repeat it per file and keep it ahead of another flag —
    // a greedy array would otherwise absorb the trailing message positional.
    const files = pathsOf(opts, atts).flatMap((p) => ['-f', p]);
    // --auto auto-approves permissions so a non-interactive run never hangs. Agent mode
    // uses the full-capability `build` agent; Ask/Plan use the read-only `plan` agent.
    const args = ['run', ...files, '--format', 'json', '--auto', '--agent', opts.agent ? 'build' : 'plan'];
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
    return { bin: 'opencode', args, prompt: promptOrDefault(prompt, atts) };
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
