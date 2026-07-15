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
  // As with OpenRouter: images are allowed by default because a custom slug's modality
  // is unknowable, and the curated entries below carry an explicit `accepts`. Together's
  // catalogue is mostly text-only open models, so most of them say so.
  accepts: ['image', 'text'],
  modelInfos: [
    { id: 'deepseek-ai/DeepSeek-V3', name: 'DeepSeek V3', accepts: ['text'] },
    { id: 'deepseek-ai/DeepSeek-R1', name: 'DeepSeek R1', accepts: ['text'] },
    { id: 'Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8', name: 'Qwen3 Coder 480B', accepts: ['text'] },
    { id: 'Qwen/Qwen3-235B-A22B-fp8-tput', name: 'Qwen3 235B', accepts: ['text'] },
    { id: 'moonshotai/Kimi-K2-Instruct', name: 'Kimi K2', accepts: ['text'] },
    { id: 'zai-org/GLM-4.5', name: 'GLM 4.5', accepts: ['text'] },
    // The Llama 4 models are natively multimodal.
    { id: 'meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8', name: 'Llama 4 Maverick' },
    { id: 'meta-llama/Llama-4-Scout-17B-16E-Instruct', name: 'Llama 4 Scout' },
    { id: 'agentica-org/DeepCoder-14B-Preview', name: 'DeepCoder 14B', accepts: ['text'] },
    { id: 'Qwen/Qwen2.5-Coder-32B-Instruct', name: 'Qwen2.5 Coder 32B', accepts: ['text'] },
  ],
  defaultModel: 'deepseek-ai/DeepSeek-V3',
});
