import Anthropic from '@anthropic-ai/sdk';
import { AttachmentKind, ChatMessage, ChatProvider, ModelInfo, StreamOptions } from './types';
import { toAnthropicTools, executeTool, toolDetail, ToolContext, toolsForMode } from './tools';
import { readBase64 } from '../attachments';

/** Public list prices (USD per 1M tokens) for cost estimation; unknown models get no estimate. */
const PRICES: Record<string, { in: number; out: number }> = {
  'claude-fable-5': { in: 10, out: 50 },
  'claude-opus-4-8': { in: 5, out: 25 },
  'claude-opus-4-7': { in: 5, out: 25 },
  'claude-opus-4-6': { in: 5, out: 25 },
  'claude-sonnet-5': { in: 3, out: 15 },
  'claude-sonnet-4-6': { in: 3, out: 15 },
  'claude-haiku-4-5': { in: 1, out: 5 },
};

/** Anthropic bills cache-read input at ~0.1× the fresh input rate; approximate that in estimates. */
const CACHE_READ_MULTIPLIER = 0.1;

/** Cap on agent tool-call rounds per turn — a backstop against a looping model. */
const MAX_AGENT_STEPS = 50;

/** Every current Claude model reads images and PDFs. */
const ACCEPTS: readonly AttachmentKind[] = ['image', 'pdf', 'text'];

/**
 * Render a turn as content blocks. Attachments come first (the API expects `document`
 * blocks ahead of the text that refers to them), text last. Plain string content when
 * there is nothing attached, so the common case stays byte-identical to before.
 *
 * The last attachment block is marked `cache_control: ephemeral`: the full history is
 * re-sent on every turn and on every agent-loop step, so without a breakpoint a 3MB
 * screenshot is re-billed at full input price each round.
 */
async function toContent(
  m: ChatMessage,
  dir: string | undefined,
): Promise<string | Anthropic.ContentBlockParam[]> {
  const atts = m.attachments ?? [];
  if (!atts.length || !dir) {
    return m.content;
  }
  // Narrower than ContentBlockParam: only these three carry `cache_control`.
  const blocks: (Anthropic.ImageBlockParam | Anthropic.DocumentBlockParam | Anthropic.TextBlockParam)[] = [];
  for (const a of atts) {
    const data = await readBase64(dir, a);
    if (a.kind === 'pdf') {
      blocks.push({
        type: 'document',
        source: { type: 'base64', media_type: 'application/pdf', data },
      });
    } else if (a.kind === 'image') {
      blocks.push({
        type: 'image',
        source: {
          type: 'base64',
          media_type: a.mime as 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp',
          data,
        },
      });
    }
  }
  if (!blocks.length) {
    return m.content;
  }
  blocks[blocks.length - 1].cache_control = { type: 'ephemeral' };
  if (m.content.trim()) {
    blocks.push({ type: 'text', text: m.content });
  }
  return blocks;
}

/** Map a whole transcript to Anthropic message params. */
async function toMessages(
  messages: readonly ChatMessage[],
  dir: string | undefined,
): Promise<Anthropic.MessageParam[]> {
  return Promise.all(
    messages.map(async (m) => ({ role: m.role, content: await toContent(m, dir) })),
  );
}

/** Running token/cost totals, accumulated across a turn (which may span several tool steps). */
interface UsageTotals {
  input: number;
  output: number;
  cached: number;
}

/**
 * Claude via the official Anthropic SDK. The call runs in the Node extension host
 * (never the webview), so the API key is never exposed to page context and there is
 * no browser CORS constraint.
 *
 * In Agent/Plan mode Claude is given the same workspace tools as the OpenAI-compatible
 * providers (see providers/tools.ts) and sema runs an agentic loop — streaming the
 * model's text, executing each tool call against the workspace, feeding results back,
 * and repeating — so Claude can actually explore, edit, and run, not just describe.
 * Ask mode is a single streamed completion.
 */
export class AnthropicProvider implements ChatProvider {
  readonly id = 'anthropic';
  readonly label = 'Claude (Anthropic)';
  // Current model IDs; Opus 4.8 is the default per Anthropic guidance.
  readonly models = [
    'claude-opus-4-8',
    'claude-sonnet-5',
    'claude-haiku-4-5',
    'claude-opus-4-7',
    'claude-fable-5',
  ];
  get modelInfos(): ModelInfo[] {
    return this.models.map((id) => ({ id }));
  }
  readonly defaultModel = 'claude-opus-4-8';
  // No `efforts`: effort is a Claude Code / Codex CLI flag, not a Messages API parameter.
  readonly requiresKey = true;
  readonly readsWorkspace = false;
  readonly secretKey = 'sema.apiKey.anthropic';
  readonly keyHint = 'console.anthropic.com';
  readonly modelHint = 'e.g. claude-opus-4-8, claude-sonnet-5, claude-fable-5';

  accepts(): readonly AttachmentKind[] {
    return ACCEPTS;
  }

  async stream(opts: StreamOptions): Promise<void> {
    const client = new Anthropic({ apiKey: opts.apiKey });
    if (opts.agent || opts.plan) {
      // Agent = full read/write/run toolset; Plan = read-only investigation tools.
      await this.runAgent(client, opts);
    } else {
      await this.runChat(client, opts);
    }
  }

  /** Single-pass chat: stream text and report usage. Used for Ask mode. */
  private async runChat(client: Anthropic, opts: StreamOptions): Promise<void> {
    const stream = client.messages.stream(
      {
        model: opts.model,
        max_tokens: opts.maxTokens,
        system: opts.system,
        messages: await toMessages(opts.messages, opts.attachmentsDir),
      },
      { signal: opts.signal },
    );
    stream.on('text', (delta) => opts.onDelta(delta));
    const final = await stream.finalMessage();
    if (opts.onModel && final.model) {
      opts.onModel(final.model);
    }
    const totals = newTotals();
    fold(totals, final.usage);
    this.report(opts, totals);
  }

  /**
   * Agentic loop: offer the file/command tools and, each round, stream the model's
   * text + any tool calls, execute the tools on the workspace, feed the results back
   * as tool_result blocks, and repeat until Claude stops calling tools (or the step
   * cap is hit). Mirrors the OpenAI-compatible agent, over the Messages API.
   */
  private async runAgent(client: Anthropic, opts: StreamOptions): Promise<void> {
    const readOnly = !opts.agent;
    const tools = toAnthropicTools(toolsForMode(readOnly, !!opts.semaBin));
    const ctx: ToolContext = { cwd: opts.cwd || process.cwd(), readOnly, semaBin: opts.semaBin };
    const messages: Anthropic.MessageParam[] = await toMessages(opts.messages, opts.attachmentsDir);
    const totals = newTotals();
    let reportedModel = false;

    for (let step = 0; step < MAX_AGENT_STEPS; step++) {
      const stream = client.messages.stream(
        { model: opts.model, max_tokens: opts.maxTokens, system: opts.system, messages, tools },
        { signal: opts.signal },
      );
      stream.on('text', (delta) => opts.onDelta(delta));

      let msg: Anthropic.Message;
      try {
        msg = await stream.finalMessage();
      } catch (e) {
        if (opts.signal.aborted) {
          break; // user stopped — keep whatever streamed so far
        }
        throw e;
      }

      if (!reportedModel && opts.onModel && msg.model) {
        reportedModel = true;
        opts.onModel(msg.model);
      }
      fold(totals, msg.usage);

      const toolUses = msg.content.filter(
        (b): b is Anthropic.ToolUseBlock => b.type === 'tool_use',
      );
      if (!toolUses.length || opts.signal.aborted) {
        break; // no tools requested → the turn is done
      }

      // Record the assistant's turn (text + tool_use), then execute each call and
      // append its result. Rebuild as *param* blocks so only text/tool_use are echoed.
      const assistantContent: Anthropic.ContentBlockParam[] = [];
      for (const b of msg.content) {
        if (b.type === 'text') {
          assistantContent.push({ type: 'text', text: b.text });
        } else if (b.type === 'tool_use') {
          assistantContent.push({ type: 'tool_use', id: b.id, name: b.name, input: b.input });
        }
      }
      messages.push({ role: 'assistant', content: assistantContent });

      const results: Anthropic.ToolResultBlockParam[] = [];
      for (const tu of toolUses) {
        const input = (tu.input ?? {}) as Record<string, unknown>;
        if (opts.onActivity) {
          opts.onActivity(tu.name, toolDetail(tu.name, input));
        }
        const result = await executeTool(tu.name, input, ctx);
        results.push({ type: 'tool_result', tool_use_id: tu.id, content: result });
      }
      messages.push({ role: 'user', content: results });
      if (opts.signal.aborted) {
        break;
      }
    }

    this.report(opts, totals);
  }

  /** Emit accumulated usage, estimating cost from the price table (cache-read discounted). */
  private report(opts: StreamOptions, t: UsageTotals): void {
    if (!opts.onUsage) {
      return;
    }
    const price = PRICES[opts.model];
    const costUsd = price
      ? ((t.input - t.cached) * price.in + t.cached * price.in * CACHE_READ_MULTIPLIER + t.output * price.out) / 1e6
      : undefined;
    opts.onUsage({
      inputTokens: t.input,
      outputTokens: t.output,
      cachedInputTokens: t.cached,
      costUsd,
    });
  }
}

function newTotals(): UsageTotals {
  return { input: 0, output: 0, cached: 0 };
}

/** Fold one Anthropic usage object into the running totals (input includes cache tokens). */
function fold(t: UsageTotals, usage: Anthropic.Usage): void {
  const cacheRead = usage.cache_read_input_tokens ?? 0;
  t.input += (usage.input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0) + cacheRead;
  t.output += usage.output_tokens ?? 0;
  t.cached += cacheRead;
}
