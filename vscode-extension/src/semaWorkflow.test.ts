import assert from 'node:assert/strict';
import test from 'node:test';
import { buildSystem, SEMA_WORKFLOW } from './semaWorkflow';

test('every Agent and Plan provider prompt gets the sema workflow', () => {
  for (const readsWorkspace of [true, false]) {
    for (const mode of ['plan', 'agent']) {
      const prompt = buildSystem('', readsWorkspace, mode);
      assert.ok(prompt.includes(SEMA_WORKFLOW));
      assert.match(prompt, /search_code/);
      assert.match(prompt, /get_code/);
    }
  }
});

test('index-off Agent and Plan prompts do not advertise sema', () => {
  for (const readsWorkspace of [true, false]) {
    for (const mode of ['plan', 'agent']) {
      const prompt = buildSystem('', readsWorkspace, mode, '', '', false);
      assert.doesNotMatch(prompt, /search_code|get_code|check_reuse|semantic index/);
    }
  }
});

test('Ask mode is simple chat for CLI and API providers', () => {
  for (const readsWorkspace of [true, false]) {
    const prompt = buildSystem('', readsWorkspace, 'ask');
    assert.match(prompt, /simple chat, not an agent run/);
    assert.match(prompt, /Do not inspect or modify the workspace/);
    assert.doesNotMatch(prompt, /start with search_code/);
  }
});

test('CLI plan mode remains read-only and includes retrieved context', () => {
  const prompt = buildSystem('function validateToken()', true, 'plan');
  assert.match(prompt, /Do NOT edit source files/);
  assert.match(prompt, /function validateToken/);
});

test('API agent mode explains the full edit and verification loop', () => {
  const prompt = buildSystem('', false, 'agent');
  assert.match(prompt, /write_file/);
  assert.match(prompt, /run_command/);
  assert.match(prompt, /explore first, then change, then verify/);
});

test('Agent mode receives the latest Plan artifact', () => {
  for (const readsWorkspace of [true, false]) {
    const prompt = buildSystem('', readsWorkspace, 'agent', '- Edit src/auth.ts', '.sema/plans/x.md');
    assert.match(prompt, /Active implementation plan/);
    assert.match(prompt, /.sema\/plans\/x.md/);
    assert.match(prompt, /Edit src\/auth.ts/);
  }
});

test('API ask mode is honest when no index context is available', () => {
  const prompt = buildSystem('', false, 'ask');
  assert.match(prompt, /cannot read the user's files in Ask mode/);
  assert.match(prompt, /turning on the sema index/);
});

test('API ask mode includes retrieved context', () => {
  const prompt = buildSystem('class SessionStore', false, 'ask');
  assert.match(prompt, /initial context/);
  assert.match(prompt, /class SessionStore/);
});
