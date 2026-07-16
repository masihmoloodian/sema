import assert from 'node:assert/strict';
import test from 'node:test';
import { compatibleCliSession, normalizeChatMode, shouldPrefetchIndex } from './chatMode';

const state = {
  cliSessionId: 'native-1',
  cliSessionProvider: 'codex',
  cliSessionModel: 'gpt-5.5',
  cliSessionMode: 'agent',
  cliSessionPermission: 'ask',
};

test('CLI resume requires the same provider, model, mode, and permission contract', () => {
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'agent', 'ask'), 'native-1');
  assert.equal(compatibleCliSession(state, 'claude-code', 'gpt-5.5', 'agent', 'ask'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.4', 'agent', 'ask'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'plan'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'ask'), undefined);
  assert.equal(compatibleCliSession(state, 'codex', 'gpt-5.5', 'agent', 'bypass'), undefined);
});

test('legacy sessions without model/mode restart safely with full transcript', () => {
  assert.equal(
    compatibleCliSession(
      { cliSessionId: 'old', cliSessionProvider: 'codex' },
      'codex',
      'gpt-5.5',
      'agent',
      'ask',
    ),
    undefined,
  );
});

test('fresh workspaces default to Agent while unknown mode values fall back to Ask', () => {
  assert.equal(normalizeChatMode('agent'), 'agent');
  assert.equal(normalizeChatMode('plan'), 'plan');
  assert.equal(normalizeChatMode('something-else'), 'ask');
  assert.equal(normalizeChatMode(undefined), 'agent');
});

test('index toggle controls sema prefetch in every mode', () => {
  assert.equal(shouldPrefetchIndex(false, 'agent'), false);
  assert.equal(shouldPrefetchIndex(false, 'plan'), false);
  assert.equal(shouldPrefetchIndex(false, 'ask'), false);
  assert.equal(shouldPrefetchIndex(true, 'agent'), true);
  assert.equal(shouldPrefetchIndex(true, 'plan'), true);
  assert.equal(shouldPrefetchIndex(true, 'ask'), true);
});
