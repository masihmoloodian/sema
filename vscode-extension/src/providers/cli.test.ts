import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { ClaudeCodeProvider, CodexProvider, OpenCodeProvider, isClaudeProtectedTool } from './cli';
import { StreamOptions } from './types';

class InspectClaude extends ClaudeCodeProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}
class InspectCodex extends CodexProvider {
  invocation(opts: StreamOptions) { return this.buildInvocation(opts); }
}
class InspectOpenCode extends OpenCodeProvider {
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
  usage: Array<{ inputTokens: number; outputTokens: number }>;
} {
  const deltas: string[] = [];
  const thinking: string[] = [];
  const activities: string[] = [];
  const sessions: string[] = [];
  const models: string[] = [];
  const usage: Array<{ inputTokens: number; outputTokens: number }> = [];
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
  assert.deepEqual(ask.slice(ask.indexOf('--tools')), ['--tools', '', '--model', 'test-model']);
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
