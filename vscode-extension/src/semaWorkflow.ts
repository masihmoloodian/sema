/** Shared sema workflow injected into every chat provider. */
export const SEMA_WORKFLOW =
  "This project uses sema's semantic code index. For codebase tasks, use sema to narrow " +
  'context before broad file reads: your first navigation tool call must be search_code using a ' +
  'natural-language query; do not delegate to a subagent or use Bash, grep, glob, find, or direct ' +
  'file reads before that first sema search. ' +
  'if a result is plausibly relevant, inspect it with get_code instead of repeatedly reformulating ' +
  'the search. Make at most two targeted searches before falling back, and do not call repo_map ' +
  'merely because a targeted result is imperfect. Before adding a helper, ' +
  'class, function, or utility, use check_reuse when that tool is available. Before changing a ' +
  'known symbol, use impact_analysis or find_usages when available. Use repo_map for architecture ' +
  'and explain_file for a compact file summary when available. If sema returns no useful result ' +
  'or is unavailable, fall back to normal workspace navigation. Never claim code exists without ' +
  'supporting tool output or a direct file read.';

export const PLAN_NOTE =
  'You are in plan mode. Investigate the problem and produce a concise, step-by-step ' +
  'implementation plan — the files to change, the approach, and the order of the steps. Do ' +
  'NOT edit source files or write the full implementation; output Markdown plan content only, ' +
  'then stop. sema will save that answer as the session plan artifact.';

export const ASK_NOTE =
  'You are in Ask mode: this is simple chat, not an agent run. Answer directly from the ' +
  'conversation and supplied context. Do not inspect or modify the workspace, execute commands, ' +
  'or invoke tools. If the answer requires live repository investigation, tell the user to switch ' +
  'to Plan or Agent mode.';

/** Build the system prompt used by all local-CLI and API-backed providers. */
export function buildSystem(
  context: string,
  readsWorkspace: boolean,
  mode: string,
  activePlan = '',
  activePlanPath = '',
): string {
  const plan = mode === 'plan';
  // CLI agents run under their own coding persona and can inspect the repository. Keep the
  // prompt focused on the portable sema workflow, plan constraint, and retrieved context.
  if (readsWorkspace) {
    if (mode === 'ask') {
      const parts = [ASK_NOTE];
      if (context) {
        parts.push('', 'Use the retrieved code below as context:', '', context);
      }
      return parts.join('\n').trim();
    }
    const parts: string[] = [SEMA_WORKFLOW];
    if (plan) {
      parts.push('', PLAN_NOTE);
    }
    if (context) {
      parts.push(
        '',
        "Relevant code from sema's semantic index (a starting point — read more files if needed):",
        '',
        context,
      );
    }
    if (mode === 'agent' && activePlan) {
      parts.push('', `Active implementation plan (${activePlanPath || 'session plan'}):`, '', activePlan);
    }
    return parts.join('\n').trim();
  }

  // API providers are bare models. In Agent/Plan mode sema gives them workspace tools and
  // runs the tool loop. Ask mode uses retrieved code as RAG context.
  const agent = mode === 'agent';
  let lines: string[];
  if (agent) {
    lines = [
      "You are a coding agent working directly in the user's workspace through tools. Available " +
        'tools: search_code (semantic search of the codebase — usually the best first step), ' +
        "get_code (fetch a symbol's full source), check_reuse (avoid duplicating existing code), " +
        'grep (regex text search), glob (find files by ' +
        'pattern), list_directory, read_file, write_file, edit_file (surgical string replacement — ' +
        'prefer it over rewriting whole files), delete_file, and run_command (shell: builds, tests, ' +
        'git, scaffolding). When the request is a task that inspects or changes the project, use ' +
        'tools to actually do it — explore first, then change, then verify. For greetings, small ' +
        'talk, or questions that do not require the workspace, reply directly without tools.',
    ];
  } else if (plan) {
    lines = [
      'You are a coding assistant in plan mode. You have read-only tools — search_code, get_code, ' +
        'check_reuse, grep, glob, list_directory, read_file — to investigate the codebase before planning; use ' +
        'them to ground your plan in the actual code. For a greeting or a message that is not a ' +
        'task to plan, reply briefly without using any tool. ' +
        PLAN_NOTE,
    ];
  } else {
    lines = [ASK_NOTE];
  }
  if (mode !== 'ask') {
    lines.push('', SEMA_WORKFLOW);
  }
  if (context) {
    lines.push('', 'Use the retrieved code below as initial context:', '', context);
  } else if (mode === 'ask') {
    lines.push(
      '',
      "You cannot read the user's files in Ask mode. Answer from the conversation; if their code is",
      'needed, suggest turning on the sema index or switching to Agent/Plan mode.',
    );
  }
  if (agent && activePlan) {
    lines.push('', `Active implementation plan (${activePlanPath || 'session plan'}):`, '', activePlan);
  }
  return lines.join('\n');
}
