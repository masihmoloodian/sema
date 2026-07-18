import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  ClaudeCodeProvider,
  CodexProvider,
  CursorProvider,
  GrokProvider,
  OpenCodeProvider,
  isClaudeProtectedTool,
  parseSignedIn,
} from './cli';
import { StreamOptions, TokenUsage } from './types';

class InspectClaude extends ClaudeCodeProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}
class InspectCodex extends CodexProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}
class InspectOpenCode extends OpenCodeProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}
class InspectGrok extends GrokProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}
class InspectCursor extends CursorProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}

function baseOptions(): StreamOptions {
  return {
    model: 'test-model',
    system: 'system',
    messages: [{ role: 'user', content: 'hello' }],
    maxTokens: 100,
    signal: new AbortController().signal,
    cwd: '/workspace',
    onDelta: () => {},
  };
}

async function fakeCli(events: unknown[], exitCode = 0): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-provider-'));
  const file = path.join(dir, 'provider.py');
  const source =
    '#!/usr/bin/env python3\n' +
    'import json, sys\n' +
    `events = json.loads(${JSON.stringify(JSON.stringify(events))})\n` +
    'for event in events:\n' +
    '    print(json.dumps(event), flush=True)\n' +
    `sys.exit(${exitCode})\n`;
  await fs.writeFile(file, source, { mode: 0o755 });
  return file;
}

function options(cliBin: string): {
  opts: StreamOptions;
  deltas: string[];
  thinking: string[];
  activities: string[];
  sessions: string[];
  models: string[];
  usage: TokenUsage[];
} {
  const deltas: string[] = [];
  const thinking: string[] = [];
  const activities: string[] = [];
  const sessions: string[] = [];
  const models: string[] = [];
  const usage: TokenUsage[] = [];
  return {
    opts: {
      model: 'default',
      system: 'Use sema search_code first.',
      messages: [{ role: 'user', content: 'Where is auth handled?' }],
      maxTokens: 100,
      signal: new AbortController().signal,
      cliBin,
      cwd: process.cwd(),
      onDelta: (text) => deltas.push(text),
      onThinking: (text) => thinking.push(text),
      onActivity: (tool, detail) => activities.push(`${tool}:${detail}`),
      onSession: (id) => sessions.push(id),
      onModel: (model) => models.push(model),
      onUsage: (value) => usage.push(value),
    },
    deltas,
    thinking,
    activities,
    sessions,
    models,
    usage,
  };
}

test('Claude Code JSON stream drives every chat callback', async () => {
  const cli = await fakeCli([
    { type: 'system', model: 'claude-test', session_id: 'claude-session' },
    { type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'thinking_delta', thinking: 'inspect' } } },
    { type: 'stream_event', event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'answer' } } },
    { type: 'assistant', message: { content: [{ type: 'tool_use', id: 'tool-1', name: 'Read', input: { file_path: 'auth.ts' } }] } },
    { type: 'result', usage: { input_tokens: 10, output_tokens: 4 }, total_cost_usd: 0.01 },
  ]);
  const state = options(cli);
  await new ClaudeCodeProvider().stream(state.opts);
  assert.deepEqual(state.deltas, ['answer']);
  assert.deepEqual(state.thinking, ['inspect']);
  assert.deepEqual(state.activities, ['Read:auth.ts']);
  assert.deepEqual(state.sessions, ['claude-session']);
  assert.deepEqual(state.models, ['claude-test']);
  assert.deepEqual(state.usage, [{ inputTokens: 10, outputTokens: 4, cachedInputTokens: 0, costUsd: 0.01 }]);
});

test('Codex JSON stream reports answer, reasoning, tools, session, and usage', async () => {
  const cli = await fakeCli([
    { type: 'thread.started', thread_id: 'codex-session' },
    { type: 'item.completed', item: { type: 'reasoning', text: 'inspect' } },
    { type: 'item.completed', item: { id: 'cmd-1', type: 'command_execution', command: 'rg auth' } },
    { type: 'item.completed', item: { type: 'agent_message', text: 'answer' } },
    { type: 'turn.completed', usage: { input_tokens: 12, cached_input_tokens: 3, output_tokens: 5, reasoning_output_tokens: 2 } },
  ]);
  const state = options(cli);
  // Pin a model so the test does not scan the real Codex session directory.
  state.opts.model = 'gpt-5.5';
  await new CodexProvider().stream({ ...state.opts, onModel: undefined });
  assert.deepEqual(state.deltas, ['answer']);
  assert.deepEqual(state.thinking, ['inspect']);
  assert.deepEqual(state.activities, ['Run:rg auth']);
  assert.deepEqual(state.sessions, ['codex-session']);
  assert.deepEqual(state.usage, [{ inputTokens: 12, outputTokens: 7, cachedInputTokens: 3 }]);
});

test('opencode JSON stream reports answer, reasoning, tools, session, and usage', async () => {
  const cli = await fakeCli([
    { type: 'reasoning', sessionID: 'open-session', part: { text: 'inspect' } },
    { type: 'tool_use', part: { id: 'tool-1', tool: 'read', state: { input: { filePath: 'auth.ts' } } } },
    { type: 'text', part: { text: 'answer' } },
    { type: 'step_finish', part: { cost: 0.02, tokens: { input: 9, output: 4, reasoning: 1, cache: { read: 2, write: 1 } } } },
  ]);
  const state = options(cli);
  await new OpenCodeProvider().stream({ ...state.opts, onModel: undefined });
  assert.deepEqual(state.deltas, ['answer']);
  assert.deepEqual(state.thinking, ['inspect']);
  assert.deepEqual(state.activities, ['read:auth.ts']);
  assert.deepEqual(state.sessions, ['open-session']);
  assert.deepEqual(state.usage, [{ inputTokens: 12, outputTokens: 5, cachedInputTokens: 2, costUsd: 0.02 }]);
});

// Verbatim from `grok --output-format streaming-json ... -p "Reply with exactly: ok"`
// against grok 0.2.102 on a signed-in account. Note what real output settles:
//   * total_tokens (4947) == input_tokens + cache_read_input_tokens + output_tokens,
//     so reasoning_tokens is already inside output_tokens and must not be added again.
//   * no total_cost_usd at all — OAuth/pool traffic goes uncosted.
//   * modelUsage is keyed by the billed id, which is not the id that was requested.
const GROK_LIVE_END = {
  type: 'end',
  stopReason: 'EndTurn',
  sessionId: '019f6f62-ec91-77d0-a586-44188d1e9e1e',
  requestId: 'de171208-08b7-4f4b-81cf-a998a52a0ced',
  usage: {
    input_tokens: 4925,
    cache_read_input_tokens: 0,
    output_tokens: 22,
    reasoning_tokens: 21,
    total_tokens: 4947,
  },
  num_turns: 1,
  modelUsage: {
    'grok-4.5-build-free': {
      inputTokens: 4925, outputTokens: 22, cacheReadInputTokens: 0, modelCalls: 1,
    },
  },
};

test('Grok streaming-json reports answer, reasoning, session, model, and usage', async () => {
  const cli = await fakeCli([
    { type: 'thought', data: 'The' },
    { type: 'thought', data: ' user' },
    { type: 'text', data: 'ok' },
    GROK_LIVE_END,
  ]);
  const state = options(cli);
  await new GrokProvider().stream(state.opts);
  assert.deepEqual(state.deltas, ['ok']);
  assert.deepEqual(state.thinking, ['The', ' user']);
  assert.deepEqual(state.activities, []);  // grok's stream carries no tool events
  assert.deepEqual(state.sessions, ['019f6f62-ec91-77d0-a586-44188d1e9e1e']);
  // Resolved from modelUsage — the requested id was grok-4.5, the billed one is not.
  assert.deepEqual(state.models, ['grok-4.5-build-free']);
  assert.deepEqual(state.usage, [
    // 4925 + 0 cache read; 22 out (NOT 22 + 21 reasoning); no cost reported.
    { inputTokens: 4925, outputTokens: 22, cachedInputTokens: 0, costUsd: undefined },
  ]);
});

test('Grok reports no cost when the CLI omits it rather than implying free', async () => {
  const cli = await fakeCli([{ type: 'text', data: 'answer' }, GROK_LIVE_END]);
  const state = options(cli);
  await new GrokProvider().stream({ ...state.opts, onModel: undefined });
  assert.equal(state.usage[0].costUsd, undefined);
});

test('Grok stays silent on the model when subagents make modelUsage ambiguous', async () => {
  const cli = await fakeCli([
    { type: 'text', data: 'answer' },
    {
      ...GROK_LIVE_END,
      modelUsage: { 'grok-4.5-build-free': { modelCalls: 1 }, 'grok-4.20-multi-agent': { modelCalls: 2 } },
    },
  ]);
  const state = options(cli);
  await new GrokProvider().stream(state.opts);
  // No marker says which entry is the main model, so guessing would mislabel the turn.
  assert.deepEqual(state.models, []);
});

test('Grok surfaces a streamed error object', async () => {
  const cli = await fakeCli([{ type: 'error', message: "Couldn't start session: not signed in" }]);
  const state = options(cli);
  await assert.rejects(
    new GrokProvider().stream({ ...state.opts, onModel: undefined }),
    /not signed in/,
  );
});

test('Grok passes the prompt to -p and gates tools by mode', () => {
  const grok = new InspectGrok();

  const ask = grok.invocation({ ...baseOptions(), model: 'default' });
  assert.equal(ask.bin, 'grok');
  assert.deepEqual(ask.args.slice(0, 2), ['--output-format', 'streaming-json']);
  // The base class appends the prompt positional, so `-p` must be the final flag.
  assert.equal(ask.args[ask.args.length - 1], '-p');
  assert.ok(ask.args.includes('--tools'), 'Ask mode must stay read-only');
  assert.ok(!ask.args.includes('--yolo'));
  assert.ok(ask.prompt.includes('hello'));

  const agent = grok.invocation({ ...baseOptions(), agent: true, model: 'grok-4.5', effort: 'high' });
  assert.ok(agent.args.includes('--yolo'), 'Agent mode must auto-approve or it hangs headless');
  assert.ok(!agent.args.includes('--tools'));
  assert.deepEqual(agent.args.slice(agent.args.indexOf('-m'), agent.args.indexOf('-m') + 2), ['-m', 'grok-4.5']);
  assert.deepEqual(
    agent.args.slice(agent.args.indexOf('--effort'), agent.args.indexOf('--effort') + 2),
    ['--effort', 'high'],
  );

  // 'default' effort/model means "pass no flag and let grok decide".
  assert.ok(!ask.args.includes('--effort'));
  assert.ok(!ask.args.includes('-m'));

  // Resume sends only the newest turn; the grok session holds the rest.
  const resumed = grok.invocation({
    ...baseOptions(),
    sessionId: 'sess-1',
    messages: [
      { role: 'user', content: 'first' },
      { role: 'assistant', content: 'reply' },
      { role: 'user', content: 'second' },
    ],
  });
  assert.deepEqual(resumed.args.slice(resumed.args.indexOf('-r'), resumed.args.indexOf('-r') + 2), ['-r', 'sess-1']);
  assert.equal(resumed.prompt, 'second');
});

test('Cursor stream-json reports answer, tools, session, and model', async () => {
  const cli = await fakeCli([
    { type: 'system', subtype: 'init', session_id: 'cursor-session', model: 'gpt-5', cwd: '/workspace', permissionMode: 'default' },
    { type: 'user', message: { role: 'user', content: [{ type: 'text', text: 'Where is auth handled?' }] }, session_id: 'cursor-session' },
    { type: 'tool_call', subtype: 'started', call_id: 'call-1', tool_call: { readToolCall: { args: { path: 'auth.ts' } } }, session_id: 'cursor-session' },
    { type: 'tool_call', subtype: 'completed', call_id: 'call-1', tool_call: { readToolCall: { args: { path: 'auth.ts' }, result: { success: {} } } }, session_id: 'cursor-session' },
    { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'answer' }] }, session_id: 'cursor-session' },
    { type: 'result', subtype: 'success', is_error: false, result: 'answer', session_id: 'cursor-session' },
  ]);
  const state = options(cli);
  await new CursorProvider().stream(state.opts);
  // The terminal `result` repeats the full answer; it must not be counted a second time.
  assert.deepEqual(state.deltas, ['answer']);
  assert.deepEqual(state.thinking, []); // reasoning is suppressed in print mode
  assert.deepEqual(state.activities, ['Read:auth.ts']); // `completed` reuses call-1 and is skipped
  assert.deepEqual(state.sessions, ['cursor-session']);
  assert.deepEqual(state.models, ['gpt-5']);
  assert.deepEqual(state.usage, []); // cursor-agent reports no token usage or cost
});

test('Cursor surfaces a failed result', async () => {
  const cli = await fakeCli([
    { type: 'system', subtype: 'init', session_id: 's', model: 'auto' },
    { type: 'result', subtype: 'error', is_error: true, result: 'Not authenticated. Run cursor-agent login.', session_id: 's' },
  ]);
  const state = options(cli);
  await assert.rejects(
    new CursorProvider().stream({ ...state.opts, onModel: undefined }),
    /Not authenticated/,
  );
});

test('Cursor passes the prompt as the positional and gates edits by mode', () => {
  const provider = new InspectCursor();
  const base = { ...baseOptions(), model: 'auto', cwd: '/workspace' };

  // Ask/Plan (read-only): no --force, context inlined into the prompt.
  const ask = provider.invocation({ ...base, agent: false });
  assert.equal(ask.bin, 'cursor-agent');
  assert.deepEqual(ask.args, ['-p', '--output-format', 'stream-json', '--workspace', '/workspace', '--model', 'auto']);
  assert.equal(ask.prompt, 'system\n\n--- End of context. Reply to the request below. ---\n\nhello');

  // Agent mode auto-approves edits.
  assert.ok(provider.invocation({ ...base, agent: true }).args.includes('--force'));

  // Resume sends only the newest user turn and passes the id explicitly.
  const resume = provider.invocation({
    ...base,
    sessionId: 'sess-1',
    messages: [
      { role: 'user', content: 'a' },
      { role: 'assistant', content: 'b' },
      { role: 'user', content: 'c' },
    ],
  });
  assert.equal(resume.args[resume.args.indexOf('--resume') + 1], 'sess-1');
  assert.equal(resume.prompt, 'c');
});

test('sign-in state is read from each CLI real output', () => {
  // Claude Code — JSON.
  assert.equal(parseSignedIn('{"loggedIn":true}'), true);
  assert.equal(parseSignedIn('{"loggedIn":false}'), false);

  // Codex — prose.
  assert.equal(parseSignedIn('Not logged in'), false);
  assert.equal(parseSignedIn('Logged in using ChatGPT'), true);

  // Grok — `grok models` exits 0 either way, so the prose is the only signal. Both
  // strings are verbatim from grok 0.2.102, captured signed out and signed in.
  assert.equal(
    parseSignedIn('You are not authenticated.\n\nDefault model: grok-build\n\nAvailable models:'),
    false,
  );
  assert.equal(
    parseSignedIn(
      'You are logged in with grok.com.\n\nDefault model: grok-4.5\n\nAvailable models:\n  * grok-4.5 (default)',
    ),
    true,
  );

  // A model list that merely mentions the word must not read as signed out.
  assert.equal(parseSignedIn('Available models:\n  auth-helper-v2'), true);
});

test('CLIs with a system channel keep the user turn free of sema context', () => {
  const sys = 'SEMA CONTEXT: use search_code first.';
  const opts = { ...baseOptions(), system: sys, messages: [{ role: 'user' as const, content: 'hi' }] };

  // Claude Code — --append-system-prompt, matching what its Agent SDK path already does.
  const claude = new InspectClaude().invocation(opts);
  assert.deepEqual(
    claude.args.slice(claude.args.indexOf('--append-system-prompt'), claude.args.indexOf('--append-system-prompt') + 2),
    ['--append-system-prompt', sys],
  );
  assert.equal(claude.prompt, 'hi', 'the user turn must be exactly what they typed');

  // Grok — --rules appends to the system prompt (verified against grok 0.2.102).
  const grok = new InspectGrok().invocation(opts);
  assert.deepEqual(
    grok.args.slice(grok.args.indexOf('--rules'), grok.args.indexOf('--rules') + 2),
    ['--rules', sys],
  );
  assert.equal(grok.prompt, 'hi');

  // No system to send → no empty flag.
  const bare = new InspectGrok().invocation({ ...opts, system: '' });
  assert.ok(!bare.args.includes('--rules'));
  assert.ok(!new InspectClaude().invocation({ ...opts, system: '' }).args.includes('--append-system-prompt'));
});

test('CLIs without a system channel inline it, but fence off the user request', () => {
  const sys = 'SEMA CONTEXT: use search_code first.';
  const opts = { ...baseOptions(), system: sys, messages: [{ role: 'user' as const, content: 'hi' }] };

  // Codex's positional IS its instructions and `opencode run` has no system flag, so
  // inlining is forced here — not an oversight. The fence keeps the request legible.
  for (const invocation of [new InspectCodex().invocation(opts), new InspectOpenCode().invocation(opts)]) {
    assert.ok(invocation.prompt.startsWith(sys), 'context leads');
    assert.ok(invocation.prompt.includes('End of context'), 'boundary is marked');
    assert.ok(invocation.prompt.trimEnd().endsWith('hi'), 'user request comes last');
  }
});

test('provider stream surfaces structured model errors', async () => {
  const cli = await fakeCli([
    { type: 'result', is_error: true, result: 'not authenticated' },
  ]);
  const state = options(cli);
  await assert.rejects(new ClaudeCodeProvider().stream(state.opts), /not authenticated/);
});

test('Claude Code maps Ask, Plan, and Agent to distinct capabilities', () => {
  const provider = new InspectClaude();
  const ask = provider.invocation(baseOptions()).args;
  const plan = provider.invocation({ ...baseOptions(), plan: true }).args;
  const agent = provider.invocation({
    ...baseOptions(),
    agent: true,
    permissionMode: 'bypass',
  }).args;
  // baseOptions() carries system: 'system', which now rides --append-system-prompt
  // rather than being inlined into the user's turn.
  assert.deepEqual(
    ask.slice(ask.indexOf('--tools')),
    ['--tools', '', '--model', 'test-model', '--append-system-prompt', 'system'],
  );
  assert.ok(plan.includes('plan'));
  assert.ok(!plan.includes('--dangerously-skip-permissions'));
  assert.ok(agent.includes('--dangerously-skip-permissions'));
  assert.deepEqual(provider.permissionModes, ['ask', 'bypass']);
});

test('Claude approval gate covers every mutating built-in tool but not read-only navigation', () => {
  for (const tool of ['Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'Bash']) {
    assert.equal(isClaudeProtectedTool(tool), true, tool);
  }
  for (const tool of ['Read', 'Grep', 'Glob', 'WebSearch']) {
    assert.equal(isClaudeProtectedTool(tool), false, tool);
  }
});

test('Codex keeps Plan/Ask read-only and maps Agent bypass to full access', () => {
  const provider = new InspectCodex();
  const ask = provider.invocation(baseOptions()).args;
  const plan = provider.invocation({ ...baseOptions(), plan: true }).args;
  const agent = provider.invocation({
    ...baseOptions(),
    agent: true,
    permissionMode: 'bypass',
  }).args;
  assert.equal(ask[ask.indexOf('--sandbox') + 1], 'read-only');
  assert.equal(plan[plan.indexOf('--sandbox') + 1], 'read-only');
  assert.ok(!agent.includes('--sandbox'));
  assert.ok(agent.includes('--dangerously-bypass-approvals-and-sandbox'));
  assert.ok(agent.includes('test-model'));
  assert.deepEqual(provider.permissionModes, ['ask', 'bypass']);
});

async function fakeCodexAppServer(): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-codex-app-server-'));
  const file = path.join(dir, 'codex.py');
  const source = `#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get('method')
    if method == 'initialize':
        print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)
    elif method == 'thread/start':
        print(json.dumps({'id': msg['id'], 'result': {'thread': {'id': 'app-thread'}, 'model': 'gpt-test'}}), flush=True)
    elif method == 'turn/start':
        print(json.dumps({'id': msg['id'], 'result': {'turn': {'id': 'turn-1'}}}), flush=True)
        print(json.dumps({'id': 900, 'method': 'item/commandExecution/requestApproval', 'params': {'command': 'touch approved.txt', 'cwd': '/workspace', 'reason': 'Create the requested file'}}), flush=True)
    elif msg.get('id') == 900:
        accepted = msg.get('result', {}).get('decision') == 'accept'
        text = 'approved' if accepted else 'denied'
        print(json.dumps({'method': 'item/agentMessage/delta', 'params': {'delta': text}}), flush=True)
        print(json.dumps({'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {'last': {'inputTokens': 8, 'cachedInputTokens': 2, 'outputTokens': 3, 'reasoningOutputTokens': 1}}}}), flush=True)
        print(json.dumps({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}), flush=True)
`;
  await fs.writeFile(file, source, { mode: 0o755 });
  return file;
}

test('Codex app-server pauses protected actions for extension approval', async () => {
  const cli = await fakeCodexAppServer();
  const state = options(cli);
  const approvals: string[] = [];
  state.opts.agent = true;
  state.opts.permissionMode = 'ask';
  state.opts.onPermissionRequest = async (request) => {
    approvals.push(`${request.provider}:${request.tool}:${request.detail}`);
    return 'allow';
  };
  await new CodexProvider().stream(state.opts);
  assert.deepEqual(state.deltas, ['approved']);
  assert.equal(state.sessions[0], 'app-thread');
  assert.equal(state.models[0], 'gpt-test');
  assert.match(approvals[0], /codex:touch approved\.txt:.*Create the requested file/s);
  assert.deepEqual(state.usage, [{ inputTokens: 8, outputTokens: 4, cachedInputTokens: 2 }]);
});

test('opencode uses plan agent for Ask/Plan and build agent only for Agent', () => {
  const provider = new InspectOpenCode();
  const ask = provider.invocation(baseOptions()).args;
  const plan = provider.invocation({ ...baseOptions(), plan: true }).args;
  const agent = provider.invocation({ ...baseOptions(), agent: true }).args;
  assert.equal(ask[ask.indexOf('--agent') + 1], 'plan');
  assert.equal(plan[plan.indexOf('--agent') + 1], 'plan');
  assert.equal(agent[agent.indexOf('--agent') + 1], 'build');
  assert.equal(agent[agent.indexOf('--model') + 1], 'test-model');
});
