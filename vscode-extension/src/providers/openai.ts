import OpenAI from 'openai';
import { ChatProvider, StreamOptions } from './types';

/** OpenAI via the official SDK, streaming chat completions. */
export class OpenAIProvider implements ChatProvider {
  readonly id = 'openai';
  readonly label = 'OpenAI';
  readonly models = ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini'];
  readonly defaultModel = 'gpt-4o';
  readonly efforts = ['default'];
  readonly requiresKey = true;
  readonly readsWorkspace = false;
  readonly secretKey = 'sema.apiKey.openai';
  readonly keyHint = 'platform.openai.com';

  async stream(opts: StreamOptions): Promise<void> {
    const client = new OpenAI({ apiKey: opts.apiKey });
    const messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [
      { role: 'system', content: opts.system },
      ...opts.messages.map((m) => ({ role: m.role, content: m.content })),
    ];
    const stream = await client.chat.completions.create(
      {
        model: opts.model,
        max_tokens: opts.maxTokens,
        messages,
        stream: true,
        stream_options: { include_usage: true },
      },
      { signal: opts.signal },
    );
    let usage: { prompt_tokens?: number; completion_tokens?: number } | undefined;
    for await (const chunk of stream) {
      const delta = chunk.choices[0]?.delta?.content;
      if (delta) {
        opts.onDelta(delta);
      }
      if (chunk.usage) {
        usage = chunk.usage;
      }
    }
    if (opts.onUsage && usage) {
      opts.onUsage({
        inputTokens: usage.prompt_tokens ?? 0,
        outputTokens: usage.completion_tokens ?? 0,
      });
    }
  }
}
