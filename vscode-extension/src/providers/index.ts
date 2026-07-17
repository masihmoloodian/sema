import { ClaudeCodeProvider, CodexProvider, GrokProvider, OpenCodeProvider } from './cli';
import { AnthropicProvider } from './anthropic';
import { openaiProvider } from './openai';
import { deepseekProvider } from './deepseek';
import { openrouterProvider } from './openrouter';
import { togetherProvider } from './together';
import { ChatProvider } from './types';

export * from './types';

// Local CLI providers first — they reuse an existing login, so no key needed.
// The key-based API providers follow.
export const PROVIDERS: ChatProvider[] = [
  new ClaudeCodeProvider(),
  new CodexProvider(),
  new OpenCodeProvider(),
  new GrokProvider(),
  new AnthropicProvider(),
  openaiProvider,
  deepseekProvider,
  openrouterProvider,
  togetherProvider,
];

export function getProvider(id: string | undefined): ChatProvider {
  return PROVIDERS.find((p) => p.id === id) ?? PROVIDERS[0];
}
