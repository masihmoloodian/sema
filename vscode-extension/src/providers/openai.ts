import { OpenAICompatibleProvider } from './openai-compatible';

/**
 * OpenAI via the official SDK — the OpenAI-compatible base with no baseURL
 * override (the SDK points at api.openai.com by default). Models track the
 * current GPT-5.6 family (Sol = flagship, Terra = balanced, Luna = cost-efficient;
 * `gpt-5.6` is an alias for Sol). Cost is estimated from public list prices ($/1M
 * tokens); unknown models get no estimate, and "+ custom id…" reaches any other
 * model (e.g. gpt-5.5, gpt-5.4-mini).
 */
export const openaiProvider = new OpenAICompatibleProvider({
  id: 'openai',
  label: 'OpenAI',
  secretKey: 'sema.apiKey.openai',
  keyHint: 'platform.openai.com',
  modelHint: 'e.g. gpt-5.6, gpt-5.5-pro, gpt-5.4-mini',
  models: ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'],
  defaultModel: 'gpt-5.6-sol',
  // The GPT-5 family is multimodal and reads PDFs via the `file` content part.
  accepts: ['image', 'pdf', 'text'],
  prices: {
    'gpt-5.6-sol': { in: 5, cachedIn: 0.5, out: 30 },
    'gpt-5.6': { in: 5, cachedIn: 0.5, out: 30 }, // alias for gpt-5.6-sol
    'gpt-5.6-terra': { in: 2.5, cachedIn: 0.25, out: 15 },
    'gpt-5.6-luna': { in: 1, cachedIn: 0.1, out: 6 },
    'gpt-5.5': { in: 5, cachedIn: 0.5, out: 30 },
    'gpt-5.5-pro': { in: 30, out: 180 },
    'gpt-5.4': { in: 2.5, cachedIn: 0.25, out: 15 },
    'gpt-5.4-mini': { in: 0.75, cachedIn: 0.075, out: 4.5 },
    'gpt-5.4-nano': { in: 0.2, cachedIn: 0.02, out: 1.25 },
  },
});
