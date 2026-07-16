import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createSession, SessionStore } from './sessionStore';

test('session survives restart with transcript, plan, and CLI execution contract', async (t) => {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-session-'));
  t.after(() => fs.rm(base, { recursive: true, force: true }));
  const firstProcess = new SessionStore(base, '/workspace/project');
  const session = createSession('codex', 'gpt-5.5');
  session.messages.push(
    { role: 'user', content: 'Make a plan' },
    { role: 'assistant', content: '1. Inspect the code.' },
  );
  session.planPath = '.sema/plans/chat.md';
  session.cliSessionId = 'native-thread';
  session.cliSessionProvider = 'codex';
  session.cliSessionModel = 'gpt-5.5';
  session.cliSessionMode = 'plan';
  session.cliSessionPermission = 'ask';
  firstProcess.save(session);

  const afterRestart = new SessionStore(base, '/workspace/project').load(session.id);
  assert.deepEqual(afterRestart, session);
});

test('one chat transcript can contain turns from different provider models', async (t) => {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), 'sema-session-'));
  t.after(() => fs.rm(base, { recursive: true, force: true }));
  const store = new SessionStore(base, '/workspace/project');
  const session = createSession('openrouter', 'anthropic/claude-opus-4.8');
  session.messages.push(
    { role: 'user', content: 'First question' },
    { role: 'assistant', content: 'Claude answer' },
    { role: 'user', content: 'Now review that answer' },
    { role: 'assistant', content: 'GPT answer' },
  );
  session.provider = 'openai';
  session.model = 'gpt-5.4';
  store.save(session);
  const loaded = store.load(session.id);
  assert.equal(loaded?.messages.length, 4);
  assert.equal(loaded?.provider, 'openai');
  assert.equal(loaded?.model, 'gpt-5.4');
});
