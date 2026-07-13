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
  models: [
    'anthropic/claude-sonnet-latest',
    'openai/gpt-latest',
    'deepseek/deepseek-chat',
    'google/gemini-2.5-pro',
    'meta-llama/llama-3.3-70b-instruct',
  ],
  defaultModel: 'anthropic/claude-sonnet-latest',
  costFromResponse: true,
});
