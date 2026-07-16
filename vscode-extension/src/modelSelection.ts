import { ChatProvider } from './providers/types';

export interface EffortCapabilities {
  efforts: readonly string[];
  byModel: Readonly<Record<string, readonly string[]>>;
}

const DEFAULT_EFFORT = 'default';

function uniqueEfforts(values: string[]): string[] {
  return [DEFAULT_EFFORT, ...values.filter((value, index) =>
    value !== DEFAULT_EFFORT && values.indexOf(value) === index,
  )];
}

/** Parse the exact named levels printed by the installed Claude Code CLI. */
export function parseClaudeEfforts(help: string): EffortCapabilities | undefined {
  const option = /--effort <level>[\s\S]{0,240}?\(([^)]+)\)/.exec(help);
  if (!option) {
    return undefined;
  }
  const levels = option[1]
    .split(',')
    .map((level) => level.trim())
    .filter(Boolean);
  return levels.length ? { efforts: uniqueEfforts(levels), byModel: {} } : undefined;
}

/** Parse model-specific effort levels from `codex debug models`. */
export function parseCodexEfforts(catalog: string): EffortCapabilities | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(catalog);
  } catch {
    return undefined;
  }
  const models = (parsed as {
    models?: Array<{
      slug?: unknown;
      supported_reasoning_levels?: Array<{ effort?: unknown }>;
    }>;
  }).models;
  if (!Array.isArray(models)) {
    return undefined;
  }
  const byModel: Record<string, readonly string[]> = {};
  const union: string[] = [];
  for (const model of models) {
    if (typeof model.slug !== 'string' || !Array.isArray(model.supported_reasoning_levels)) {
      continue;
    }
    const levels = model.supported_reasoning_levels
      .map((level) => level.effort)
      .filter((level): level is string => typeof level === 'string' && !!level);
    if (!levels.length) {
      continue;
    }
    byModel[model.slug] = uniqueEfforts(levels);
    union.push(...levels);
  }
  return Object.keys(byModel).length
    ? { efforts: uniqueEfforts(union), byModel }
    : undefined;
}

/** Safe values accepted by Codex versions that predate `debug models`. */
export const LEGACY_CODEX_EFFORTS = ['default', 'minimal', 'low', 'medium', 'high'] as const;

/** Return only reasoning levels accepted by the selected model. */
export function effortsForModel(provider: ChatProvider, model: string): readonly string[] {
  return provider.modelInfos.find((info) => info.id === model)?.efforts ?? provider.efforts ?? [];
}
