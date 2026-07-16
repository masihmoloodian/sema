import OpenAI from 'openai';
import { AttachmentKind, ChatMessage, ChatProvider, ModelInfo, StreamOptions } from './types';
import { executeTool, toolDetail, ToolContext, toolsForMode } from './tools';
import { readBase64, toDataUri } from '../attachments';

/** Public list price (USD per 1M tokens) for a model, used to estimate cost. */
export interface ModelPrice {
  in: number;
  out: number;
  /** Price for cached (re-read) input tokens, when the provider bills them cheaper than fresh input. */
  cachedIn?: number;
}

export interface OpenAICompatConfig {
  id: string;
  label: string;
  /** Flat model-id list (openai, deepseek). Use modelInfos for a rich/sectioned list. */
  models?: string[];
  /** Rich model metadata (openrouter, together) — takes precedence over `models`. */
  modelInfos?: ModelInfo[];
  defaultModel: string;
  secretKey: string;
  keyHint: string;
  /** Example model id(s) for the custom-model-id prompt (hints the id format). */
  modelHint?: string;
  /** OpenAI-compatible base URL; omit for OpenAI itself (the SDK default). */
  baseURL?: string;
  /** Extra default headers sent on every request (e.g. OpenRouter app attribution). */
  headers?: Record<string, string>;
  /** Per-model price table for cost estimation; models absent here get no estimate. */
  prices?: Record<string, ModelPrice>;
  /** True when the API returns a real `usage.cost` (OpenRouter) — use it instead of estimating. */
  costFromResponse?: boolean;
  /**
   * Attachment kinds this provider's models read by default; 'text' is implicit. A
   * `ModelInfo.accepts` entry overrides this for one model. Omit for text-only
   * providers (DeepSeek).
   */
  accepts?: readonly AttachmentKind[];
}

/** Superset of the OpenAI usage shape, covering DeepSeek's cache fields and OpenRouter's cost. */
interface UsageShape {
  prompt_tokens?: number;
  completion_tokens?: number;
  /** OpenAI-style cached input split. */
  prompt_tokens_details?: { cached_tokens?: number };
  /** DeepSeek-style cached input split. */
  prompt_cache_hit_tokens?: number;
  /** OpenRouter reports the real dollar cost of the call here. */
  cost?: number;
}

/** Running token/cost totals, accumulated across a turn (which may span several tool steps). */
interface UsageTotals {
  input: number;
  output: number;
  cached: number;
  cost: number;
  costKnown: boolean;
}

type Messages = OpenAI.Chat.Completions.ChatCompletionMessageParam[];

/**
 * Render a turn as content parts. Both `image_url.url` and `file.file_data` must be
 * data URIs here (unlike Anthropic, which wants bare base64), and `filename` is
 * required alongside `file_data`. Plain string content when nothing is attached.
 */
async function toContent(
  m: ChatMessage,
  dir: string | undefined,
): Promise<string | OpenAI.Chat.Completions.ChatCompletionContentPart[]> {
  const atts = m.attachments ?? [];
  if (!atts.length || !dir) {
    return m.content;
  }
  const parts: OpenAI.Chat.Completions.ChatCompletionContentPart[] = [];
  if (m.content.trim()) {
    parts.push({ type: 'text', text: m.content });
  }
  for (const a of atts) {
    const uri = toDataUri(a.mime, await readBase64(dir, a));
    if (a.kind === 'image') {
      parts.push({ type: 'image_url', image_url: { url: uri } });
    } else if (a.kind === 'pdf') {
      parts.push({ type: 'file', file: { filename: a.name, file_data: uri } });
    }
  }
  return parts.length ? parts : m.content;
}

/** Cap on agent tool-call rounds per turn — a backstop against a looping model. */
const MAX_AGENT_STEPS = 50;

/**
 * Shared transport for OpenAI-compatible chat providers (OpenAI, DeepSeek,
 * OpenRouter). They differ only in base URL, key, headers, model list, and how
 * cost is derived — so one streaming implementation serves all three, configured
 * from an {@link OpenAICompatConfig}. Runs in the Node extension host, so the API
 * key never reaches webview/page context.
 *
 * In Agent mode the model is given file/command tools (see {@link AGENT_TOOLS})
 * and sema runs an agentic loop, executing each tool call on the workspace — so a
 * bare chat model can actually create/edit files, not just describe how.
 */
export class OpenAICompatibleProvider implements ChatProvider {
  // No `efforts`: these providers take a plain chat-completions request with no
  // reasoning-effort argument to pass through.
  readonly requiresKey = true;
  readonly readsWorkspace = false;

  constructor(private readonly cfg: OpenAICompatConfig) {}

  get id(): string {
    return this.cfg.id;
  }
  get label(): string {
    return this.cfg.label;
  }
  get modelInfos(): ModelInfo[] {
    return this.cfg.modelInfos ?? (this.cfg.models ?? []).map((id) => ({ id }));
  }
  get models(): string[] {
    return this.modelInfos.map((m) => m.id);
  }
  get defaultModel(): string {
    return this.cfg.defaultModel;
  }
  get secretKey(): string {
    return this.cfg.secretKey;
  }
  get keyHint(): string {
    return this.cfg.keyHint;
  }
  get modelHint(): string | undefined {
    return this.cfg.modelHint;
  }

  accepts(model: string): readonly AttachmentKind[] {
    const info = this.modelInfos.find((m) => m.id === model);
    return info?.accepts ?? this.cfg.accepts ?? ['text'];
  }

  async stream(opts: StreamOptions): Promise<void> {
    const client = new OpenAI({
      apiKey: opts.apiKey,
      baseURL: this.cfg.baseURL,
      defaultHeaders: this.cfg.headers,
    });
    const messages: Messages = [
      { role: 'system', content: opts.system },
      ...(await Promise.all(
        opts.messages.map(async (m) =>
          m.role === 'user'
            ? { role: 'user' as const, content: await toContent(m, opts.attachmentsDir) }
            : { role: 'assistant' as const, content: m.content },
        ),
      )),
    ];
    if (opts.agent || opts.plan) {
      // Agent = full read/write/run toolset; Plan = read-only investigation tools.
      const readOnly = !opts.agent;
      const tools = toolsForMode(readOnly, !!opts.semaBin);
      await this.runAgent(client, opts, messages, tools, readOnly);
    } else {
      await this.runChat(client, opts, messages);
    }
  }

  /** Single-pass chat: stream text and report usage. Used for Ask/Plan mode. */
  private async runChat(client: OpenAI, opts: StreamOptions, messages: Messages): Promise<void> {
    const totals = newTotals();
    let reportedModel = false;
    const stream = await client.chat.completions.create(
      { model: opts.model, max_tokens: opts.maxTokens, messages, stream: true, stream_options: { include_usage: true } },
      { signal: opts.signal },
    );
    for await (const chunk of stream) {
      if (!reportedModel && opts.onModel && chunk.model) {
        reportedModel = true;
        opts.onModel(chunk.model);
      }
      const delta = chunk.choices[0]?.delta?.content;
      if (delta) {
        opts.onDelta(delta);
      }
      if (chunk.usage) {
        this.fold(totals, chunk.usage as UsageShape);
      }
    }
    this.report(opts, totals);
  }

  /**
   * Agentic loop: offer the file/command tools, and each round stream the model's
   * text + any tool calls, execute the tools on the workspace, feed the results
   * back, and repeat until the model stops calling tools (or the step cap is hit).
   */
  private async runAgent(
    client: OpenAI,
    opts: StreamOptions,
    messages: Messages,
    tools: OpenAI.Chat.Completions.ChatCompletionTool[],
    readOnly: boolean,
  ): Promise<void> {
    const totals = newTotals();
    const ctx: ToolContext = { cwd: opts.cwd || process.cwd(), readOnly, semaBin: opts.semaBin };
    let reportedModel = false;

    for (let step = 0; step < MAX_AGENT_STEPS; step++) {
      const stream = await client.chat.completions.create(
        {
          model: opts.model,
          max_tokens: opts.maxTokens,
          messages,
          tools,
          stream: true,
          stream_options: { include_usage: true },
        },
        { signal: opts.signal },
      );

      let text = '';
      // Tool calls arrive as fragments across chunks, keyed by index — accumulate them.
      const calls: Record<number, { id: string; name: string; args: string }> = {};
      for await (const chunk of stream) {
        if (!reportedModel && opts.onModel && chunk.model) {
          reportedModel = true;
          opts.onModel(chunk.model);
        }
        const delta = chunk.choices[0]?.delta;
        if (delta?.content) {
          text += delta.content;
          opts.onDelta(delta.content);
        }
        for (const tc of delta?.tool_calls ?? []) {
          const i = tc.index ?? 0;
          const c = (calls[i] ??= { id: '', name: '', args: '' });
          if (tc.id) {
            c.id = tc.id;
          }
          if (tc.function?.name) {
            c.name = tc.function.name;
          }
          if (tc.function?.arguments) {
            c.args += tc.function.arguments;
          }
        }
        if (chunk.usage) {
          this.fold(totals, chunk.usage as UsageShape);
        }
      }

      const toolCalls = Object.values(calls).filter((c) => c.id && c.name);
      if (!toolCalls.length || opts.signal.aborted) {
        break; // no tools requested → the turn is done
      }

      // Record the assistant's tool-call turn, then execute each call and append its result.
      messages.push({
        role: 'assistant',
        content: text || null,
        tool_calls: toolCalls.map((c) => ({
          id: c.id,
          type: 'function' as const,
          function: { name: c.name, arguments: c.args || '{}' },
        })),
      });
      for (const c of toolCalls) {
        let args: Record<string, unknown> = {};
        try {
          args = c.args ? JSON.parse(c.args) : {};
        } catch {
          // Leave args empty; executeTool will report the missing field.
        }
        if (opts.onActivity) {
          opts.onActivity(c.name, toolDetail(c.name, args));
        }
        const result = await executeTool(c.name, args, ctx);
        messages.push({ role: 'tool', tool_call_id: c.id, content: result });
      }
      if (opts.signal.aborted) {
        break;
      }
    }

    this.report(opts, totals);
  }

  /** Fold one usage object into the running totals (handles the OpenAI/DeepSeek cache split). */
  private fold(t: UsageTotals, usage: UsageShape): void {
    t.input += usage.prompt_tokens ?? 0;
    t.output += usage.completion_tokens ?? 0;
    t.cached += usage.prompt_tokens_details?.cached_tokens ?? usage.prompt_cache_hit_tokens ?? 0;
    if (this.cfg.costFromResponse && typeof usage.cost === 'number') {
      t.cost += usage.cost;
      t.costKnown = true;
    }
  }

  /** Emit accumulated usage, deriving cost from the response (OpenRouter) or the price table. */
  private report(opts: StreamOptions, t: UsageTotals): void {
    if (!opts.onUsage) {
      return;
    }
    let costUsd: number | undefined = t.costKnown ? t.cost : undefined;
    if (costUsd === undefined) {
      const p = this.cfg.prices?.[opts.model];
      if (p) {
        costUsd = ((t.input - t.cached) * p.in + t.cached * (p.cachedIn ?? p.in) + t.output * p.out) / 1e6;
      }
    }
    opts.onUsage({
      inputTokens: t.input,
      outputTokens: t.output,
      cachedInputTokens: t.cached,
      costUsd,
    });
  }
}

function newTotals(): UsageTotals {
  return { input: 0, output: 0, cached: 0, cost: 0, costKnown: false };
}
