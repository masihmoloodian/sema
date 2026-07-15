import { OpenAICompatibleProvider } from './openai-compatible';

/**
 * DeepSeek — OpenAI-compatible API (same request/response shape, different base
 * URL and key). DeepSeek does not return cost, so it is estimated from a
 * cache-aware price table: cache-hit input tokens bill at `cachedIn`, the rest at
 * the full `in` rate. Prices are USD per 1M tokens. `deepseek-chat` /
 * `deepseek-reasoner` still work as custom ids for the non-thinking / thinking
 * modes; the "+ custom id…" entry covers any model not listed here.
 */
export const deepseekProvider = new OpenAICompatibleProvider({
  id: 'deepseek',
  label: 'DeepSeek',
  baseURL: 'https://api.deepseek.com',
  secretKey: 'sema.apiKey.deepseek',
  keyHint: 'platform.deepseek.com/api_keys',
  modelHint: 'e.g. deepseek-v4-pro, deepseek-v4-flash',
  models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
  defaultModel: 'deepseek-v4-flash',
  // No `accepts`: DeepSeek's API models are text-only, so images/PDFs are refused up
  // front rather than 400-ing upstream.
  prices: {
    'deepseek-v4-flash': { in: 0.14, cachedIn: 0.0028, out: 0.28 },
    'deepseek-v4-pro': { in: 0.435, cachedIn: 0.003625, out: 0.87 },
    // Legacy aliases (map to v4-flash; retired 2026-07-24) — priced so a custom-id
    // entry of either still gets a cost estimate during the transition window.
    'deepseek-chat': { in: 0.14, cachedIn: 0.0028, out: 0.28 },
    'deepseek-reasoner': { in: 0.14, cachedIn: 0.0028, out: 0.28 },
  },
});
