export type ChatMode = 'ask' | 'plan' | 'agent';

/** Fresh workspaces start in Agent; corrupt persisted/UI values still fail safe to Ask. */
export function normalizeChatMode(value: string | undefined): ChatMode {
  if (value === undefined) {
    return 'agent';
  }
  return value === 'ask' || value === 'plan' || value === 'agent' ? value : 'ask';
}

/** The visible index toggle is authoritative in every chat mode. */
export function shouldPrefetchIndex(useIndex: boolean, _mode: ChatMode): boolean {
  return useIndex;
}

export interface CliResumeState {
  cliSessionId?: string;
  cliSessionProvider?: string;
  cliSessionModel?: string;
  cliSessionMode?: string;
  cliSessionPermission?: string;
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
  permission?: string,
): string | undefined {
  return state.cliSessionProvider === provider &&
    state.cliSessionModel === model &&
    state.cliSessionMode === mode &&
    state.cliSessionPermission === permission
    ? state.cliSessionId
    : undefined;
}
