import { OpenAICompatibleProvider } from './openai-compatible';

/**
 * Together AI — an OpenAI-compatible gateway to open models (Llama, DeepSeek,
 * Qwen, gpt-oss, …), addressed by `org/Model` slug. Same request/response shape
 * as OpenAI (base URL `https://api.together.ai/v1`); cost isn't returned, so it's
 * estimated from public list prices ($/1M tokens). The curated list plus
 * "+ custom id…" reaches the full catalogue (see together.ai/models).
 */
export const togetherProvider = new OpenAICompatibleProvider({
  id: 'together',
  label: 'Together AI',
  baseURL: 'https://api.together.ai/v1',
  secretKey: 'sema.apiKey.together',
  keyHint: 'api.together.ai/settings/api-keys',
  modelHint: 'org/Model slug — e.g. Qwen/Qwen3.6-Plus, deepseek-ai/DeepSeek-R1 (see together.ai/models)',
  models: [
    'meta-llama/Llama-3.3-70B-Instruct-Turbo',
    'deepseek-ai/DeepSeek-V4-Pro',
    'Qwen/Qwen3.6-Plus',
    'openai/gpt-oss-120b',
    'openai/gpt-oss-20b',
  ],
  defaultModel: 'meta-llama/Llama-3.3-70B-Instruct-Turbo',
  prices: {
    'meta-llama/Llama-3.3-70B-Instruct-Turbo': { in: 0.88, out: 0.88 },
    'deepseek-ai/DeepSeek-V4-Pro': { in: 2.1, out: 4.4 },
    'Qwen/Qwen3.6-Plus': { in: 0.5, out: 3.0 },
    'openai/gpt-oss-120b': { in: 0.15, out: 0.6 },
    'openai/gpt-oss-20b': { in: 0.05, out: 0.2 },
  },
});
