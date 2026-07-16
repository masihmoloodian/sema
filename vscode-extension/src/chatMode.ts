export type ChatMode = 'ask' | 'plan' | 'agent';

/** Treat unknown persisted/UI values as Ask, the least-capable mode. */
export function normalizeChatMode(value: string | undefined): ChatMode {
  return value === 'plan' || value === 'agent' ? value : 'ask';
}

/** Agentic modes always begin with sema context; Ask only does so when opted in. */
export function shouldPrefetchIndex(useIndex: boolean, mode: ChatMode): boolean {
  return mode !== 'ask' || useIndex;
}

export interface CliResumeState {
  cliSessionId?: string;
  cliSessionProvider?: string;
  cliSessionModel?: string;
  cliSessionMode?: string;
}

/**
 * A native CLI thread is safe to resume only under the execution contract that
 * created it. Some CLIs retain their model and sandbox on resume (Codex does both),
 * so provider-only matching can silently ignore a model or mode switch.
 *
 * The sema transcript is still continuous when this returns undefined: the provider
 * starts a fresh native run and receives the full stored conversation.
 */
export function compatibleCliSession(
  state: CliResumeState,
  provider: string,
  model: string,
  mode: ChatMode,
): string | undefined {
  return state.cliSessionProvider === provider &&
    state.cliSessionModel === model &&
    state.cliSessionMode === mode
    ? state.cliSessionId
    : undefined;
}
