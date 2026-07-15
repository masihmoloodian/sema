import { OpenAICompatibleProvider } from './openai-compatible';

/**
 * AvalAI — an OpenAI-compatible gateway (base URL `https://api.avalai.ir/v1`)
 * fronting models from several providers, addressed by bare model id rather than
 * a `provider/model` slug. Reachable from networks where the upstream provider
 * APIs are not, which is the reason it is here: it keeps sema usable when
 * api.anthropic.com and friends are unreachable.
 *
 * Cost is neither reported nor estimated. AvalAI returns an `estimated_cost` that
 * its own docs describe as approximate — exact figures require a second,
 * authenticated call to /user/v1/transactions/lookup keyed by `x-request-id`, which
 * this transport has no hook for. Reporting nothing beats reporting a number the
 * provider itself won't stand behind.
 *
 * The curated list below is a starting point; "+ custom id…" reaches the full
 * catalogue (see GET https://api.avalai.ir/public/models).
 */
export const avalaiProvider = new OpenAICompatibleProvider({
  id: 'avalai',
  label: 'AvalAI',
  baseURL: 'https://api.avalai.ir/v1',
  secretKey: 'sema.apiKey.avalai',
  keyHint: 'chat.avalai.ir/platform → API keys',
  modelHint: 'bare model id — e.g. claude-opus-4-8, gpt-5.5 (see api.avalai.ir/public/models)',
  // As with OpenRouter: images are allowed by default because a custom id's modality
  // is unknowable up front, and a wrong guess surfaces as a clear upstream error
  // rather than a silently refused attachment. Curated entries carry explicit
  // `accepts` where the modality is known.
  accepts: ['image', 'text'],
  modelInfos: [
    {
      id: 'claude-opus-4-8',
      name: 'Claude Opus 4.8',
      section: 'Anthropic',
      recommended: true,
      accepts: ['image', 'pdf', 'text'],
    },
    { id: 'claude-opus-4-7', name: 'Claude Opus 4.7', section: 'Anthropic', accepts: ['image', 'pdf', 'text'] },
    { id: 'claude-sonnet-4-6', name: 'Claude Sonnet 4.6', section: 'Anthropic', accepts: ['image', 'pdf', 'text'] },
    { id: 'claude-haiku-4-5', name: 'Claude Haiku 4.5', section: 'Anthropic', accepts: ['image', 'pdf', 'text'] },
    { id: 'gpt-5.5', name: 'GPT-5.5', section: 'OpenAI' },
    { id: 'gpt-5.4', name: 'GPT-5.4', section: 'OpenAI' },
    { id: 'gpt-5.4-mini', name: 'GPT-5.4 Mini', section: 'OpenAI' },
    { id: 'gpt-5.3-codex', name: 'GPT-5.3 Codex', section: 'OpenAI' },
    { id: 'gemini-3.5-flash', name: 'Gemini 3.5 Flash', section: 'Google', accepts: ['image', 'pdf', 'text'] },
    {
      id: 'gemini-3.1-pro-preview',
      name: 'Gemini 3.1 Pro',
      section: 'Google',
      accepts: ['image', 'pdf', 'text'],
    },
    { id: 'grok-4.3', name: 'Grok 4.3', section: 'xAI' },
    { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro', section: 'Open models', accepts: ['text'] },
    { id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash', section: 'Open models', accepts: ['text'] },
    { id: 'qwen3.7-max', name: 'Qwen3.7 Max', section: 'Open models', accepts: ['text'] },
    { id: 'kimi-k2.7-code', name: 'Kimi K2.7 Code', section: 'Open models', accepts: ['text'] },
    { id: 'glm-5.2', name: 'GLM 5.2', section: 'Open models', accepts: ['text'] },
  ],
  defaultModel: 'claude-opus-4-8',
});
