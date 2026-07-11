import Anthropic from '@anthropic-ai/sdk';
import { ChatProvider, StreamOptions } from './types';

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

/**
 * Claude via the official Anthropic SDK. The call runs in the Node extension
 * host (never the webview), so the API key is never exposed to page context and
 * there is no browser CORS constraint.
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
  readonly defaultModel = 'claude-opus-4-8';
  readonly efforts = ['default'];
  readonly requiresKey = true;
  readonly readsWorkspace = false;
  readonly secretKey = 'sema.apiKey.anthropic';
  readonly keyHint = 'console.anthropic.com';

  async stream(opts: StreamOptions): Promise<void> {
    const client = new Anthropic({ apiKey: opts.apiKey });
    const stream = client.messages.stream(
      {
        model: opts.model,
        max_tokens: opts.maxTokens,
        system: opts.system,
        messages: opts.messages.map((m) => ({ role: m.role, content: m.content })),
      },
      { signal: opts.signal },
    );
    stream.on('text', (delta) => opts.onDelta(delta));
    const final = await stream.finalMessage();
    if (opts.onUsage && final.usage) {
      const input = final.usage.input_tokens ?? 0;
      const output = final.usage.output_tokens ?? 0;
      const price = PRICES[opts.model];
      const costUsd = price ? (input * price.in + output * price.out) / 1e6 : undefined;
      opts.onUsage({ inputTokens: input, outputTokens: output, costUsd });
    }
  }
}
