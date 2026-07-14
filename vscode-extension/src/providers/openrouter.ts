import { OpenAICompatibleProvider } from './openai-compatible';

/**
 * OpenRouter — an OpenAI-compatible gateway to many models, addressed by
 * `provider/model` slug. Usage (including a real dollar `cost`) is always
 * streamed in the final chunk, so cost is reported, never estimated. The optional
 * HTTP-Referer / X-Title headers attribute traffic to this app on the OpenRouter
 * dashboard. The curated list below is a starting point; "+ custom id…" reaches
 * the full catalogue (see GET https://openrouter.ai/api/v1/models).
 */
export const openrouterProvider = new OpenAICompatibleProvider({
  id: 'openrouter',
  label: 'OpenRouter',
  baseURL: 'https://openrouter.ai/api/v1',
  headers: {
    'HTTP-Referer': 'https://github.com/masihmoloodian/sema',
    'X-Title': 'sema',
  },
  secretKey: 'sema.apiKey.openrouter',
  keyHint: 'openrouter.ai/keys',
  modelHint: 'provider/model slug — e.g. anthropic/claude-opus-4.8, x-ai/grok-4 (see openrouter.ai/models)',
  modelInfos: [
    { id: 'openai/gpt-5.2-codex', name: 'GPT-5.2 Codex' },
    { id: 'openai/gpt-5.1-codex', name: 'GPT-5.1 Codex' },
    { id: 'anthropic/claude-opus-4.8', name: 'Claude Opus 4.8' },
    { id: 'anthropic/claude-fable-5', name: 'Claude Fable 5' },
    { id: 'google/gemini-3-pro', name: 'Gemini 3 Pro' },
    { id: 'deepseek/deepseek-v4-pro', name: 'DeepSeek V4 Pro' },
    { id: 'qwen/qwen3-coder-plus', name: 'Qwen3 Coder Plus' },
    { id: 'z-ai/glm-5.2', name: 'GLM 5.2' },
    { id: 'minimax/m3', name: 'MiniMax M3' },
    { id: 'xiaomi/mimo-v2.5', name: 'MiMo V2.5' },
    { id: 'deepseek/deepseek-v4-flash', name: 'DeepSeek V4 Flash' },
    { id: 'qwen/qwen3-coder', name: 'Qwen3 Coder' },
    { id: 'google/gemma-3', name: 'Gemma 3' },
    { id: 'meta-llama/llama-4-scout', name: 'Llama 4 Scout' },
    { id: 'z-ai/glm-4.7-flash', name: 'GLM 4.7 Flash' },
  ],
  defaultModel: 'openai/gpt-5.2-codex',
  costFromResponse: true,
});
