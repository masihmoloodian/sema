import { ClaudeCodeProvider, CodexProvider } from './cli';
import { AnthropicProvider } from './anthropic';
import { OpenAIProvider } from './openai';
import { ChatProvider } from './types';

export * from './types';

// Local CLI providers first — they reuse an existing login, so no key needed.
export const PROVIDERS: ChatProvider[] = [
  new ClaudeCodeProvider(),
  new CodexProvider(),
  new AnthropicProvider(),
  new OpenAIProvider(),
];

export function getProvider(id: string | undefined): ChatProvider {
  return PROVIDERS.find((p) => p.id === id) ?? PROVIDERS[0];
}
